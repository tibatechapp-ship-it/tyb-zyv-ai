# Installation

Everything you need to run `zyvo-telegram.py` and `zyvo-voice.py` on an Android
phone with Termux. Follow the sections in order — each one builds on the last.

> **Time:** ~15 minutes. **Cost:** free.

---

## 0. Prerequisites

| Thing | Why |
|-------|-----|
| Android phone | The target device. Root is **not** required. |
| [Termux](https://termux.dev) | The Linux terminal. Install the **F-Droid** or **GitHub** build — *not* the Play Store one (it is outdated). |
| [Termux:API](https://github.com/termux/termux-api) app | Must be installed from the **same source** as Termux. |
| **ZYVO** | The AI brain both assistants talk to. See [ZYVO-AI](https://github.com/zyvo9/ZYVO-AI). |
| A Telegram bot token | Only for the Telegram assistant (step 5). |

> ⚠️ **Termux and Termux:API must come from the same store.** Mixing F-Droid
> Termux with a Play Store Termux:API breaks every `termux-*` command.

---

## 1. Update Termux and install core packages

```bash
pkg update && pkg upgrade -y
pkg install -y python termux-api ffmpeg espeak git
```

What each package gives you:

| Package | Provides |
|---------|----------|
| `python` | Python 3.11+ runtime |
| `termux-api` | `termux-tts-speak`, `termux-speech-to-text`, `termux-vibrate`, `termux-wake-lock` |
| `ffmpeg` | Audio conversion (OGG ↔ WAV ↔ MP3) for voice notes |
| `espeak` | Offline text-to-speech fallback |
| `git` | Clone / update the repo |

Verify Python is new enough:

```bash
python --version   # must print 3.11 or newer
```

---

## 2. Get the repository

```bash
git clone https://github.com/tibatechapp-ship-it/tyb-zyv-ai.git
cd tyb-zyv-ai
```

---

## 3. Python extras (recommended)

The assistants run on the **standard library alone**, but two optional packages
make the voice experience much better:

```bash
pip install -r requirements.txt
```

| Package | Effect if missing |
|---------|-------------------|
| `gTTS` | Telegram voice notes fall back to `espeak` (more robotic) |
| `SpeechRecognition` | Voice-note transcription is skipped (replies in text only) |

---

## 4. Install the scripts

Either copy them onto your `PATH`:

```bash
install -m755 zyvo-telegram.py "$PREFIX/bin/zyvo-telegram"
install -m755 zyvo-voice.py    "$PREFIX/bin/zyvo-voice"
```

…or just run them from the clone with `./zyvo-telegram.py`.

Add `$PREFIX/bin` to your `PATH` if it isn't already (Termux does this by default):

```bash
echo 'export PATH="$PREFIX/bin:$PATH"' >> ~/.bashrc
```

---

## 5. Telegram bot setup (only for `zyvo-telegram.py`)

### 5.1 Create a bot
1. Open Telegram, talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, give it a name and a username.
3. Copy the **token** it gives you (`123456:ABC…`).

### 5.2 Find your chat ID
Send your new bot any message, then:

```bash
curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | grep -o '"chat":{"id":[0-9-]*'
```

The number is your **chat ID**.

### 5.3 Write the config file

```bash
mkdir -p ~/.config/zyvo
cat > ~/.config/zyvo/telegram.env <<'EOF'
TELEGRAM_BOT_TOKEN=123456:ABC-your-real-token
ZYVO_TELEGRAM_CHAT_ID=123456789
EOF
chmod 600 ~/.config/zyvo/telegram.env
```

> 🔒 `chmod 600` matters — this file holds your secret token. It is `.gitignore`d.

### 5.4 Verify

```bash
zyvo-telegram --self-test     # checks server + TTS + STT, then exits
zyvo-telegram --send-test     # sends a startup message to your chat, exits
```

---

## 6. Grant permissions (first run)

When you first use voice features, Android will ask for:

- **Microphone** — for `termux-speech-to-text`.
- **Notifications / foreground** — for the wake-lock service.

Grant both. On Android 13+, the Termux notification permission is required for
the wake-lock notification to appear.

---

## 7. Run as a background service (optional, Telegram only)

Install `termux-services` and create a service so the bot survives reboots:

```bash
pkg install -y termux-services
```

Create `$PREFIX/var/service/zyvo-telegram/run`:

```sh
#!/data/data/com.termux/files/usr/bin/sh
export HOME="$HOME"
export PATH="$HOME/bin:$PREFIX/bin:$PATH"
exec zyvo-telegram 2>&1
```

```bash
chmod +x "$PREFIX/var/service/zyvo-telegram/run"
sv-enable zyvo-telegram
sv up zyvo-telegram
```

> **Important:** export `HOME` and put `$HOME/bin` on `PATH`, exactly as above.
> The assistants spawn ZYVO as a child process; without the right `PATH` it will
> not find the `zyvo` binary.

### Auto-start on boot
Install the [Termux:Boot](https://github.com/termux/termux-boot) app, then create
`~/.termux/boot/start-zyvo`:

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
. "$PREFIX/etc/profile"
sv-enable zyvo-telegram
```

```bash
chmod +x ~/.termux/boot/start-zyvo
```

---

## 7b. Voice assistant (no Telegram needed)

`zyvo-voice.py` needs no token. Just make sure ZYVO is installed and run:

```bash
zyvo-voice --text "hello"   # quick smoke test
zyvo-voice                  # full voice loop
```

---

## Troubleshooting

See [`FAQ.md`](FAQ.md). The two most common:

- **`Service not found: @opencode/Provider`** — expected on some ZYVO builds;
  both scripts already work around it (they launch the full app under a PTY).
- **Mic does nothing** — Termux:API app missing, wrong store, or the mic
  permission was denied. Re-check step 6.

---

Back to the [README](../README.md) · Next: [USAGE](USAGE.md)
