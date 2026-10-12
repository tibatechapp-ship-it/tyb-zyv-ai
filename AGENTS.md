# tyb-zyv-ai — project memory

Multi-agent framework + Textual TUI over a local ZYVO HTTP API, plus two
standalone assistants (zyvo-telegram.py, zyvo-voice.py) at the repo root.

## Verify before shipping (must all pass)
```bash
ruff check .
mypy zyvo-telegram.py zyvo-voice.py src
python scripts/check_annotations.py        # every function type-annotated
python -m pytest --cov                     # full suite; coverage floor 95%
```
Test deps: `pip install -e ".[dev]"` (adds pytest-asyncio, pytest-benchmark,
syrupy). Coverage floor is enforced by `[tool.coverage.report] fail_under = 95`.

## Deep agent-framework test suite (v2.2.0)
Integration-level suites that drive the 20 real agents via mock backends in
`tests/deep_helpers.py` (DirectiveBackend / RecordingBackend / ModelBackend /
BlockingBackend) — no server/network/real LLM:
- `test_phase1_context.py` — persona contract, context fused into the USER turn
  byte-for-byte (never the system prompt), multi-turn isolation.
- `test_phase2_collab.py` — pipeline + early stop, parallel fan-out, supervisor,
  delegation, blackboard, communication bus, full 20-agent run.
- `test_phase3_resilience.py` — retry, interruptible hangs, on-disk CommandQueue
  capture + cross-instance replay, fallback chain, watchdog/heartbeat.
- `test_phase4_security.py` — traversal/injection INERT, secret scan, invariant
  `set(cls.permissions) <= set(cls.tools)`, read-only vs shell agents, rate limit.
- `test_phase5_performance.py` — 100-task load (sequential + asyncio concurrent),
  memory (tracemalloc), latency, throughput, pytest-benchmark micro-benchmarks.
- `test_snapshot_contract.py` + `tests/__snapshots__/` — syrupy golden catalog of
  the 20-agent contract (tools/permissions/persona).

## Adversarial suite (v2.3.0)
`tests/adversarial/` — red-team layer over the real 20 agents (mock backends only).
**Inverted verdict**: PASS = attacked repelled (HELD), FAIL = weakness (BROKEN).
Session table auto-printed; findings written in `ADVERSARIAL.md`. 92 vectors, 8
categories: injection, dos, manipulation, security, integration, comprehension +
bonus fuzz, chaos. Run: `python -m pytest tests/adversarial/ -v`.
- `report.py` (AdversarialReport HELD/BROKEN ledger), `attacks.py` (payload corpus
  + `tree_snapshot`/`imported_modules` helpers).
- `test_report_contract.py` (meta) guards shape: all categories present, ≥8 each,
  ≥48 total, golden snapshot of the report JSON.
- Hook/fixture (`REPORT`, `pytest_runtest_makereport`, `pytest_terminal_summary`,
  `adversarial_report`) live in the ONE root `tests/conftest.py` — see lesson.

## Architecture (key files)
- `src/core/` — types.py (Message/AgentResult), backend.py (LLMBackend
  protocol + EchoBackend for offline tests), model_router.py (ModelRouter/
  ModelTier/ModelProfile/RoutingDecision: deterministic complexity heuristic ->
  tier -> cheapest fit; strength filter + explicit override), context_manager.py
  (ContextManager: token accounting with offline estimate_tokens ~4 chars/token,
  compaction at 80% ratio — oldest turns evicted to an offload store, optional
  summarizer folds them into ONE running digest slot never evicted itself,
  keep_at_least floor + extra eviction when a dense digest tips over, restore()
  newest-first while budget allows, snapshot();
  ContextEntry/CompactionResult records), resilience.py,
  watchdog.py, zyvo_client.py (ZyvoBackend → local HTTP 127.0.0.1).
- `src/tools/` — schemas.py (ToolParameter/ToolSchema frozen tool descriptions;
  to_json_schema/to_openai/to_anthropic renderers; validate_arguments incl.
  required/type/enum/unknown keys; ToolRegistry thread-safe catalogue).
  executor.py — the REAL execution layer (Phase 10): `parse_tool_calls` (tolerant
  `<tool-call name=… args='{…}'/>` parser), `LocalToolExecutor` (read-only tools
  `read`/`list`/`grep`, every path confined to a workspace root — `../`+absolute
  escapes refused; output clamped; never raises), `ToolCall`/`ToolOutcome`/
  `ToolRun`. `write`/`edit`/`bash` are NOT executed here (server-side only).
  `BaseAgent.run_with_tools(task, root=, max_steps=5)` runs a bounded ReAct loop
  (generate → execute → feed back) while `run()` stays a single call. 100% cov.
