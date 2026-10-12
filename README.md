# tyb-zyv-ai

> **AI-powered assistants for Termux** — voice and Telegram front-ends for the
> [ZYVO](https://github.com/zyvo9/ZYVO-AI) local AI brain, running entirely on your
> Android phone.

[![CI](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/actions/workflows/ci.yml)
[![Tests](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/actions/workflows/test.yml/badge.svg)](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/tibatechapp-ship-it/tyb-zyv-ai/main/.github/badges/coverage.json)](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

This repository ships two standalone assistants that turn a phone running
[Termux](https://termux.dev) into a hands-free AI terminal:

| Assistant | File | What it does |
|-----------|------|--------------|
| **Telegram bot** | `zyvo-telegram.py` | Chat with ZYVO from anywhere. Send text **or** a voice note; get back an Arabic text report, an Arabic voice note, and any images ZYVO produced. |
| **Voice assistant** | `zyvo-voice.py` | Fully on-device, always-on voice loop: speak → ZYVO executes → it reads you a detailed Arabic report of what it did. |

Both talk to the **ZYVO HTTP server** (a local AI agent built on the
[opencode](https://github.com/sst/opencode) fork) over `127.0.0.1`, so nothing
leaves your device except what you explicitly ask the model to do.

---

## Features

### `zyvo-telegram.py` — Telegram bridge
- **Text and voice input.** Voice notes are transcribed automatically (Arabic by default).
- **Rich replies.** Arabic text report + Arabic voice note (gTTS, with an `espeak` fallback) + photos.
- **Real action reporting.** Reads what tools actually ran from the server's SSE stream — not guesses.
- **Permission-safe.** A background watchdog auto-answers tool-permission prompts so a turn never hangs.
- **Kill switch.** Send `توقف` / `/stop` to abort and enter safe (chat-only) mode; `شغّل` / `/resume` to re-enable execution.
- **Single-chat lock.** Only the configured chat ID is answered.
- **Self-healing server.** Health-checks ZYVO before every turn and restarts it if it died.
- **Runs as a service.** Designed for `termux-services` with a wake-lock so Android never suspends it.
- **Standalone test modes.** `--self-test` and `--send-test`.

### `zyvo-voice.py` — on-device voice assistant
- **Continuous loop.** vibrate → listen → execute → speak report → repeat, until a stop word or `Ctrl+C`.
- **Full action mode.** ZYVO really runs bash, writes/edits files, etc.
- **Spoken Arabic action report.** Summarises what it understood, what it ran, and what changed.
- **Never freezes.** Permission watchdog + server health-check/restart + on-disk session memory + retries.
- **Configurable.** `--chat` (no execution), `--no-mic`, `--no-speak`, `--no-report`, `--text "…"`, `--new`.
- **RTL-aware output.** Optional `rtl` helper re-shapes Arabic for terminals that render it left-to-right.

### `src/core` — reliability layer for the server
- **Backoff retries.** Five attempts with 5s → 10s → 20s → 40s delays (`RetryPolicy`).
- **Model fallback.** If the primary model fails, each alternate is tried in turn (`FallbackBackend`).
- **Caching & rate limiting.** One-hour reply cache (`ResponseCache`) and a sliding-window request cap (`RateLimiter`).
- **Offline queue.** Requests captured while the server is down are stored on disk and replayed later (`CommandQueue`).
- **Structured logging.** One JSON object per line, rotating daily (`logging_utils`).
- **Watchdog + heartbeat.** Restarts the service when it dies and pings Telegram every five minutes (`Watchdog`/`Heartbeat`, `scripts/health_monitor.py`).

### `src/agents` + `src/tui` — the 20-agent framework
A multi-agent layer over ZYVO: every agent is a specialised persona (system
prompt + tools + permissions) running on a shared `LLMBackend`. Build the
full team with one call, **no live server needed**:

```python
from src.agents import all_agents
from src.core.backend import EchoBackend

agents = all_agents(EchoBackend())   # 20 agents, grouped: core · planning · quality · ops
```

| Agent | Group | Role |
|-------|-------|------|
| Architect | core | Designs system architecture, components and data flow |
| Coder | core | Writes clean, tested implementation code |
| Reviewer | core | Reviews code for correctness, style and clarity |
| Tester | core | Writes and runs tests; reports coverage honestly |
| Debugger | core | Diagnoses failures and proposes minimal fixes |
| Planner | planning | Breaks goals into ordered, testable tasks |
| Analyst | planning | Analyses requirements, constraints and assumptions |
| Researcher | planning | Gathers current, source-backed information |
| Designer | planning | Designs UI/UX, schemas and APIs |
| Estimator | planning | Estimates effort, cost and risk with ranges |
| SecurityAuditor | quality | Audits code and config for vulnerabilities |
| Performance | quality | Finds and removes performance bottlenecks |
| Documentation | quality | Writes user and developer documentation |
| Refactor | quality | Restructures code without changing behaviour |
| Compliance | quality | Checks licences, policies and legal constraints |
| Orchestrator | ops | Coordinates agents and pipelines to the goal |
| DevOps | ops | Builds, deploys and provisions infrastructure |
| Monitor | ops | Watches health signals and alerts on anomalies |
| Recovery | ops | Restores service after failures |
| Reporter | ops | Summarises outcomes for the human |

`TuiState` (and the Textual TUI) auto-register all 20 when given a backend:

```bash
pip install ".[tui]"
python -c "from src.core.backend import EchoBackend; from src.tui.app import run_app; run_app(backend=EchoBackend())"
```

The `TUI`'s subtitle says "20-agent console" — because now it is one.

### `src/security` — offline static security scanner
A deterministic, dependency-free scanner for source trees. Eight agents (
secrets · injection · traversal · crypto · deserialization · network ·
dependency · pii) find risky shapes, a classifier judges the risk, and a
reporter renders markdown/JSON. Nothing is executed; nothing leaves the device.

```bash
python -m src.security src                 # markdown report
python -m src.security . --json            # machine-readable
python -m src.security . --fail-on high    # exit 1 on HIGH+ (pre-commit / CI)
```

Full guide → [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Requirements

- **Android + [Termux](https://termux.dev)** (also runs on any Linux/macOS shell for the parts that don't need Termux APIs).
- **Python 3.11+** (uses modern type hints and `X | Y` unions).
- **ZYVO** installed and on `PATH` — see [ZYVO-AI](https://github.com/zyvo9/ZYVO-AI).
- **Termux:API** app + `termux-api` package (for `termux-tts-speak`, `termux-speech-to-text`, `termux-vibrate`, `termux-wake-lock`).
- **ffmpeg** (audio conversion), **espeak** (offline TTS fallback).
- Optional Python packages: **`gTTS`** (better voice notes) and **`SpeechRecognition`** (voice-note transcription) — see `requirements.txt`.
- Optional: **`termux-services`** (run the bot as a background service).

---

## Installation

Full step-by-step guide → [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

**Quick version:**

```bash
# 1. core packages
pkg update && pkg install -y python termux-api ffmpeg espeak git

# 2. this repo
git clone https://github.com/tibatechapp-ship-it/tyb-zyv-ai.git
cd tyb-zyv-ai

# 3. python extras (optional but recommended)
pip install -r requirements.txt

# 4. install the scripts
install -m755 zyvo-telegram.py  "$PREFIX/bin/zyvo-telegram"
install -m755 zyvo-voice.py     "$PREFIX/bin/zyvo-voice"
```

---

## Usage

Full guide → [`docs/USAGE.md`](docs/USAGE.md).

### Telegram bot

```bash
# configure (once)
mkdir -p ~/.config/zyvo
cat > ~/.config/zyvo/telegram.env <<'EOF'
TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token
ZYVO_TELEGRAM_CHAT_ID=123456789
EOF
chmod 600 ~/.config/zyvo/telegram.env

# verify, then run
zyvo-telegram --self-test
zyvo-telegram
```

Then just message the bot. Text or voice — ask it to do anything.

### Voice assistant

```bash
zyvo-voice                 # full voice loop (listens, executes, speaks the report)
zyvo-voice --text "list my downloads and count the files"   # one typed command
zyvo-voice --chat          # talk only — never executes tools
```

Say **"توقف"** (or `stop`) to end the loop.

---

## Examples

**Telegram — voice → action**
> 🎤 *(voice)* "اعمل فولدر اسمه مشروع وانسخ فيه كل ملفات py من الهوم"
> 🤖 *(text)* "تم — أنشأت `~/مشروع` ونسخت 6 ملفات `.py`. …"
> 🔊 *(voice note)* report spoken in Arabic
> 🖼 *(photos)* any screenshots the tool produced

**Telegram — control commands**
```
/new       → start a fresh conversation
/status    → show mode + session state
توقف       → STOP execution, enter safe mode (chat only)
شغّل       → RESUME execution
```

**Voice — hands-free**
```
you  (speak): "show me the biggest 5 files in my home folder"
buzz → it runs du/sort → you hear: "the largest five files are … a total of …"
you  (speak): "stop"
```

**One-shot (scriptable)**
```bash
zyvo-voice --text "git status in ~/myapp" --no-speak
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Every package, app, token and permission needed — start to finish. |
| [`docs/USAGE.md`](docs/USAGE.md) | All CLI flags, control words, env vars, and recipes. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the pieces fit: PTY server, SSE stream, permission watchdog, retries. |
| [`docs/SECURITY.md`](docs/SECURITY.md) | The Phase 9 static security scanner — agents, verdict ladder, CLI. |
| [`docs/SERVER_RELIABILITY.md`](docs/SERVER_RELIABILITY.md) | The `src/core` resilience layer: retries, cache, queue, fallback, JSON logging, watchdog + heartbeat. |
| [`docs/TESTING.md`](docs/TESTING.md) | Running the test suite, coverage, fakes, and how CI is wired. |
| [`docs/FAQ.md`](docs/FAQ.md) | "Service not found", no audio, mic not working, TTS language, and more. |

---

## Configuration reference

| What | Variable / file | Default |
|------|-----------------|---------|
| Telegram token | `TELEGRAM_BOT_TOKEN` in `~/.config/zyvo/telegram.env` | — |
| Telegram allowed chat | `ZYVO_TELEGRAM_CHAT_ID` | — |
| Telegram server port | `ZYVO_TELEGRAM_PORT` | `4142` |
| Voice server port | `ZYVO_VOICE_PORT` | `4141` |
| Voice TTS language | `ZYVO_VOICE_TTS_LANG` | device default |
| Voice model | `ZYVO_VOICE_MODEL` | ZYVO default |

---

## Contributing

Issues and pull requests are welcome.
1. Fork the repo and create a feature branch.
2. Keep every function type-annotated (the CI enforces `ruff` + `mypy`).
3. Run the tests — `pip install -e ".[dev]" && pytest --cov` — and keep
   coverage at or above 95%.
4. Open a pull request describing the change.

See [`docs/TESTING.md`](docs/TESTING.md) for the full test/coverage workflow.

---

## License

Released under the [MIT License](LICENSE).

ZYVO itself is a separate project by
[zyvo9](https://github.com/zyvo9) released under its own license; this repository
only provides front-end assistants that talk to it.
