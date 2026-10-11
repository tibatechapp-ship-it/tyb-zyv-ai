# Learning

`src/learning/` is the agent team's learning loop: it watches what agents do
and turns those experiences into behaviour improvements. Phase 1 + 1.5 are the
data layer; later phases add the learning itself.

```
src/learning/
├── __init__.py             # Experience, Outcome, ExperienceCollector, Lesson, LessonExtractor, LessonStore, LessonInjector, LessonDatabase, LearningOrchestrator, KNOWN_*
├── experience_collector.py # the in-memory buffer, queries, stats, IO, pruning
├── lesson.py               # Lesson + LessonExtractor (Phase 2)
├── lesson_store.py         # LessonStore + LessonInjector + render_lessons + LessonDatabase (Phase 3/4)
└── orchestrator.py         # LearningOrchestrator + ExtractionReport (Phase 4)
```

## Experience — one recorded action

An `Experience` captures *who did what and how it ended*:

| Field | Meaning |
|-------|---------|
| `task_id` / `step_id` | which run and step produced it (non-empty, enforced) |
| `agent_role` | canonical role — must be in `KNOWN_AGENT_ROLES` |
| `action_type` | canonical action — must be in `KNOWN_ACTION_TYPES` |
| `outcome` | `Outcome.SUCCESS` / `FAILURE` / `PARTIAL` |
| `action` / `result` | free-text detail |
| `ts` | clock time of the record |

`Outcome` is a `StrEnum`, so `to_dict()` serialises straight to JSON, and
`Experience.from_dict()` rebuilds it (raising `ValueError` on an unknown
outcome).

## Quick start

```python
from src.learning import ExperienceCollector, Outcome

collector = ExperienceCollector()          # clock=time.time by default

collector.record(
    "task-42", "step-1", "coder", "write_file",
    Outcome.FAILURE, action="night build", result="exit 1",
)
```

### Fail-safe recording

`record()` validates everything before touching the buffer and raises
`ValueError` (plus a `logging` warning) when:

* `task_id` / `step_id` is blank;
* `agent_role` is not in `KNOWN_AGENT_ROLES` (20 roles: architect, coder,
  reviewer, tester, debugger, planner, analyst, researcher, designer,
  estimator, security_auditor, performance, documentation, refactor,
  compliance, orchestrator, devops, monitor, recovery, reporter);
* `action_type` is not in `KNOWN_ACTION_TYPES` (10 actions: read_file,
  write_file, edit_file, list_files, search_files, run_command, web_search,
  web_fetch, delegate_task, write_todos).

Only the canonical lowercase forms are accepted.

## Statistics

```python
collector.stats()
# {
#   "total": 100,
#   "success_rate": 0.82, "failure_rate": 0.12,     # floats in [0, 1]
#   "by_agent":  {"coder": 40, "planner": 30, ...}, # Counter.most_common() desc
#   "by_action": {"write_file": 50, "read_file": 30, ...},
#   "by_outcome": {"success": 82, "failure": 12, "partial": 6},
#   "time_range": {"first": ..., "last": ..., "span_seconds": ...},
# }
```

## Queries — all newest first

```python
collector.get_by_agent("coder")                 # one agent's latest
collector.get_by_action("write_file")           # one action type's latest
collector.get_by_outcome(Outcome.FAILURE)       # failures only
collector.search(lambda e: "build" in e.action) # anything else you want
```

Every query takes `limit=50` by default and returns a snapshot list.

## Export / import

```python
collector.export("exp.json")            # JSON list   -> int (written)
collector.import_from("exp.json")       # -> int (added); invalid items skipped
collector.export_to_jsonl("exp.jsonl")  # JSON Lines  -> int (written)
collector.import_from_jsonl("exp.jsonl")# -> int (added); blank/broken lines skipped
```

Malformed items are logged with `logging.warning` and skipped, never fatal.
Missing files raise `FileNotFoundError`.

## Retention

```python
collector.prune(max_age_days=30, max_count=10_000)   # -> int removed
collector.prune_by_outcome(Outcome.FAILURE, max_count=1000)  # keep newest N of a kind
```

## Why in-memory?

The collector is the *working* buffer for one process — cheap and fast. The
long-lived record of episodes already lives in `src.memory` (the episodic
store), so nothing here needs a database.

