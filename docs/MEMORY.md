# Memory

The agent team has a persistent, four-part memory backed by a single SQLite
file. It needs **no server, no model and no extra dependencies** — it runs
offline on a phone exactly as it does on a desktop.

```
src/memory/
├── base.py        # connect(), MemoryRecord, MemoryStore (clock + RLock), MemoryKind
├── embeddings.py  # HashingEmbedder, cosine, make_embedder (offline semantic search)
├── episodic.py    # EpisodicMemory   — what happened (timestamped episodes)
├── semantic.py    # SemanticMemory   — what is true   (durable facts)
├── procedural.py  # ProceduralMemory — how to do things (named skills)
├── emotional.py   # EmotionalMemory  — how the user feels (tone/prefs/satisfaction)
└── manager.py     # MemoryManager    — DB, sessions, context, remember/recall, export
```

## Why four stores

Each store answers a different question, so an agent can pull exactly the slice
it needs instead of replaying one giant log:

| Store | Question | Backing table(s) | Key fields |
|-------|----------|------------------|------------|
| **Episodic** | *What happened?* | `episodes` | ts, iso, session_id, agent, task, result, ok, duration_s |
| **Semantic** | *What is true?* | `facts` | subject, key, value, source |
| **Procedural** | *How do we do it?* | `skills` | name, description, steps, examples, tags |
| **Emotional** | *How does the user feel?* | `preferences`, `signals`, `satisfaction` | key/value/confidence, sentiment/score |

Every row is surfaced as an immutable `MemoryRecord` (`.id`, `.kind`, `.ts`,
`.iso`, `.data`) with a JSON-friendly `.to_dict()`.

## Quick start

```python
from src.memory import MemoryManager

with MemoryManager() as mem:                 # default DB:
    sid = mem.start_session()                #   ~/.local/share/tyb-zyv-ai/memory.db

    mem.remember_task("Planner", "plan the day", "done", duration_s=1.2)
    mem.semantic.remember("user_name", "tyba", subject="user")
    mem.procedural.learn("deploy", "ship to GitHub Pages",
                         steps=["build", "push"], tags=["web"])
    mem.emotional.set_preference("tone", "arabizi")
    mem.emotional.observe("shukran, perfect!")          # -> ("positive", 1.0)

    print(mem.context_text("deploy"))        # prompt-ready digest
    mem.end_session(summary="phase 1 demo")
```

The database path can be overridden in three ways: pass `path=` to
`MemoryManager`, set `$ZYVO_MEMORY_DB`, or use the built-in default. `":memory:"`
gives a throwaway database (great for tests).

## One-word front door — remember / recall / forget

When you do not want to think about *which* store to use, the manager picks it
for you. `remember()` infers the store from the arguments, `recall()` from the
question:

```python
mem.remember("the deploy target is GitHub Pages")          # -> semantic (a fact)
mem.remember("ship the release", task="ship", steps=["tag", "push"])  # -> procedural
mem.remember("ran the night build", task="night build")    # -> episodic
mem.remember("reply terse", kind="preference")             # -> emotional

mem.recall("how do I deploy?")     # "how to"/"steps"  -> procedural memory
mem.recall("what did we do today?")  # "what did we"   -> episodic memory
mem.recall("my preference for tone")  # "preference"    -> emotional memory
mem.recall("deploy target?")        # otherwise        -> semantic memory

mem.forget("deploy")               # drop the key from facts AND skills
mem.forget("deploy", kind="semantic", subject="ops")   # one store, scoped
```

`remember` accepts `kind=` (`"auto"` default) with synonyms — `fact`, `episode`,
`skill`, `preference`. With `"auto"`: `steps=` means a skill, `task=` means an
episode, otherwise a fact. Every call returns `{"kind", "id"}`. `recall` returns
`{"kind", "query", "results"}` (list of `MemoryRecord.to_dict()`). Passing an
explicit `kind=` to either call skips inference.

## Retention & compression

Episodes are the only ever-growing store, so the manager can prune or roll them
up. `retention()` drops old or excess episodes; `compress()` replaces episodes
older than a cutoff with per-day weighted summaries (so history survives without
keeping every row):

```python
mem.retention(max_age_days=90, max_episodes=10_000)   # -> {"episodes_removed": n}
mem.compress(older_than_days=30)                      # -> {"episodes_removed", "summaries", "vacuumed"}
mem.summaries()        # [{"day": "2026-09-01", "agent": "Coder", "count": 42, ...}]
```

Summaries key on `(day, agent)` and **accumulate** across compress runs, so
running `compress()` daily keeps folding into the same rows. `compress()` also
`VACUUM`s the database to reclaim space.

## The stores in detail

### Episodic — what happened

```python
mem.episodic.record("Coder", "write code", "boom", ok=False, duration_s=0.4)
mem.episodic.recent(10)              # newest first
mem.episodic.last()                  # the single latest episode, or None
mem.episodic.between(start, end)     # inclusive time window, oldest first
mem.episodic.for_agent("Coder")      # one agent's history
mem.episodic.for_session(sid)        # one session's history
mem.episodic.search("deploy")        # substring over task + result
mem.episodic.activity()              # {"2026-10-11": 12, ...} day -> count
```

### Semantic — what is true

Facts are keyed by `(subject, key)` and upserted, so remembering the same fact
twice updates it instead of duplicating.

```python
mem.semantic.remember("user_name", "tyba", subject="user")
mem.semantic.recall("user_name", subject="user")   # "tyba"
mem.semantic.forget("user_name", subject="user")
mem.semantic.search("arabic")                      # relevance-ranked facts
mem.semantic.all()                                 # everything, sorted
```

