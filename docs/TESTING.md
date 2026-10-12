# Testing

This project ships a real pytest suite that exercises **every function** in
both assistants. There is no external service, network call, subprocess, or
real clock involved — each test patches the exact seam the code touches.

```
tests/
├── conftest.py                     # importlib module loader + fakes (FakePopen/FakeResponse)
├── deep_helpers.py                 # mock LLM backends for the deep agent suites
├── test_zyvo_telegram.py           # unit tests for the Telegram bot
├── test_zyvo_voice.py              # unit tests for the voice assistant
├── test_src_core.py                # types, backend, zyvo client, resilience, watchdog
├── test_src_agents.py              # BaseAgent, registry, state, metrics, communication
├── test_src_patterns.py            # supervisor, pipeline, fanout, delegation, blackboard
├── test_src_tui.py                 # Textual app / screens / widgets
├── test_agents_20.py               # the 20 built-in agents' contract
├── test_concurrency.py             # thread-safety (races, lock ordering, benchmark)
├── test_core_model_router.py       # ModelRouter (Phase 7)
├── test_core_context_manager.py    # ContextManager token accounting + compaction (Phase 8)
├── test_tools_schemas.py           # ToolSchema renderers + argument validation (Phase 7)
├── test_sub_agent.py               # SubAgentRunner tool-restriction + depth guard (Phase 7)
├── test_memory_*.py                # memory stores, manager, embeddings, tone (Phases 1-6)
├── test_learning_*.py              # experience collector, lessons, manager, anti-forgetting
├── test_security_scanner.py        # the 8 security agents + orchestrator/classifier/reporter
├── test_security_cli.py            # the `python -m src.security` command line
├── test_phase1_context.py          # deep: personas, bilingual contract, context handling
├── test_phase2_collab.py           # deep: multi-agent collaboration patterns
├── test_phase3_resilience.py       # deep: retries, fallback, queue, watchdog
├── test_phase4_security.py         # deep: injection/traversal inert, secrets, permissions
├── test_phase5_performance.py      # deep: load (100 tasks), memory, latency, benchmarks
├── test_snapshot_contract.py       # golden catalog of the 20-agent public contract
├── adversarial/                    # red-team suite (92 vectors, 8 categories)
└── __snapshots__/                  # syrupy golden files
```

As of the last run: **1077 tests, 99 % coverage** (branch coverage; the
`src/agents/`, `src/security/` and `src/tools/` layers sit at 100 %).

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

## Adversarial suite (`tests/adversarial/`)

The red-team layer. It **inverts the verdict**: a test PASSES when the attack is
repelled (**HELD**) and FAILS when it breaks through (**BROKEN**) — a BROKEN
vector is a discovered weakness to fix, not a test bug. Mock backends only; no
server, network or real LLM. A verdict table prints at the end of the run and the
findings are written up in [`ADVERSARIAL.md`](../ADVERSARIAL.md).

```bash
python -m pytest tests/adversarial/ -v              # 92 vectors + verdict table
python -m pytest tests/adversarial/ -m "not slow"   # skip heavy vectors
```

| Category | File | What it attacks |
|----------|------|-----------------|
| 1 — Injection | `test_injection.py` | prompt/command/SQL/path/template/header injection, unicode — all delivered as **inert text** |
| 2 — DoS | `test_dos.py` | endless loops, recursion, huge & bomb payloads, request floods, ReDoS (all **bounded**) |
| 3 — Manipulation | `test_manipulation.py` | role confusion, context flip, nested orders, authority, salami, gaslighting — authority is a **code fact** |
| 4 — Security | `test_security.py` | secret leak, permission drift, sandbox escape, concurrency, SSRF, supply chain |
| 5 — Integration | `test_integration.py` | cascade, deadlock, livelock, Byzantine, partition, clock skew, split brain, herd |
| 6 — Comprehension | `test_comprehension.py` | ambiguity, contradiction, paradox, sarcasm, code-switch (Arabic), typos — delivered **verbatim** |
| 7 — Fuzzing (bonus) | `test_fuzz.py` | ~1,000 seeded hostile strings through agent/message/queue/cache |
| 8 — Chaos (bonus) | `test_chaos.py` | random backend faults under fan-out/pipeline/retry |

`test_report_contract.py` guards the suite's shape (all categories present, each
≥ 8 vectors, ≥ 48 total) and pins a golden snapshot of the report JSON. Vectors
7–8 are the commissioned *bonus* work; category 5 exposed and fixed a Byzantine-
backend type leak, and category 2 exposed and fixed a nested-JSON stack
exhaustion in the persistent queue (both documented in `ADVERSARIAL.md`).

---

## Concurrency hardening (`test_concurrency.py`)

`AgentMetrics.record` and `AgentRegistry.register` are the two shared, mutable
coordination points. Both are now genuinely thread-safe — `AgentMetrics` uses an
instance `RLock` (reentrant, because `to_dict` nests through its read helpers),
`AgentRegistry` uses a single `threading.Lock` on every method with `__iter__`
yielding a snapshot. `test_concurrency.py` proves it with **100 real threads**
per race (barrier-released for maximum contention, 30 s join timeout = deadlock
assertion): no lost metric updates, no torn snapshots, no duplicate-register
race, 200 agents under concurrent read/write, plus **deadlock** and
**lock-ordering** checks and a `pytest-benchmark` of the locked `record` path
(~1.5 µs). Findings + fix rationale: `ADVERSARIAL.md` → *Deep Hardening*.

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