## Lessons (Phase 2) — principles from experiences

A `Lesson` is a reusable principle distilled from one or more experiences, e.g.
*"When deploying at night, always run the test suite first because failures cost
a whole release"*.

```python
from src.core.backend import EchoBackend
from src.learning import ExperienceCollector, LessonExtractor

collector = ExperienceCollector()
collector.record("task-42", "step-1", "coder", "write_file",
                 Outcome.FAILURE, action="night deploy", result="exit 1")

extractor = LessonExtractor(backend, collector)   # any LLMBackend
extractor.extract_from_failure(exp)   # -> Lesson | None
extractor.extract_from_success(exp)   # -> Lesson | None
extractor.extract_batch(experiences)  # -> list[Lesson] (merged/deduped)
extractor.extract_from_task("task-42")  # -> list[Lesson] (oldest-first)
```

The extractor asks the backend for **one** principle as JSON
(`{"principle", "context", "confidence", "tags"}`), using the prompt format
*"When [context], always/never [action] because [reason]"*. It is deliberately
**tolerant**:

* a reply that is not valid JSON, a blank principle, a fenced ```json block, or
  a backend error → `None` (one bad call never kills a batch);
* `confidence` is clamped to `[0, 1]`; missing fields default sanely.

### Lesson fields

| Field | Meaning |
|-------|---------|
| `id` | UUID (fresh, incl. for merged lessons) |
| `principle` | the general rule |
| `context` | when it applies |
| `confidence` | 0.0–1.0 (see below) |
| `evidence` | `("task_id:step_id", ...)` the lesson rests on |
| `source_outcome` | `Outcome.SUCCESS` / `FAILURE` (dominant when merged) |
| `created_at` | clock time |
| `agent_role` | the source agent (dominant when merged) |
| `tags` | `frozenset[str]` for classification |

`Lesson.to_dict()` / `Lesson.from_dict()` round-trip it (JSON-safe).

### Confidence

The model's estimate is adjusted deterministically so equal inputs score equal:
`confidence = base + min(0.2, (evidence−1)·0.05) − (0.25 if contradictory)`.
Repetition (more supporting experiences) raises it, capped at +0.2;
contradiction (the same principle seen with **both** success *and* failure)
lowers it.

### Deduplication

`extract_batch` merges lessons whose principles are similar — identical token
sets or token **Jaccard ≥ 0.6**. A merged lesson keeps the first principle, the
union of evidence and tags, the most common agent/outcome, the latest
`created_at`, and re-scores confidence on the merged evidence.

## Roadmap

- **Phase 1 (done)** — collector core: record / all / len / clear.
- **Phase 1.5 (done)** — queries, statistics, JSON + JSONL export/import,
  retention pruning, fail-safe `record` with KNOWN_* validators.
- **Phase 2 (done)** — `LessonExtractor`: LLM-backed principle extraction
  (`extract_from_failure` / `extract_from_success` / `extract_batch` /
  `extract_from_task`), tolerant JSON parsing, confidence scoring,
  similarity-based deduplication.
- **Phase 3 (done)** — `LessonStore` + `LessonInjector`: rank the stored lessons
  against a task and fold the relevant ones into an agent's prompt, closing the
  learn → apply loop (see below).
- **Phase 4 (done)** — `LearningOrchestrator` runs the whole pipeline in one
  call and drops weak lessons; `LessonDatabase` persists lessons to SQLite; the
  TUI gains a **Lessons** view.

## Using lessons (Phase 3) — closing the loop

Phase 2 produces lessons; Phase 3 *spends* them. A `LessonStore` holds the
lessons, ranks the relevant ones for a task, and renders them into a prompt
block that any agent will accept.

```python
from src.learning import LessonStore, LessonInjector, render_lessons

store = LessonStore()
store.add_many(extractor.extract_from_task("task-42"))   # from Phase 2

# get the lessons relevant to this task (token overlap + agent-role bonus)
block = store.build_context("deploy the auth service", agent_role="coder")
# -> "- (0.80) When deploying at night, always run the test suite first — ..."