- `src/agents/` — base.py (BaseAgent: metadata + run loop), sub_agent.py
  (SubAgent tool-restricted child + SubAgentRunner: tools ⊆ parent's (else
  ValueError), max_depth guard, optional ModelRouter for child model),
  registry.py, state.py, metrics.py, communication.py + **20 built-in agents**
  (core/planning/quality/ops, 5 each). `__init__.py` exposes `AGENT_CLASSES`
  and `all_agents(backend)` — the one-call factory for the full team.
- `src/patterns/` — supervisor, pipeline, fanout, delegation, blackboard.
- `src/tui/` — app.py (ZyvoTUI, Textual), state.py (TuiState auto-registers
  all 20 when given a backend), screens/ + widgets/.
- `src/learning/` — experience collector (Phase 1 + 1.5): in-memory, thread-safe
  buffer of `Experience` (task_id/step_id/agent_role/action_type/outcome/action/
  result/ts). Canonical lowercase names only — `KNOWN_AGENT_ROLES` (20) +
  `KNOWN_ACTION_TYPES` (10); fail-safe `record()` raises ValueError + logs
  warning. `stats()` (rates + Counter.most_common desc + time_range),
  export/import JSON + JSONL (invalid items skipped w/ warning),
  `prune(max_age_days, max_count)` / `prune_by_outcome(outcome, max_count)`.
  Queries: `get_by_agent` / `get_by_action` / `get_by_outcome` / `search(...)`
  (newest-first, limit=50, RLock snapshots). Docs: `docs/LEARNING.md`. Tests:
  `tests/test_learning_experience_collector.py` (43). Long-term storage stays
  in `src.memory`; the collector is the working in-process buffer.
- `src/learning/lesson.py` — Phase 2 `Lesson` (frozen: id/principle/context/
  confidence/evidence/source_outcome/created_at/agent_role/tags + to_dict/
  from_dict) + `LessonExtractor(backend, collector, clock=)`:
  extract_from_failure/_success/_batch/_task. Prompt asks for ONE JSON principle
  ("When [ctx], always/never [action] because [reason]"); tolerant parse (None on
  bad JSON/backend error/blank); confidence = base + min(.2,(n-1)*.05) − .25 if
  contradictory, clamped [0,1]; dedup by token Jaccard ≥ 0.6. Evidence =
  "task_id:step_id" (experiences have no id). Tests: `tests/test_learning_lesson.py`.
- `src/learning/lesson_store.py` — Phase 3 `LessonStore` (thread-safe, in-memory
  index; upsert by id; all/by_agent/by_tag/by_outcome/search/top; deterministic
  `relevant()` = Jaccard(query, principle+context+tags) + 0.5·tag-hits +
  0.3·role-match + 0.1·confidence, gated by min_confidence and a match; blank
  query → confidence order; `build_context()` → prompt block; stats; JSON+JSONL
  export/import) + `render_lessons()` (`- (conf) principle — context`) +
  `LessonInjector(store, limit, min_confidence, tags).run(agent, task)`.
  `BaseAgent.build_messages(task, context, *, lessons="")` + `run(..., lessons=)`
  fold a `--- lessons ---` block into the USER turn (never the persona). run()
  calls build_messages WITHOUT lessons then layers them on, so subclasses that
  override build_messages with the old (task, context) signature keep working.
  Tests: `tests/test_learning_lesson_store.py`.
  Phase 4 adds `LessonDatabase` (SQLite `lessons` table, PRAGMA user_version,
  upsert keyed by id, load skips malformed rows) + `default_lesson_db_path()`
  ($ZYVO_LESSON_DB else ~/.local/share/tyb-zyv-ai/lessons.db) and store methods
  `save_to_db`/`load_from_db`/`migrate_schema` (accept an explicit `database=`).
  Uses `src.memory.base.connect`. Tests: `tests/test_learning_persistence.py`.
