# FAQ & Troubleshooting

---

## ZYVO / server

### `Service not found: @opencode/Provider`
That is expected on some ZYVO builds and is **already handled**. Both scripts do
not use `zyvo run` / `zyvo serve`; they launch the full app under a PTY
(`script -qfc "zyvo --port N"`) and talk to its HTTP server. If you see this
message from a *manual* `zyvo run`, ignore it — the assistants don't need it.

### The server dies and the assistant restarts it constantly
Android is reclaiming the process. Make sure `termux-wake-lock` is active
(the scripts call it) and, for the Telegram bot, that it runs under
`termux-services` with a wake-lock. Also disable battery optimisation for both
**Termux** and **Termux:API** in Android settings.

### Port already in use
Two assistants use different ports by design (`4141` voice, `4142` telegram).
If something else owns one, change it:

```bash
ZYVO_VOICE_PORT=5151 zyvo-voice
ZYVO_TELEGRAM_PORT=5252 zyvo-telegram
```

### A turn hangs waiting for approval
It shouldn't — a background `PermissionWatchdog` auto-answers tool-permission
prompts. If it still hangs, the server may be wedged: send `توقف` to abort, or
restart the assistant.

---

## Voice / audio

### `termux-speech-to-text: command not found`
Install the packages **and** the app from the same source:

```bash
pkg install termux-api
# + install the Termux:API *app* from the SAME store as Termux (F-Droid or GitHub)
```

Then open Termux:API once and grant the microphone permission.

### The mic hears nothing / always empty
1. Android **Settings → Apps → Termux:API → Permissions → Microphone → Allow**.
2. If you use the Play Store build of Termux, switch to the F-Droid/GitHub build
   of **both** Termux and Termux:API — mixing stores breaks every `termux-*` call.

### No sound from TTS
- Confirm `pkg install termux-api` and the Termux:API app.
- Force the language: `ZYVO_VOICE_TTS_LANG=ar zyvo-voice`
- Test directly: `termux-tts-speak -l ar "اختبار"`

### TTS speaks the wrong language
Set `ZYVO_VOICE_TTS_LANG=ar` (or your language code). Without it, the device
default is used.

### Telegram voice notes sound robotic
That means `gTTS` isn't installed and `espeak` is being used. Install it:

```bash
pip install gTTS
```

### Telegram voice notes aren't transcribed
`SpeechRecognition` is missing (or there's no network — it uses Google's API):

```bash
pip install SpeechRecognition
pkg install ffmpeg     # required for the audio conversion
```

The assistant degrades gracefully: it replies in text if transcription fails.

---

## Arabic / display

### Arabic appears left-to-right / letters disconnected in the terminal
That's a **terminal** limitation, not the assistant's. Termux (≥ 0.118.2) renders
text strictly LTR, so Arabic looks reversed. `zyvo-voice.py` uses an optional
`rtl` helper to re-shape it for display. Install the helper or an Arabic-capable
font; see the ZYVO project's RTL notes. The Telegram app renders Arabic correctly
on its own — this only affects the Termux console.

---

## Telegram

### The bot never replies
- Check the token and chat id in `~/.config/zyvo/telegram.env`.
- The bot **only answers the configured chat id** — confirm `ZYVO_TELEGRAM_CHAT_ID`
  matches your chat.
- Run `zyvo-telegram --self-test` and `zyvo-telegram --send-test` to isolate it.

### It replied once, then went silent
You may have accidentally triggered **SAFE mode** (send `توقف`/`stop`). Send
`شغّل`/`resume` to re-enable execution, or `/status` to see the current mode.

### How do I stop it mid-task?
Send **`توقف`** (or `/stop`). It aborts the running turn and enters safe mode.

### `getUpdates` shows nothing
Send your bot a message first, *then* call `getUpdates`. If the bot is already
long-polling, Telegram won't deliver updates to a second consumer.

---

## Security

### Is my bot token safe?
Yes, if you follow the setup:
- It lives **only** in `~/.config/zyvo/telegram.env` (`chmod 600`).
- It is listed in `.gitignore` and is **never** printed by the scripts.
- Never paste it into a repo, an issue, or a chat. If it leaks, revoke it in
  [@BotFather](https://t.me/BotFather) → `/revoke` and write a new one.

### Does my data leave the device?
The assistants talk to a **local** server at `127.0.0.1`. Two features do use the
network: **gTTS** and **SpeechRecognition** both call Google, and of course the
model you drive in ZYVO may call its own provider. Everything else stays on the
phone.

---

## General

### Do I need root?
No. Neither assistant requires root.

### Does it run on desktop Linux/macOS?
The Telegram assistant largely does (it's pure Python + the local server). The
voice assistant needs the `termux-*` commands, which are Android-only.

### Which Python is required?
**3.11 or newer** (the code uses modern type hints and `X | Y` unions). The CI
tests 3.11, 3.12 and 3.13.

### How do I update?
```bash
cd tyb-zyv-ai
git pull
install -m755 zyvo-telegram.py "$PREFIX/bin/zyvo-telegram"
install -m755 zyvo-voice.py    "$PREFIX/bin/zyvo-voice"
```

---

Still stuck? Open an issue:
https://github.com/tibatechapp-ship-it/tyb-zyv-ai/issues

---

Back to the [README](../README.md)
