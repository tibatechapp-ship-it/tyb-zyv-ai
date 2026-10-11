# Usage

Two assistants, one brain. Pick whichever fits the moment — or run both.

---

## `zyvo-telegram.py` — Telegram bridge

### Start it

```bash
zyvo-telegram
```

It prints `polling as @YourBot` and keeps running. Leave it in the foreground, or
install it as a service (see [INSTALLATION](INSTALLATION.md#6-grant-permissions-first-run)).

### Talk to it

Just send a message. Text or voice — both work.

```
you →  وش الملفات الكبيرة في الhome؟
bot →  📄 تقرير + 🔊 صوت + 🖼 صور (إن وُجدت)
```

<details>
<summary>What comes back</summary>

1. **Text report** (Arabic) — what it understood, which actions ran, success/failure, files changed, plus the answer.
2. **Voice note** (Arabic) — the same report spoken (gTTS; `espeak` fallback).
3. **Photos** — any images the turn produced.

</details>

### Control words

| You send | Effect |
|----------|--------|
| `/new` | Start a fresh conversation (new session). |
| `/status` | Show current mode and session state. |
| `توقف` `وقف` `خلاص` `كفاية` `stop` `abort` | **Abort** the running task and switch to **SAFE mode** — it will chat but no longer execute tools. |
| `شغّل` `كمل` `متابعة` `resume` `continue` `start` | Leave safe mode and **re-enable execution**. |
| `/help` | Show the built-in help. |

> 🛑 **Safe mode is your panic button.** Anything destructive in flight? Send
> `توقف` and the assistant stops executing and just talks to you.

### Standalone modes (testing / scripting)

```bash
zyvo-telegram --send-test     # send a startup line to the chat, then exit
zyvo-telegram --self-test     # check HTTP server + TTS + STT, then exit
```

### Environment

| Var | Meaning | Default |
|-----|---------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token (else read from the env file) | — |
| `ZYVO_TELEGRAM_CHAT_ID` | Only this chat is answered | — |
| `ZYVO_TELEGRAM_PORT` | Local ZYVO server port | `4142` |

Config file: `~/.config/zyvo/telegram.env` (chmod `600`).

---

## `zyvo-voice.py` — voice assistant

### The loop

```bash
zyvo-voice
```

```
📳 vibrate → 🎙 listen → 🧠 ZYVO executes → 🔊 Arabic report → repeat
```

Say a stop word (**`stop` / `توقف` / `خلاص` / `كفاية` / `bye`**) or press
`Ctrl+C` to exit.

### Flags

| Flag | What it does |
|------|--------------|
| *(none)* | Full **ACTION** mode — ZYVO really runs commands and edits files. |
| `--chat` | Chat only: the assistant answers but **never executes tools**. |
| `--text "…"` | Send one typed command and exit (great for scripts/cron). |
| `--new` | Start a fresh session instead of resuming the last one. |
| `--no-mic` | Type instead of speaking (still loops). |
| `--no-speak` | Silent mode — no TTS at all. |
| `--no-report` | Speak only the final answer, skip the action report. |

### Environment

| Var | Meaning | Default |
|-----|---------|---------|
| `ZYVO_VOICE_PORT` | Local ZYVO server port | `4141` |
| `ZYVO_VOICE_TTS_LANG` | Force TTS language (e.g. `ar`) | device default |
| `ZYVO_VOICE_MODEL` | Model id, e.g. `opencode/big-pickle` | ZYVO default |

### The action report

In ACTION mode every spoken reply is preceded by a structured report:

- **شو فهمت** — what it understood from your words
- **الأوامر** — the exact commands/tools it ran
- **النتيجة** — success or failure, per step
- **الملفات** — files created or changed
- then the **answer**

This data is read from the server's real tool events — it is *not* invented.

---

## Recipes

**One-shot from a script / cron**
```bash
zyvo-voice --text "check disk usage and warn if >90%" --no-speak
```

**Safe public demo (no execution)**
```bash
zyvo-voice --chat
```

**Different model for one run**
```bash
ZYVO_VOICE_MODEL=opencode/big-pickle zyvo-voice
```

**Run the Telegram bot in the background manually**
```bash
nohup zyvo-telegram >/dev/null 2>&1 &
```

**Termux notification when a turn finishes** (customise the script's
`build_and_send`/report step with `termux-notification`).

---

## Both at once?

Yes — they use **different ports** (`4141` vs `4142`) and **different session
files** (`voice-session` vs `telegram-session`), so they never collide. Each
spawns and manages its own ZYVO server.

---

Back to the [README](../README.md) · Next: [ARCHITECTURE](ARCHITECTURE.md)