- `src/learning/orchestrator.py` — Phase 4 `LearningOrchestrator(collector,
  extractor, store, *, min_confidence=0.5)`: `auto_extract(task_id=None,
  experiences=None, min_confidence=None)` runs collector→extractor→store in one
  call, drops lessons with confidence < floor, returns `ExtractionReport`
  (extracted/kept/dropped/lessons + to_dict). Source priority: explicit
  experiences > task's experiences (oldest-first) > all. Tests:
  `tests/test_learning_orchestrator.py`.
- `src/learning/manager.py` — Phase 5 `ContinualLearningManager(collector,
  extractor, store, *, orchestrator=, validator=, min_confidence=0.5, clock=)`
  runs the loop collect→extract→validate→merge. `learn(experiences=None)`
  incremental over a collector index cursor (explicit batch leaves cursor
  untouched); extract via orchestrator `store_results=False`; validate via
  pluggable `validator(lesson)->bool` (default: conf ≥ floor AND non-blank
  principle); merge via new `LessonStore.merge()` (similar principle → union
  evidence/tags, re-scored conf, fresh id REPLACES old entry; else add).
  `schedule(every_experiences=10, every_seconds=0)` → `SchedulePolicy` (due when
  either trigger fires; both-zero → ValueError), `due()`/`run_due()`/
  `unschedule()`, `pending()`, `history`. Returns `LearningCycle` (collected/
  extracted/validated/merged/added/dropped/lessons/duration + to_dict).
  `lesson.py` now exposes module-level `principles_similar`/`merge_lessons`/
  `blend_confidence` (extractor delegates to them; store reuses them). Tests:
  `tests/test_learning_manager.py`.
- `src/learning/anti_forgetting.py` — Phase 6 `AntiForgetting(store, *,
  protection_threshold=0.8, min_evidence=3, protect=, topic_threshold=0.5)`:
  guards against catastrophic forgetting. **Protection** — `protect`/`unprotect`/
  `is_protected` (explicit id OR `protect` predicate OR value rule: conf ≥
  threshold AND ≥ min_evidence) + `protect_valuable()`. **Conflict detection** —
  same topic (Jaccard ≥ 0.5 over NON-polarity tokens) but opposite polarity
  (always/never via `_NEGATIVE`/`_POSITIVE`) → `Conflict`; `consolidate()` stores
  it ALONGSIDE (never overwrites). **Sparse merge** — `consolidate(lesson)` →
  `ConsolidationResult(lesson, action=added|merged|conflict, conflict)`, touching
  only the related entry; preserves protected status through a merge.
  `reinforce(id, amount=0.1)` (spaced repetition), `resolve(conflict,
  keep=confident|existing|incoming)` (never drops a protected lesson), `conflicts`
  property. Manager takes optional `anti_forgetting=` and reports `conflicts` on
  `LearningCycle`. Tests: `tests/test_learning_anti_forgetting.py`.
- `src/tui/screens/lessons.py` — Phase 4 `LessonsScreen` (ModalBase): table of
  confidence/principle/agent/evidence/tags, text filter input, confidence
  keys 1–4. Opened by `ZyvoTUI.action_show_lessons` (ctrl+l app binding,
  `:lessons` command). `TuiState.lesson_store` (defaults to a fresh LessonStore;
  injectable via `TuiState(..., lesson_store=)`).
- `src/memory/` — persistent memory, plain SQLite, no deps (6 phases done):
  base.py (connect/MemoryRecord/MemoryStore+RLock/Clock), embeddings.py
  (HashingEmbedder/cosine), episodic.py, semantic.py (optional injectable
  `embedder` for cosine search; lexical default), procedural.py, emotional.py
  (+ bilingual `analyze_tone`), manager.py (MemoryManager: sessions, context
  bundle, stats, JSON export/import + unified `remember`/`recall`/`forget`,
  `retention`/`compress`/`summaries`). Docs: `docs/MEMORY.md`. Tests:
  `tests/test_memory_stores.py`, `tests/test_memory_manager.py`,
  `tests/test_embeddings.py`. DB path: `$ZYVO_MEMORY_DB` else
  `~/.local/share/tyb-zyv-ai/memory.db`; `":memory:"` for tests.
