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

## Architecture (key files)
- `src/core/` — types.py (Message/AgentResult), backend.py (LLMBackend
  protocol + EchoBackend for offline tests), resilience.py, watchdog.py,
  zyvo_client.py (ZyvoBackend → local HTTP 127.0.0.1).
- `src/agents/` — base.py (BaseAgent: metadata + run loop), registry.py,
  state.py, metrics.py, communication.py + **20 built-in agents**
  (core/planning/quality/ops, 5 each). `__init__.py` exposes `AGENT_CLASSES`
  and `all_agents(backend)` — the one-call factory for the full team.
- `src/patterns/` — supervisor, pipeline, fanout, delegation, blackboard.
- `src/tui/` — app.py (ZyvoTUI, Textual), state.py (TuiState auto-registers
  all 20 when given a backend), screens/ + widgets/.

## Conventions
- Every function fully type-annotated (CI enforces via check_annotations.py).
- Agent system prompts are bilingual Arabic + English; docs/README in English.
- Ruff config is conservative (E4/E7/E9/F only), line-length 100.

## Decisions (dated)
- 2026-10-11: 20 built-in agents shipped as real files (v2.1.0);
  `TuiState(registry=None, backend=X)` auto-builds the 20-agent registry.
- 2026-10-11: v2.2.0 — deep 5-phase test suite added (506 tests, 98.5% cov);
  coverage floor raised to 95%; every agent permission now backed by a tool.
- 2026-10-11: version lives in pyproject.toml AND src/__init__.py (both 2.2.0).
- 2026-10-11: git push via `gh` credential helper; releases via `gh release create`.
- Lesson: pytest `python_classes=Test*` collects agent class `TesterAgent`/alias
  `Tester` → PytestCollectionWarning. Never import `TesterAgent` under a
  Test-prefixed name in tests; use `AGENT_CLASSES` or alias it (e.g. `as QA`).
- Lesson: mutmut/hypothesis pip installs hang on Termux (libcst sdist build);
  use `syrupy` snapshots for the offline golden tests instead.

## Release flow
1. commit + push main
2. `gh release create vX.Y.Z --title "..." --notes "..."`