# the agent folds it into the USER turn (never the persona)
result = agent.run("deploy the auth service", lessons=block)
```

Because agents are stateless, wiring is opt-in: an agent told nothing behaves
exactly as before. `LessonInjector` automates the wiring for you:

```python
injector = LessonInjector(store, limit=5, min_confidence=0.4)
result = injector.run(agent, "deploy the auth service")   # context + lessons injected
```

### How relevance is scored

Deterministic, so equal inputs rank equally:

```
score = Jaccard(query, lesson)          # token overlap (principle + context + tags)
      + 0.5  · (matching requested tags)
      + 0.3  if lesson.agent_role == agent_role
      + 0.1  · lesson.confidence
```

A lesson is a candidate only when it clears `min_confidence` **and** overlaps the
query, shares a requested tag, or matches the agent role — so an unrelated task
injects nothing. A blank `query` has no tokens to match, so `min_confidence` /
role / tag alone decide the order (handy for "always show high-confidence
lessons").

### Store API

```python
store.add(lesson) / add_many([...])         # upsert by id; preserves position
store.get(id) / remove(id) / clear()
store.all(limit=None)                        # newest first (created_at)
store.by_agent(role) / by_tag(tag) / by_outcome(outcome) / search(pred)
store.top(n=10)                              # most confident (ties: newest)
store.relevant(query, agent_role=..., tags=[...], limit=5, min_confidence=0.0)
store.build_context(query, ...)              # -> prompt block (str, "" if none)
store.stats()                                # totals + avg_confidence + counters
store.export(path) / import_from(path)       # JSON   (+ jsonl variants)
```

`render_lessons(lessons)` is the pure formatter behind `build_context`
(`- (confidence) principle — context`), usable on its own. The store is
thread-safe (RLock) and, like the collector, deliberately in-memory — durable
history stays in `src.memory`.

## Auto-extraction (Phase 4) — the pipeline in one call

`LearningOrchestrator` wires the three pieces together
(`collector → extractor → store`) and runs them with one method. Weak lessons
(**`confidence < 0.5`** by default) are dropped before storing:

```python
from src.learning import LearningOrchestrator

orchestrator = LearningOrchestrator(collector, extractor, store)   # min_confidence=0.5

report = orchestrator.auto_extract(task_id="task-42")   # or experiences=[...], or nothing
# ExtractionReport(extracted=3, kept=2, dropped=1, lessons=(...))
```

`auto_extract` resolves its source in this priority: explicit `experiences`,
else `task_id`'s collected experiences (oldest first), else **every** collected
experience. Pass `min_confidence=` to override the floor for one run.
`ExtractionReport.to_dict()` gives a JSON view (`extracted` / `kept` / `dropped` /
`lessons`).

## Persistence (Phase 4) — lessons in SQLite

The in-memory store stays the fast working copy; `LessonDatabase` is the durable
record — one `lessons` table, versioned through `PRAGMA user_version`, opened
with the same WAL connection helper as `src.memory`.

```python
from src.learning import LessonDatabase, LessonStore, default_lesson_db_path

db = LessonDatabase(default_lesson_db_path())   # or ":memory:" / any path
store = LessonStore(database=db)                # wire the store to the db

store.save_to_db()        # upsert every in-memory lesson   -> int
store.load_from_db()      # load lessons into memory         -> int
store.migrate_schema()    # ensure the schema is current (idempotent)

db.count() / db.load() / db.clear() / db.close()
```

Rows are keyed by lesson id, so `save` is an upsert (re-saving an updated lesson
overwrites the old row). `load()` returns newest-first and **skips malformed
rows** with a warning. `save_to_db` / `load_from_db` / `migrate_schema` accept an
explicit `database=` argument, so a store built without one can still be
persisted. Path override: `$ZYVO_LESSON_DB` (else
`~/.local/share/tyb-zyv-ai/lessons.db`, same dir as the memory DB).

## The Lessons view (Phase 4) — see what was learned

The TUI has a **Lessons** modal showing every lesson in the app's store:

* press **`ctrl+l`** (or type **`:lessons`**) to open it;
* columns: confidence, principle, agent, evidence count, tags;
* confidence colour: green ≥ 0.7, yellow ≥ 0.5, red below;
* filter by text (matches principle / context / tags) in the input box, and by
  confidence with keys **`1`** (all) **`2`** (≥0.3) **`3`** (≥0.6) **`4`** (≥0.8).

`TuiState` owns the store (`state.lesson_store`) and accepts a pre-populated one
via `TuiState(..., lesson_store=store)` — e.g. one loaded from SQLite at startup.

## The learning loop (Phase 5) — `ContinualLearningManager`

Phase 4's orchestrator learns **when you ask it to**. Phase 5 makes learning
**continuous**: a manager drives the whole loop and can run it automatically.

    collect  →  extract  →  validate  →  merge

```python
from src.learning import ContinualLearningManager, LessonExtractor, ...