- `src/security/` — Phase 9 offline static security scanner (stdlib only, no
  execution, no network). types.py (`Severity`/`RiskLevel`/`Finding` +
  `SEVERITY_WEIGHTS`), agents.py (`SecurityAgent` base: `name`/`description`/
  `patterns`/overridable `path_filter(Path)->bool`; 8 agents — `secrets`,
  `injection`, `traversal`, `crypto`, `deserialization`, `network`,
  `dependency` (manifest-only via path_filter), `pii`; `SECURITY_AGENTS`),
  orchestrator.py (`SecurityScanner.scan_text`/`scan_file`(skips binary +
  >max_bytes)/`scan_path`(tree walk, `DEFAULT_EXCLUDE`), `ScanResult`),
  classifier.py (`FindingClassifier` dedup by (agent,rule,path,line,message) +
  score + `_verdict_for` ladder: any critical→critical; any high or ≥3 medium→
  high; any medium or ≥5 low→medium; any low/info→low; else clean), reporter.py
  (`SecurityReporter.render_markdown`/`render_json`/`render_summary`),
  `__main__.py` CLI (`python -m src.security PATH [--json] [--top N]
  [--exclude NAME] [--fail-on LEVEL]`; exit 1 at/above the gate, default high).
  Docs: `docs/SECURITY.md`. Tests: `tests/test_security_scanner.py`,
  `tests/test_security_cli.py`.

## Conventions
- Every function fully type-annotated (CI enforces via check_annotations.py).
- Agent system prompts are bilingual Arabic + English; docs/README in English.
- Ruff config is conservative (E4/E7/E9/F only), line-length 100.

## Decisions (dated)
- 2026-10-11: 20 built-in agents shipped as real files (v2.1.0);
  `TuiState(registry=None, backend=X)` auto-builds the 20-agent registry.
- 2026-10-11: v2.2.0 — deep 5-phase test suite added (506 tests, 98.5% cov);
  coverage floor raised to 95%; every agent permission now backed by a tool.
- 2026-10-11: v2.3.0 — adversarial suite added (92 vectors, 8 categories; 604
  tests total, 98.5% cov). Fixed 2 real weaknesses it found: `BaseAgent.run` now
  rejects a non-`str` backend return (Byzantine); `CommandQueue._load` catches
  `RecursionError` from a nested-JSON bomb (was a startup crash).
- 2026-10-11: v2.4.0 — deep hardening: `AgentMetrics` now guards all reads/writes
  with an instance `RLock` (reentrant — `to_dict` nests); `AgentRegistry` guards
  every method with one `threading.Lock` and `__iter__` returns a snapshot. Lock
  order is fixed registry→metrics. `tests/test_concurrency.py` (11 tests: 100-thread
  races, deadlock, lock-ordering, benchmark). 615 tests, 98.5% cov.
- 2026-10-11: version lives in pyproject.toml AND src/__init__.py (both 2.5.0).
- 2026-10-11: git push via `gh` credential helper; releases via `gh release create`.
- 2026-10-11: memory system Phase 1 shipped (`src/memory/`, v2.5.0 target):
  four stores over ONE SQLite DB + MemoryManager (sessions, context, export/
  import). No deps — works on-phone. Semantic search is lexical by default with
  an injectable `embedder` seam for Phase 2. 695 tests, 98.5% cov.
- 2026-10-11: memory Phase 6 (unified front door) — `MemoryManager.remember`
  (dispatches by `kind=`/inference: steps→skill, task→episode, else fact),
  `recall` (infer store from query cues), `forget` (facts+skills, or scoped);
  `retention`/`compress`/`summaries` (episodes → per-(day,agent) summaries that
  ACCUMULATE across runs + VACUUM); `semantic.forget(subject=None)` unscoped;
  `export`/`load` aliases. `episode_summaries` table added in `__init__`.
  802 tests, 98.6% cov. NOTE: `compress` summary count accumulates only for
  same-day batches straddling the cutoff (a summary `count` is per (day,agent)).
- 2026-10-11: `src/learning/` Phase 1 + 1.5 — `ExperienceCollector` (in-memory,
  thread-safe, injectable clock) + newest-first queries get_by_agent/
  get_by_action/get_by_outcome/search. NOTE: the user referenced this module as
  "already done" but it did NOT exist — it was created fresh this session;
  verify before assuming past work exists. 822 tests (18 new), 98.7% cov.