**Relevance ranking** is dependency-free: by default a lexical (token-overlap)
score over subject/key/value. Because it is Unicode-aware, Arabic words match
too. Pass an `embedder` (see [Embeddings](#embeddings-phase-2)) and `remember`
stores a vector while `search` / `top_k` rank by cosine similarity — the call
site barely changes:

```python
mem = MemoryManager(embedder="hashing")   # built-in, offline
```

`search(query, limit=...)` and its alias `top_k(query, k=...)` return the Top-K
most relevant facts.

### Procedural — how to do things

```python
mem.procedural.learn("deploy", "ship to GitHub Pages",
                     steps=["build", "push"], examples=["make deploy"],
                     tags=["web", "github"])
mem.procedural.get("deploy")
mem.procedural.find("github deploy")   # best-matching skills
mem.procedural.all()                   # sorted by name
mem.procedural.forget("deploy")
```

### Emotional — how the user feels

```python
mem.emotional.set_preference("tone", "arabizi", confidence=0.9)
mem.emotional.preference("tone")                 # "arabizi"
mem.emotional.observe("this is great, shukran")  # classify + log, -> (label, score)
mem.emotional.signals(limit=20)                  # recent tone signals
mem.emotional.record_satisfaction(0.9, note="nice")
mem.emotional.average_satisfaction()             # mean of all scores, or None
```

`analyze_tone(text)` is a small bilingual lexicon (English + common Arabic
cues) returning `("positive" | "negative" | "neutral", score in [-1, 1])`.
It is the seam a real sentiment model plugs into later without changing callers.

## Embeddings (Phase 2)

ChromaDB, FAISS and sentence-transformers do not install on a phone (Termux,
Python 3.14), so the default embedder is a **feature-hashing** model that runs
anywhere with the standard library — and any real model plugs into the same seam.

```python
from src.memory import HashingEmbedder, cosine, make_embedder

embedder = HashingEmbedder(dim=512, ngram=3)   # words + character 3-grams
embedder("deploy the release")                  # -> L2-normalised 512-d vector
cosine(embedder("deploy"), embedder("deploying"))   # high (typo/stem tolerant)

mem = MemoryManager(embedder="hashing")   # or MemoryManager(embedder=embedder)
mem = MemoryManager(embedder=my_model)    # any Callable[[str], Sequence[float]]
```

How it works: every word plus every character *n*-gram inside it is hashed into a
fixed-size vector with a **signed** hash (the "hashing trick"), then L2-normalised.
Using `hashlib` (not Python's `hash`) keeps vectors identical across runs and
machines. This catches typos, stems and substrings that plain token overlap
misses, while staying fast and fully offline.

`make_embedder(spec)` resolves `None` (→ lexical), a callable (→ unchanged) or a
name (`"hashing"`). On `import_json`, facts are re-embedded automatically when
the manager has an embedder configured.

## Sessions

A session groups the episodes of one working stretch.

```python
sid = mem.start_session()          # or mem.start_session("my-id")
mem.remember_task("Planner", "plan", "done")   # tagged with the current session
mem.current_session()              # sid, or None
mem.end_session(summary="shipped phase 1")
mem.sessions(limit=20)             # newest first
mem.session(sid)                   # one session dict, or None
```

## Context bundle — prime an agent before it runs

`mem.context(query="")` returns a dictionary with recent episodes, the most
relevant facts and skills, active preferences and the running satisfaction
average. `mem.context_text(query="")` renders the same thing as a compact
prompt-friendly block:

```
session: 2308fd69969b
- Planner [ok] plan the day
- Coder [fail] write code
* user_name: tyba
+ skill deploy
~ tone = arabizi
satisfaction: 0.90
```

## Export / import and stats

```python
mem.stats()                     # {"sessions": 1, "episodes": 2, ...}
mem.snapshot()                  # full dict of every table (version 1)
mem.export_json("dump.json")    # -> bytes written   (alias: mem.export(...))
mem.import_json("dump.json")    # -> rows restored per table (alias: mem.load(...))
```

Import uses `INSERT OR REPLACE` with the original ids, so loading the same
snapshot twice does not duplicate rows.

## Concurrency

The SQLite connection is opened with `check_same_thread=False` and WAL mode, and
every store guards execution with an instance `RLock` (mirroring the
thread-safety work in `src/agents`). `MemoryManager` guards its own session and
import writes with a lock as well. A 5-thread × 20-write test asserts that all
100 episodes persist.

## Testing

```bash
python -m pytest tests/test_memory_stores.py tests/test_embeddings.py \
                 tests/test_memory_manager.py -q
```

- `tests/test_memory_stores.py` — the four stores + base helpers (injected
  timestamps, no real clock).
- `tests/test_embeddings.py` — the hashing embedder, cosine and the factory.
- `tests/test_memory_manager.py` — sessions, context, export/import round-trip,
  idempotent import, concurrency, the unified `remember`/`recall`/`forget` API and
  retention/compression (including same-day summary accumulation).

The memory package sits at **~99 % coverage**; the whole suite stays above the
95 % floor.

## Roadmap

- **Phase 1 (done)** — persistent SQLite stores, sessions, context, export/import.
- **Phase 2 (done)** — offline embeddings (`HashingEmbedder`) + Top-K cosine
  search (the `embedder` seam), reachable by name or callable.
- **Phase 3 (done)** — richer episodic queries: `activity_graph(days=...)` and
  `timeline()` alongside `between()` / `for_session()`.
- **Phase 4 (done)** — procedural skills with `learn` / `find` retrieval and
  auto-learning (`Skill` / `SkillsRegistry`).
- **Phase 5 (done)** — tone analysis, auto-adjusting replies, satisfaction scoring.
- **Phase 6 (done)** — one-word front door (`remember` / `recall` / `forget`),
  retention policy and episode compression (summaries).
