# Testing

This project ships a real pytest suite that exercises **every function** in
both assistants. There is no external service, network call, subprocess, or
real clock involved — each test patches the exact seam the code touches.

```
tests/
├── conftest.py            # importlib module loader + fakes (FakePopen/FakeResponse)
├── test_zyvo_telegram.py  # unit tests for the Telegram bot
└── test_zyvo_voice.py     # unit tests for the voice assistant
```

As of the last run: **172 tests, 100 % coverage** (branch coverage).

---

## Running the tests

```bash
# 1. install the test tooling (pure-Python, works on Termux)
pip install pytest pytest-cov

# 2. run everything
pytest

# 3. with coverage
pytest --cov --cov-report=term-missing
```

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
- **Keep coverage ≥ 80 %.** `pytest --cov` fails nobody by default, but the
  badge and PRs should stay green.
- **Type-annotate everything** in `tests/` too (the repo targets Python 3.11+).