- 2026-10-11: learning Phase 1.5 completed (2nd round): `record()` now takes
  task_id/step_id and FAILS on blank ids or roles/actions outside
   KNOWN_AGENT_ROLES/KNOWN_ACTION_TYPES (ValueError + logger warning; canonical
  lowercase only); `stats()` → rates + Counter desc + time_range;
  export/import JSON + JSONL (invalid items skipped, never fatal);
  prune/prune_by_outcome retention. 847 tests, ~98.7% cov.
- 2026-10-11: learning Phase 2 — `LessonExtractor` (LLM-backed principles) with
  tolerant JSON parsing, deterministic confidence scoring, similarity dedup.
  LLMBackend.generate(messages: Sequence[Message], *, model=None) -> str is the
  contract (stubbed offline in tests). 869 tests, ~98.8% cov.
- 2026-10-11: learning Phase 3 — `LessonStore` + `LessonInjector` close the loop:
  relevant lessons are ranked (deterministic score) and folded into the agent
  USER turn via the new optional `lessons=` on `build_messages`/`run`. Injection
  is opt-in and byte-for-byte backward compatible (no lessons → identical
  prompt). LESSON: keep `run()` calling `build_messages(task, context)` without
  lessons and layer the block on afterwards — passing `lessons=` broke a
  subclass that overrides build_messages with the old signature. 899 tests,
  98.88% cov.
- 2026-10-11: released **v2.5.0** (memory Phases 1-6 + learning Phases 1-3).
- 2026-10-11: learning Phase 4 — `LearningOrchestrator` (collector→extractor→
  store in one call; drops lessons with confidence < 0.5) + `LessonDatabase`
  (SQLite persistence, PRAGMA user_version, upsert by id) + TUI `LessonsScreen`
  (ctrl+l / :lessons; text + confidence filters). `TuiState.lesson_store`.
- 2026-10-11: learning Phase 5 — `ContinualLearningManager` runs the loop
  collect→extract→validate→merge (incremental cursor; pluggable validator;
  `LessonStore.merge()` consolidates recurring principles instead of duplicating)
  with automatic scheduling (`schedule`/`due`/`run_due`). `lesson.py` similarity/
  merge/confidence logic lifted to module-level helpers, reused by the store.
  948 tests, 98.96% cov.
- 2026-10-11: learning Phase 6 — `AntiForgetting` (protection + conflict
  detection + sparse merge) guards the store: protected/valuable lessons keep
  identity through merges, opposite-polarity ("always" vs "never") lessons on the
  same topic are stored alongside (not overwritten) as conflicts, and
  consolidation touches only the related entry. Manager gains `anti_forgetting=`
  + `LearningCycle.conflicts`. Added `if TYPE_CHECKING:` to coverage
  `exclude_lines`. 977 tests, 99.04% cov.
- 2026-10-11: Phase 7 — ModelRouter (deterministic complexity heuristic → tier
  → cheapest profile; strength filter), src/tools/schemas.py (ToolSchema frozen
  schemas + renderers to OpenAI/Anthropic + argument validation + ToolRegistry),
  sub_agent.py (SubAgentRunner: tools ⊆ parent's, max_depth guard, optional
  router for the child model). 1027 tests, 99.09% cov.
- 2026-10-11: Phase 8 — ContextManager (token accounting + compaction at 80% +
  offload): evicted turns land in an offload store (restore() newest-first),
  summarizer folds them into ONE running digest slot (never evicted; folded into
  the next digest, no marker stacking), keep_at_least floor, extra eviction when
  a dense digest tips utilization back over the ratio, auto-compact AFTER append
  so the history is at/under the ratio when add() returns. LESSON: eviction
  loops + digest-in-the-same-list designs get index/marker bugs; a separate
  digest slot is clean and testable. 1053 tests, 99% cov (context_manager 100%).
