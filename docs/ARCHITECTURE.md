# Architecture

How the two assistants work under the hood, and why they are built the way they
are.

---

## The big picture

```
            ┌─────────────────────────────────────────────────┐
 Telegram   │  zyvo-telegram.py                                │
 cloud  ⇄   │   poll getUpdates → work queue → worker()        │
            │   ├─ voice note → ffmpeg → SpeechRecognition     │
            │   ├─ send prompt → ZYVO HTTP server (127.0.0.1)  │
            │   ├─ watch SSE /event for real tool parts        │
            │   └─ reply: text + gTTS voice note + photos      │
            └───────────────┬──────────────────────────────────┘
                            │ HTTP  (port 4142)
                            ▼
            ┌─────────────────────────────────────────────────┐
            │  zyvo-voice.py                                   │
            │   vibrate → termux-speech-to-text →              │
            │   send prompt → ZYVO HTTP server →               │
            │   build Arabic action report → termux-tts-speak  │
            └───────────────┬──────────────────────────────────┘
                            │ HTTP  (port 4141)
                            ▼
            ┌─────────────────────────────────────────────────┐
            │  ZYVO  (opencode fork, local)                    │
            │   launched under a PTY:  script -qfc "zyvo --port P" │
            │   exposes  POST /session  ·  GET /event  ·  …     │
            └─────────────────────────────────────────────────┘
```

Both assistants are **thin HTTP clients**. All the intelligence lives in the
local ZYVO server; the assistants just move audio/text in and actions/reports out.

---

## Why launch ZYVO under a PTY?

On several ZYVO builds, the convenient entry points are broken:

```
$ zyvo run
Service not found: @opencode/Provider
$ zyvo serve
Service not found: @opencode/Provider
```

…but the **full TUI app** still exposes a working HTTP server. So the assistants
spawn it wrapped in a pseudo-terminal:

```python
script -qfc "zyvo --port 4142" /dev/null
```

`script` gives the app a real TTY (so it doesn't bail out), and `-fc` keeps it in
the background. The assistant then talks to `http://127.0.0.1:4142`.

> If a future ZYVO release fixes `zyvo serve`, the launch command is the only
> thing that needs to change — everything downstream stays the same.

---

## The HTTP surface used

| Method & path | Purpose |
|---------------|---------|
| `GET  /session` | list sessions |
| `POST /session` | create a session (optionally pick a model) |
| `POST /session/{id}/message` | send a prompt, block for the reply |
| `GET  /session/{id}/message` | read history (text parts) |
| `GET  /event` | **SSE** stream of live events |
| `POST /session/{id}/permissions/{pid}` | answer a tool-permission prompt |

### The critical detail: tools are only visible on the SSE stream

A gotcha this repo exists to tame: **tool parts are not returned by the message
endpoints.** `POST /session/{id}/message` returns only the final text;
`GET /session/{id}/message` returns text parts only. To see *which tools ran*
(bash, writes, edits, their inputs, outputs and titles) you must subscribe to:

```
GET /event        →   data: {json} lines
```

and listen for `message.part.updated` events whose `properties.part.type ==
"tool"`. That is exactly how both assistants build their **real** action reports.

```
event:  properties.part.type == "tool"
fields: .tool                → "bash" | "write" | "edit" | ...
        .state.input         → tool arguments
        .state.status        → pending | running | completed
        .state.title         → human summary
        .state.output        → result
        .state.metadata.exists → false means "new file"
```

See `sse_listener()` (Telegram) / `collect_turn_tools()` (voice).

---

## Reliability mechanisms

A phone is a hostile place for a long-running process. Both scripts defend
against it:

| Mechanism | Where | What it prevents |
|-----------|-------|------------------|
| **Permission watchdog** | `PermissionWatchdog` (thread) | A turn hanging forever on a tool-approval prompt — the watchdog auto-answers it. |
| **Health check + restart** | `ensure_server()` | Talking to a dead server after Android reclaimed it — it restarts ZYVO first. |
| **On-disk session** | `~/.config/zyvo/*-session` | Losing the conversation across restarts. |
| **Retries** | per request (3 attempts) | Transient connection failures. |
| **Wake-lock** | `termux-wake-lock` | Android suspending the CPU mid-turn. |
| **Process-tree kill** | `kill_tree()` | Orphaned `zyvo` children leaking when a server is restarted. |

---

## Telegram specifics

- **Long polling**, not webhooks — no public URL needed, works behind NAT.
- **Single worker thread** pulls from a `queue.Queue`, so overlapping messages
  are serialised and the chat never sees two turns fight.
- **One-chat lock** — messages from any other chat id are ignored.
- **SAFE mode** — a global flag flips between "execute" and "chat only"; the
  control words in `STOP_WORDS` / `RESUME_WORDS` toggle it.
- **Outbound** — `send_message`, `send_voice` (multipart upload of an OGG/Opus
  note), `send_photo`; all via the stdlib `urllib` (no `python-telegram-bot`
  dependency).

### Voice pipeline (Telegram)
```
incoming .oga voice note
   → download_file()
   → ffmpeg → 16 kHz mono WAV
   → SpeechRecognition (Google, ar-SA)
   → text prompt
   ...
 reply text → gTTS → mp3 ─┐
                 (espeak →  wav fallback)
                           └─ ffmpeg → OGG/Opus 48 kHz mono → sendVoice
```

---

## Voice-assistant specifics

- **Continuous loop** in `main()`: `buzz()` → `listen()` → turn → `speak()`.
- **`listen()`** calls `termux-speech-to-text`; a stop word ends the loop.
- **RTL handling** — `rtl_str()` uses the optional `rtl` helper (when present)
  to re-shape/reorder Arabic so it displays correctly in terminals that render
  it left-to-right.
- **Model override** — `ZYVO_VOICE_MODEL` lets you pick a speedier or smarter
  model per run.

---

## File / state map

| Path | Written by | Holds |
|------|-----------|-------|
| `~/.config/zyvo/telegram.env` | you | bot token + chat id (**secret**) |
| `~/.config/zyvo/telegram-session` | telegram bot | current session id |
| `~/.config/zyvo/voice-session` | voice assistant | current session id |
| `$TMPDIR/zyvo-telegram-server.log` | telegram bot | ZYVO server stdout |
| `$TMPDIR/zyvo-voice-server.log` | voice assistant | ZYVO server stdout |

---

## Extending it

- **New input channel?** (Slack, Matrix, a web UI) — you only need to produce a
  prompt string and a way to send a reply. Reuse `run_turn()` and the SSE
  listener.
- **New output?** — `build_and_send()` in the Telegram bot is the seam: text,
  voice, photos. Add a branch there.
- **Different brain?** — swap the `BASE` URL and the request shape; the report
  builders are transport-agnostic as long as the tool events are available.

---

Back to the [README](../README.md) · Next: [FAQ](FAQ.md)
