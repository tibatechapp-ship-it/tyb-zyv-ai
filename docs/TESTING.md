# Testing

This project ships a real pytest suite that exercises **every function** in
both assistants. There is no external service, network call, subprocess, or
real clock involved — each test patches the exact seam the code touches.

```
tests/
├── conftest.py                 # importlib module loader + fakes (FakePopen/FakeResponse)
├── deep_helpers.py             # mock LLM backends for the deep agent suites
├── test_zyvo_telegram.py       # unit tests for the Telegram bot
├── test_zyvo_voice.py          # unit tests for the voice assistant
├── test_src_core.py            # types, backend, zyvo client, resilience, watchdog
├── test_src_agents.py          # BaseAgent, registry, state, metrics, communication
├── test_src_patterns.py        # supervisor, pipeline, fanout, delegation, blackboard
├── test_src_tui.py             # Textual app / screens / widgets
├── test_agents_20.py           # the 20 built-in agents' contract
├── test_phase1_context.py      # deep: personas, bilingual contract, context handling
├── test_phase2_collab.py       # deep: multi-agent collaboration patterns
├── test_phase3_resilience.py   # deep: retries, fallback, queue, watchdog
├── test_phase4_security.py     # deep: injection/traversal inert, secrets, permissions
├── test_phase5_performance.py  # deep: load (100 tasks), memory, latency, benchmarks
├── test_snapshot_contract.py   # golden catalog of the 20-agent public contract
└── __snapshots__/              # syrupy golden files
```

As of the last run: **505 tests, 98 % coverage** (branch coverage; the
`src/agents/` layer sits at 100 %).

---

## Running the tests

```bash
# 1. install the test tooling (pure-Python, works on Termux)
pip install -e ".[dev]"

# 2. run everything
pytest

# 3. with coverage
pytest --cov --cov-report=term-missing
```

The dev extra installs `pytest-asyncio` (async deep tests), `pytest-benchmark`
(the Phase-5 micro-benchmarks), and `syrupy` (golden-contract snapshots).

`pytest` picks up its configuration from `pyproject.toml`
(`[tool.pytest.ini_options]` and `[tool.coverage.*]`), so no flags are required.

---

## How the suites work

Both assistants are **single, hyphenated scripts** (`zyvo-telegram.py`), so
they cannot be imported with a normal `import`. Instead, `conftest.py` loads
each one once via `importlib.util.spec_from_file_location(...)` and exposes it
as a fixture:

| Fixture | Module under test |
|---------|-------------------|
| `tg`    | `zyvo-telegram.py` |
| `voice` | `zyvo-voice.py`    |

An autouse fixture (`_reset_module_state`) clears mutable globals
(`PARTS`, `PAUSED`, `_PROC`, `ACTION`, …) before every test so tests never
leak state into one another.

Each test then patches **the narrowest possible seam** so nothing touches the
outside world:

| Seam | Fake |
|------|------|
| `urllib.request.urlopen` | `FakeResponse` |
| `subprocess.Popen`       | `FakePopen` |
| `subprocess.run`         | a capture lambda / `SimpleNamespace(stdout=...)` |
| `time.sleep`, `os.killpg`| recording lambdas |
| the module's own `api`   | a `lambda` returning canned JSON |

---

## What is covered

### `test_zyvo_telegram.py`
- Config / env loading (`~/.config/zyvo/telegram.env`).
- Telegram API calls: `send_message` chunking + Markdown fallback, uploads,
  `sendVoice`/`sendPhoto`, typing action errors swallowed.
- `download_file` success / `ok:false`.
- ZYVO HTTP: `api`, `server_up`.
- Server lifecycle: `kill_tree`, `start_server` (success, crash, missing
  `script`), `ensure_server`.
- Session: reuse vs. create, `/new`.
- Tool parsing: `action_detail`, `parse_tools` (success, non-zero exit,
  created vs. modified files).
- Turn execution: `run_turn` (reply, retry, error surfacing).
- Reporting, control words, and the receive loop.

### `test_zyvo_voice.py`
- Arabic detection + RTL reshape (`has_arabic`, `rtl_str`).
- Terminal display (`show`, `rule`).
- HTTP + server lifecycle (same set as above).
- Session (`get_session`, incl. model override).
- `PermissionWatchdog` — replies to a pending request, then stops.
- Turn execution (`session_message_count`, `collect_turn_tools`,
  `parse_tools`, `run_turn`).
- Reports (`render_report`, `speech_report`).
- Audio in/out: `listen`, `clean_for_speech`, `speak` (chunking + language),
  `buzz`.
- `main()` — the `--text` and `--chat` paths.

---

## Deep agent-framework suites (Phases 1–5)

Beyond unit coverage, five integration-level suites drive the **20 real agents**
and every pattern through a mock LLM backend (`deep_helpers.py`), so they need
no server, network or real model:

| Phase | File | Proves |
|-------|------|--------|
| 1 — Context | `test_phase1_context.py` | Bilingual persona contract per agent; the context block is fused into the *user* turn byte-for-byte (never the system prompt); multi-turn isolation + error recovery. |
| 2 — Collaboration | `test_phase2_collab.py` | Pipeline chaining with early stop, true-parallel fan-out, supervisor aggregation, delegation hand-off, blackboard, the communication bus, and a full 20-agent run. |
| 3 — Resilience | `test_phase3_resilience.py` | Retry recovery with bounded attempts, interruptible hangs, on-disk command-queue capture + cross-instance replay, model fallback chain, bounded watchdog/heartbeat loops. |
| 4 — Security | `test_phase4_security.py` | Path traversal and command injection are **inert** (hostile text is never executed), no hardcoded secrets, and the permission model is consistent (every permission backed by a declared tool; read-only agents can't write/shell out). |
| 5 — Performance | `test_phase5_performance.py` | 100 tasks sequential + 100 concurrent (asyncio), bounded memory over 100 runs, low single-run latency, minimum throughput, plus `pytest-benchmark` micro-benchmarks. |

A golden-contract snapshot (`test_snapshot_contract.py`, syrupy) pins the whole
20-agent public contract so an accidental rename/dropped-tool/widened-permission
change shows up as a reviewable diff.

---

## CI

| Workflow | What it does |
|----------|--------------|
| `.github/workflows/ci.yml`   | Ruff lint, mypy type-check, type-hint coverage, secret scan. |
| `.github/workflows/test.yml` | Runs this suite on Python 3.11/3.12/3.13, and publishes the coverage badge. |

The coverage badge is generated **inside the repo** — no Codecov account or
token needed. After a push to `main`, the `coverage-badge` job writes
`.github/badges/coverage.json` (a shields.io *endpoint* payload) and commits it
with `[skip ci]`. The README badge points at that file via
`https://img.shields.io/endpoint`.

---

## Conventions

- **Every function gets at least one test.** New public functions should come
  with a test in the same file.
- **Patch, don't hit.** No test may perform real I/O, network, or sleep.
- **Keep coverage ≥ 95 %.** `[tool.coverage.report]` sets `fail_under = 95`, so
  `pytest --cov` fails the build if overall coverage drops below the bar.
- **Type-annotate everything** in `tests/` too (the repo targets Python 3.11+).