- 2026-10-12: Phase 9 — offline static security scanner (`src/security/`):
  8 regex agents + orchestrator + dedup/score/verdict classifier + markdown/JSON
  reporter, plus a `python -m src.security` CLI that fails the build at/above
  `--fail-on` (default high). Fully stdlib, deterministic, no execution/network.
  `SecurityAgent.path_filter` is a real overridable METHOD (was a `staticmethod`
  lambda that tripped mypy: "Invalid self argument"). Docs: `docs/SECURITY.md`.
  1077 tests, 99% cov (security/* 100%).
- 2026-10-12: released **v3.0.0** (continual learning Phases 4-6 + model router/
  tools/sub-agents Phase 7 + context manager Phase 8 + security scanner Phase 9).
- 2026-10-12: **project review + fixes** (`docs/PROJECT_REVIEW.md`, the report):
  - **Fixed a real zip-bomb OOM bug** in `SecurityOrchestrator._scan_archive`
    (uncommitted Phase-10 work): it read the entry BEFORE checking size. Now the
    declared uncompressed size is checked from the ZIP central directory *before*
    `archive.read`, plus `_MAX_ENTRIES` (5000) and `_MAX_TOTAL_BYTES` (20 MB) caps
    and per-entry error handling. This bug had broken
    `tests/adversarial/test_dos.py::test_zip_bomb_bytes_are_treated_as_inert_data`
    (suite was failing 1/1116). Re-scoped that invariant to: no streaming
    decompressor imported, exactly ONE module imports `zipfile`, and a source
    assertion that the size guard precedes `archive.read` (so removing it fails).
    Regression tests: `test_zip_bomb_entry_is_skipped_without_decompression`,
    `test_archive_entry_count_is_capped`.
  - **Built the real tool-execution layer** (`src/tools/executor.py`) — the biggest
    gap vs LangGraph/CrewAI/smolagents was that tools/permissions were declarative
    only (agents executed nothing). `run_with_tools` is a bounded ReAct loop; only
    read-only tools run in-framework, confined to a root; `run()` unchanged.
  - TUI security export now defaults to `~/.local/share/tyb-zyv-ai/reports/`
    (never the working tree); `security-report-*.md` gitignored; 3 stray root
    files deleted.
  - Docs: README "Tools actually run" + security front-door sections; SECURITY.md
    Phase-10 section; new PROJECT_REVIEW.md.
  - Verified green: ruff · mypy · check_annotations · 1157 tests @ 98.90% ·
    adversarial 92/92 HELD. `src/tools/executor.py` 100% cov.
- 2026-10-12: released **v3.1.0** (Phase 10 security TUI screen + zip-bomb fix +
  real tool executor `src/tools/executor.py` + reviews/docs). Version bumped in
  both pyproject.toml and src/__init__.py. 1162 tests @ 99.09%.
- 2026-10-12: **wired tool execution into the coordination patterns** — released
  as **v3.2.0** (version bumped in both pyproject.toml and src/__init__.py):
  `ToolPolicy(root, max_steps, max_calls_per_step)` (frozen, in `src/tools/
  executor.py`, exported from `src.tools`); `src/patterns/_stage.py`
  (`run_stage` → `StageOutcome(result, tool_run)`). `Supervisor/Pipeline/Fanout/
  Delegation` take an optional `tools: ToolPolicy | None`; `None` = the old
  single-`run()` behaviour byte-for-byte. With a policy each stage runs
  `run_with_tools`, traces surface as `SupervisorReport.tool_runs` /
  `Pipeline|Fanout|Delegation.tool_runs`; return types unchanged (still
  `AgentResult`-based) so every existing caller/tests keep working.
  `tests/test_patterns_tools.py` (13). 1175 tests @ 99.10%; all patterns 100%.

- Lesson: pytest `python_classes=Test*` collects agent class `TesterAgent`/alias
  `Tester` → PytestCollectionWarning. Never import `TesterAgent` under a
  Test-prefixed name in tests; use `AGENT_CLASSES` or alias it (e.g. `as QA`).
- Lesson: mutmut/hypothesis pip installs hang on Termux (libcst sdist build);
  use `syrupy` snapshots for the offline golden tests instead.
- Lesson: NEVER add a second `conftest.py` in a tests sub-dir without `__init__.py`
  — both import as top-level module `conftest` and shadow each other (broke
  `from conftest import FakePopen` in test_zyvo_telegram/voice). Keep ONE conftest
  (root) and register its markers in pyproject `[tool.pytest.ini_options] markers`.

## Release flow
1. commit + push main
2. `gh release create vX.Y.Z --title "..." --notes "..."`