manager = ContinualLearningManager(collector, extractor, store)

# one cycle, on demand (incremental: only NEW experiences since last time)
cycle = manager.learn()
# -> LearningCycle(collected=4, extracted=3, validated=3, merged=1, added=2, ...)

# or let it run itself — poll run_due() from a TUI tick / task hook
manager.schedule(every_experiences=10, every_seconds=300)   # either trigger
manager.run_due()   # runs a cycle only when enough new work has piled up
```

**The four steps:**

* **collect** — pulls only the experiences recorded since the previous cycle (an
  index cursor over the collector). Pass `learn(experiences=[...])` to run on an
  explicit batch instead; the cursor is left untouched.
* **extract** — delegates to the `LearningOrchestrator` in `store_results=False`
  mode, so the manager owns what gets written.
* **validate** — a pluggable `validator(lesson) -> bool`. The default keeps a
  lesson only if its confidence ≥ `min_confidence` (0.5) **and** its principle is
  non-blank.
* **merge** — folds each survivor into the store via `LessonStore.merge()`: a
  recurring principle (token Jaccard ≥ 0.6, or exact match) merges into the
  existing entry — evidence and tags union, confidence is re-scored with a
  contradiction penalty, and a fresh id replaces the old one. A novel principle
  is added as-is.

**Scheduling:** `schedule(every_experiences=10, every_seconds=0)` installs a
`SchedulePolicy`; a cycle is due once **either** N new experiences have piled up
**or** T seconds have elapsed since the last cycle (whichever fires first).
`due()` asks whether a cycle should run now, `run_due()` runs it if so (else
returns `None`), and `unschedule()` clears it. `pending()` reports how many
experiences are awaiting the next cycle, and `history` keeps every `LearningCycle`
run so far.

`LearningCycle.merged` vs `.added` tell you how much was genuinely new versus
consolidated — the health signal of continual learning.

## Anti-forgetting (Phase 6) — protecting the core

A store that lives for months accumulates noise, and a naive merge can silently
wash out an important lesson. `AntiForgetting` sits between the learner and the
store and adds three protections:

```python
from src.learning import AntiForgetting

anti = AntiForgetting(store, protection_threshold=0.8, min_evidence=3)
manager = ContinualLearningManager(
    collector, extractor, store, anti_forgetting=anti,
)
```

* **Protection** — a lesson is *protected* if it is explicitly pinned
  (`anti.protect(id)`), matches an injected `protect=lesson->bool` predicate, or
  simply clears the value rule (confidence ≥ `protection_threshold` **and**
  ≥ `min_evidence` supporting experiences). `protect_valuable()` pins the whole
  current core set at once. A protected lesson keeps its identity through every
  merge — its confidence is never silently traded away.
* **Conflict detection** — an incoming lesson that is topically the same as an
  existing one but has the **opposite polarity** ("never X" vs "always X", over
  the non-polarity tokens, Jaccard ≥ 0.5) is reported as a `Conflict` and stored
  **alongside** — the existing lesson is left untouched, so meaning is never
  collapsed. `anti.conflicts` lists everything found; `anti.resolve(conflict,
  keep="confident"|"existing"|"incoming")` drops one side (never a protected one).
* **Sparse updates** — `consolidate(lesson)` touches **only** the entry the
  incoming lesson relates to (merge) or adds a new one; every unrelated lesson
  stays byte-for-byte identical. It returns a `ConsolidationResult` with an
  `action` of `"added"`, `"merged"` or `"conflict"`.

`reinforce(lesson_id, amount=0.1)` nudges a lesson's confidence upward (spaced
repetition, clamped at 1.0). When the manager runs with a layer the cycle's
`conflicts` count reflects contradictions found during that cycle.

