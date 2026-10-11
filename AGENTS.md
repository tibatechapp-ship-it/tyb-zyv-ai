# tyb-zyv-ai — project memory

Multi-agent framework + Textual TUI over a local ZYVO HTTP API, plus two
standalone assistants (zyvo-telegram.py, zyvo-voice.py) at the repo root.

## Verify before shipping (must all pass)
```bash
ruff check .
mypy zyvo-telegram.py zyvo-voice.py src
python scripts/check_annotations.py        # every function type-annotated
python -m pytest --cov                     # full suite; 98%+ overall
```

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
- 2026-10-11: version lives in pyproject.toml AND src/__init__.py (both now 2.1.0).
- 2026-10-11: git push via `gh` credential helper; releases via `gh release create`.

## Release flow
1. commit + push main
2. `gh release create vX.Y.Z --title "..." --notes "..."`