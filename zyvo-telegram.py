#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zyvo-telegram — a Telegram bot that drives ZYVO.

Receives text OR voice messages on Telegram, sends them to ZYVO (the local HTTP
brain built on the PTY server), then replies on Telegram with:
  * a TEXT report in Arabic (what it understood / actions / files / answer),
  * a VOICE note in Arabic (TTS),
  * any images ZYVO produced (photos).

Runs forever as a background service (termux-services). Uses termux-wake-lock so
Android does not suspend it. Safety: send "توقف" / /stop to abort the running
task and switch to SAFE mode (chat only, no execution); "شغّل" / /resume to
re-enable execution.

Config: ~/.config/zyvo/telegram.env   (chmod 600)
    TELEGRAM_BOT_TOKEN=...
    ZYVO_TELEGRAM_CHAT_ID=...        # only this chat is answered

Standalone modes (for testing):
    ./zyvo-telegram.py --send-test    # send a startup message to the chat, exit
    ./zyvo-telegram.py --self-test    # check server + tts + stt, exit
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOME = os.path.expanduser("~")
ENV_FILE = os.path.join(HOME, ".config", "zyvo", "telegram.env")
STATE_DIR = os.path.join(HOME, ".config", "zyvo")
STATE_FILE = os.path.join(STATE_DIR, "telegram-session")
WORK_DIR = os.path.join(os.environ.get("TMPDIR", HOME), "zyvo-tg")
LOG_SERVER = os.path.join(os.environ.get("TMPDIR", "/tmp"), "zyvo-telegram-server.log")

PORT = int(os.environ.get("ZYVO_TELEGRAM_PORT", "4142"))
BASE = f"http://127.0.0.1:{PORT}"

STOP_WORDS = {"توقف", "وقف", "خلاص", "كفاية", "انتهى", "stop", "abort"}
RESUME_WORDS = {"شغل", "شغّل", "كمل", "كمل", "متابعة", "resume", "continue", "start"}
HELP_TEXT = (
    "🤖 *بوت ZYVO*\n\n"
    "ابعتلي أي أمر نصّي أو رسالة صوتية وأنفّذه، وببعتلك:\n"
    "• تقرير نصّي بالعربي\n• رسالة صوتية بالعربي\n• صور إذا لزم\n\n"
    "أوامر:\n"
    "/new — محادثة جديدة\n"
    "/status — الحالة والوضع\n"
    "توقف أو /stop — إيقاف التنفيذ (وضع آمن)\n"
    "شغّل أو /resume — استئناف التنفيذ\n"
)


def load_env() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


CFG: dict[str, str] = load_env()
TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN") or CFG.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT: str = str(os.environ.get("ZYVO_TELEGRAM_CHAT_ID")
                        or CFG.get("ZYVO_TELEGRAM_CHAT_ID", "")).strip()
API: str = f"https://api.telegram.org/bot{TOKEN}"

# Modes
PAUSED: bool = False            # True => SAFE mode (no execution, chat only)
CURRENT_SID: str | None = None
RUNNING_TURN: bool = False

# Tool parts are NOT persisted in this build's message history; they only flow
# on the /event SSE stream. PARTS holds the latest state per tool part id.
PARTS: dict[str, dict[str, Any]] = {}
PARTS_LOCK: threading.Lock = threading.Lock()

os.makedirs(WORK_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Telegram API (urllib only — no extra deps)
# --------------------------------------------------------------------------- #
def _request(url: str, data: bytes | None = None,
             headers: dict[str, str] | None = None,
             timeout: int = 60) -> Any:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def tg(method: str, params: dict[str, Any] | None = None,
       timeout: int = 60) -> dict[str, Any]:
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    res = _request(url, data=data, timeout=timeout)
    if not res.get("ok"):
        print(f"tg {method} error: {res}", flush=True)
    return res


def _multipart(fields: Mapping[str, Any],
               files: Mapping[str, tuple[str, bytes, str]]) -> tuple[str, bytes]:
    boundary = "----zyvo" + str(random.randint(10**9, 10**10))
    out = b""
    for k, v in fields.items():
        out += b"--" + boundary.encode() + b"\r\n"
        out += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        out += str(v).encode("utf-8") + b"\r\n"
    for k, (fn, blob, ctype) in files.items():
        out += b"--" + boundary.encode() + b"\r\n"
        out += f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'.encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += blob + b"\r\n"
    out += b"--" + boundary.encode() + b"--\r\n"
    return boundary, out


def tg_upload(method: str, fields: dict[str, Any], file_field: str, path: str,
              ctype: str, timeout: int = 120) -> dict[str, Any]:
    blob = open(path, "rb").read()
    boundary, body = _multipart(fields, {file_field: (os.path.basename(path), blob, ctype)})
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    res = _request(f"{API}/{method}", data=body, headers=headers, timeout=timeout)
    if not res.get("ok"):
        print(f"tg {method} upload error: {res}", flush=True)
    return res


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
    text = text or ""
    for i in range(0, len(text), 4000):        # Telegram limit 4096
        chunk = text[i:i + 4000]
        try:
            tg("sendMessage", {"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode})
        except Exception:
            tg("sendMessage", {"chat_id": chat_id, "text": chunk})


def send_voice(chat_id: str, path: str, caption: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return tg_upload("sendVoice", fields, "voice", path, "audio/ogg")


def send_photo(chat_id: str, path: str, caption: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return tg_upload("sendPhoto", fields, "photo", path, "image/png")


def send_action(chat_id: str, action: str = "typing") -> None:
    try:
        tg("sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception:
        pass


def download_file(file_id: str, dest: str) -> str | None:
    info = tg("getFile", {"file_id": file_id})
    if not info.get("ok"):
        return None
    fp = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TOKEN}/{fp}"
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


# --------------------------------------------------------------------------- #
# ZYVO server (PTY launch — the CLI run/serve are broken on this build)
# --------------------------------------------------------------------------- #
_PROC: subprocess.Popen[bytes] | None = None


def api(method: str, path: str, body: Any = None, timeout: int = 600) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


def server_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/session", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def kill_tree(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    for _ in range(10):
        if proc.poll() is not None:
            return
        time.sleep(0.3)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass


def start_server() -> subprocess.Popen[bytes]:
    import shutil
    if shutil.which("script") is None:
        raise RuntimeError("missing 'script' (pkg install util-linux)")
    logf = open(LOG_SERVER, "w")
    proc = subprocess.Popen(
        ["script", "-qec", f"zyvo --port {PORT} --print-logs", "/dev/null"],
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(60):
        if server_up():
            return proc
        if proc.poll() is not None:
            break
        time.sleep(1)
    kill_tree(proc)
    raise RuntimeError("ZYVO server did not come up")


def ensure_server() -> subprocess.Popen[bytes] | None:
    global _PROC
    if server_up():
        return _PROC
    if _PROC is not None and _PROC.poll() is None:
        for _ in range(15):
            if server_up():
                return _PROC
            time.sleep(1)
    if _PROC is not None:
        kill_tree(_PROC)
    for attempt in range(1, 4):
        try:
            _PROC = start_server()
            return _PROC
        except Exception as e:
            print("ensure_server:", e, flush=True)
            time.sleep(3)
    return None


def get_session(force_new: bool = False) -> str:
    if not force_new and os.path.exists(STATE_FILE):
        sid = open(STATE_FILE).read().strip()
        if sid:
            try:
                if api("GET", f"/session/{sid}", timeout=10):
                    return sid
            except Exception:
                pass
    sid = api("POST", "/session", {"title": "ZYVO Telegram"})["id"]
    os.makedirs(STATE_DIR, exist_ok=True)
    open(STATE_FILE, "w").write(sid)
    return sid


# --------------------------------------------------------------------------- #
# SSE event listener — the only place tool parts appear in this build
# --------------------------------------------------------------------------- #
def sse_listener() -> None:
    while True:
        try:
            req = urllib.request.Request(BASE + "/event")
            with urllib.request.urlopen(req, timeout=180) as r:
                for raw in r:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except Exception:
                        continue
                    if ev.get("type") == "message.part.updated":
                        part = (ev.get("properties") or {}).get("part") or {}
                        if part.get("type") == "tool" and part.get("id"):
                            with PARTS_LOCK:
                                PARTS[part["id"]] = part
        except Exception as e:
            print("sse_listener reconnect:", e, flush=True)
            time.sleep(3)


# --------------------------------------------------------------------------- #
# Permission watchdog (respects PAUSED => reject)
# --------------------------------------------------------------------------- #
class PermissionWatchdog(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop: bool = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            sid = CURRENT_SID
            action = "reject" if PAUSED else "always"
            if sid:
                pending = []
                for path in (f"/session/{sid}/permission", "/permission"):
                    try:
                        p = api("GET", path, timeout=3)
                        if isinstance(p, list):
                            pending += p
                    except Exception:
                        pass
                for item in pending:
                    rid = (item.get("id") or item.get("requestID")
                           or item.get("permissionID") or item.get("requestId"))
                    if not rid:
                        continue
                    for rpath in (f"/session/{sid}/permission/{rid}/reply",
                                  f"/permission/{rid}/reply"):
                        try:
                            api("POST", rpath, {"reply": action}, timeout=3)
                            break
                        except Exception:
                            continue
            time.sleep(0.5)


# --------------------------------------------------------------------------- #
# Turn execution + report
# --------------------------------------------------------------------------- #
ARABIC_VERB = {
    "bash": "نفّذت أمر", "shell": "نفّذت أمر", "write": "كتبت ملف",
    "edit": "عدّلت ملف", "patch": "طبّقت تعديل", "read": "قرأت ملف",
    "glob": "بحثت عن ملفات", "grep": "بحثت داخل الملفات", "list": "استعرضت مجلد",
    "webfetch": "جلبت صفحة", "fetch": "جلبت صفحة", "task": "شغّلت مهمة",
}
FILE_TOOLS = {"write", "edit", "patch"}
SKIP_TOOLS = {"todowrite", "todoread"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _short(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def action_detail(inp: Any) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in ("command", "filePath", "filepath", "path", "pattern", "url", "query", "description"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def collect_turn_tools(sid: str, pre_ids: set[str]) -> list[dict[str, Any]]:
    with PARTS_LOCK:
        parts = list(PARTS.values())
    tools: list[dict[str, Any]] = []
    for p in parts:
        if p.get("id") in pre_ids:
            continue
        if sid and p.get("sessionID") != sid:
            continue
        tools.append(p)
    return tools


def parse_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    actions: list[dict[str, Any]] = []
    created: list[str] = []
    modified: list[str] = []
    for t in tools:
        name = t.get("tool", "?")
        if name in SKIP_TOOLS:
            continue
        st = t.get("state", {}) or {}
        inp = st.get("input", {}) or {}
        status = st.get("status", "")
        ok = status == "completed"
        err = st.get("error") or ""
        meta = st.get("metadata") or {}
        if name in ("bash", "shell") and isinstance(meta.get("exit"), int) and meta.get("exit") != 0:
            ok = False
            err = err or (st.get("output") or "")[:200]
        actions.append({"tool": name, "detail": action_detail(inp),
                        "ok": ok, "error": err})
        if name in FILE_TOOLS:
            fp = inp.get("filePath") or inp.get("filepath") or inp.get("path")
            if fp:
                if name == "write" and meta.get("exists") is False:
                    created.append(fp)
                else:
                    modified.append(fp)
    return actions, created, modified


def run_turn(sid: str, text: str) -> tuple[str, list[dict[str, Any]]]:
    with PARTS_LOCK:
        pre_ids = set(PARTS.keys())
    reply: str = ""
    for attempt in range(1, 4):
        try:
            msg = api("POST", f"/session/{sid}/message",
                      {"parts": [{"type": "text", "text": text}]}, timeout=1800)
            parts = msg.get("parts", []) if isinstance(msg, dict) else []
            reply = "\n".join(p.get("text", "") for p in parts
                              if p.get("type") == "text" and not p.get("synthetic")).strip()
            break
        except Exception as e:
            print(f"run_turn error ({attempt}/3): {e}", flush=True)
            if ensure_server() is None:
                time.sleep(3)
            else:
                time.sleep(1)
    time.sleep(1.0)                     # let trailing SSE tool events land
    tools = collect_turn_tools(sid, pre_ids)
    return reply, tools


def text_report(heard: str, reply: str, actions: list[dict[str, Any]],
                created: list[str], modified: list[str]) -> str:
    lines = [f"🎙 *فهمت منك:*\n{heard}", ""]
    lines.append(f"🛠 *الإجراءات ({len(actions)}):*")
    if not actions:
        lines.append("— ما نفّذت أي إجراء خارجي —")
    for a in actions:
        verb = ARABIC_VERB.get(a["tool"], "استخدمت أداة")
        mark = "✅" if a["ok"] else "❌"
        lines.append(f"{mark} {verb}:")
        if a["detail"]:
            lines.append(f"`{_short(a['detail'], 200)}`")
        if not a["ok"] and a["error"]:
            lines.append(f"⚠️ السبب: {_short(a['error'], 200)}")
    lines.append("")
    lines.append(f"📄 *الملفات:* أنشأت {len(created)}، عدّلت {len(modified)}")
    for f in created:
        lines.append(f"➕ `{f}`")
    for f in modified:
        lines.append(f"✏️ `{f}`")
    lines.append("")
    lines.append(f"🤖 *الرد:*\n{reply or '—'}")
    return "\n".join(lines)


def spoken_report(heard: str, reply: str, actions: list[dict[str, Any]],
                  created: list[str], modified: list[str]) -> str:
    ok_all = all(a["ok"] for a in actions) if actions else True
    out = [f"فهمت منك: {heard}."]
    if actions:
        out.append(f"نفذت {len(actions)} إجراء.")
        for i, a in enumerate(actions, 1):
            verb = ARABIC_VERB.get(a["tool"], "استخدمت أداة")
            d = _short(a["detail"], 120)
            out.append(f"الإجراء {i}: {verb}: {d}. " + ("نجح." if a["ok"] else f"فشل. {_short(a['error'],100)}"))
    else:
        out.append("ما نفذت أي إجراء، جاوبت فقط.")
    if created:
        out.append("أنشأت الملفات: " + "، ".join(created) + ".")
    if modified:
        out.append("عدلت الملفات: " + "، ".join(modified) + ".")
    out.append("النتيجة العامة: " + ("نجحت كل الإجراءات." if ok_all else "فيه إجراءات فشلت."))
    if reply:
        out.append("الرد: " + reply)
    return " ".join(out)


# --------------------------------------------------------------------------- #
# Speech: STT (voice in) and TTS (voice out)
# --------------------------------------------------------------------------- #
def clean_for_speech(t: str) -> str:
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[*_#>|~^]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def make_voice_note(text: str, out_ogg: str, lang: str = "ar") -> str | None:
    """Text -> OGG/Opus voice note. gTTS first, espeak fallback."""
    txt = clean_for_speech(text)
    if not txt:
        return None
    mp3 = out_ogg + ".mp3"
    wav = out_ogg + ".wav"
    ok: bool = False
    try:
        from gtts import gTTS
        gTTS(txt[:4000], lang=lang).save(mp3)
        ok = True
    except Exception as e:
        print("gTTS failed:", e, flush=True)
    if not ok:
        try:
            subprocess.run(["espeak", "-v", "ar", "-w", wav, txt[:4000]], check=True)
        except Exception as e:
            print("espeak failed:", e, flush=True)
            return None
    src: str = mp3 if ok else wav
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
                        out_ogg], check=True)
        return out_ogg if os.path.getsize(out_ogg) > 0 else None
    except Exception as e:
        print("ffmpeg voice failed:", e, flush=True)
        return None


def transcribe(ogg_path: str, lang: str = "ar-SA") -> str:
    """OGG/audio file -> text (Arabic) via SpeechRecognition + Google."""
    wav = ogg_path + ".16k.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ogg_path,
                        "-ar", "16000", "-ac", "1", wav], check=True)
    except Exception as e:
        print("ffmpeg stt failed:", e, flush=True)
        return ""
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            audio = r.record(src)
        return r.recognize_google(audio, language=lang).strip()
    except Exception as e:
        print("STT failed:", e, flush=True)
        return ""


# --------------------------------------------------------------------------- #
# Message handling
# --------------------------------------------------------------------------- #
WORKQ: queue.Queue[tuple[str, str]] = queue.Queue()


def build_and_send(chat_id: str, heard: str, reply: str,
                   tools: list[dict[str, Any]]) -> None:
    actions, created, modified = parse_tools(tools)
    send_message(chat_id, text_report(heard, reply, actions, created, modified))
    # voice note
    vpath = os.path.join(WORK_DIR, f"voice_{int(time.time())}.ogg")
    spoken = spoken_report(heard, reply, actions, created, modified)
    if make_voice_note(spoken, vpath):
        send_action(chat_id, "record_voice")
        send_voice(chat_id, vpath)
    # images
    imgs = [f for f in (created + modified) if f.lower().endswith(IMAGE_EXT) and os.path.exists(f)]
    for img in imgs[:5]:
        try:
            send_photo(chat_id, img, caption=os.path.basename(img))
        except Exception as e:
            print("sendPhoto failed:", e, flush=True)


def worker() -> None:
    global RUNNING_TURN, CURRENT_SID
    while True:
        chat_id, text = WORKQ.get()
        try:
            RUNNING_TURN = True
            if ensure_server() is None:
                send_message(chat_id, "⚠️ تعذّر تشغيل سيرفر ZYVO.")
                continue
            CURRENT_SID = get_session()
            send_action(chat_id, "typing")
            reply, tools = run_turn(CURRENT_SID, text)
            build_and_send(chat_id, text, reply, tools)
        except Exception as e:
            send_message(chat_id, f"⚠️ خطأ: {_short(str(e), 300)}")
        finally:
            RUNNING_TURN = False
            WORKQ.task_done()


def handle_control(chat_id: str, text: str) -> bool:
    """Return True if handled as a control command."""
    global PAUSED, CURRENT_SID
    low = text.strip().lower()
    if low in ("/start", "/help", "مساعدة", "/?"):
        send_message(chat_id, HELP_TEXT)
        return True
    if low in ("/status", "حالة"):
        mode = "وضع آمن (بدون تنفيذ)" if PAUSED else "وضع التنفيذ الكامل"
        send_message(chat_id, f"📊 الحالة:\n• الوضع: {mode}\n• الجلسة: `{CURRENT_SID or '—'}`\n• قائمة الانتظار: {WORKQ.qsize()}")
        return True
    if low in ("/new", "جلسة جديدة"):
        try:
            sid = get_session(force_new=True)
            CURRENT_SID = sid
            send_message(chat_id, f"🆕 جلسة جديدة: `{sid}`")
        except Exception as e:
            send_message(chat_id, f"⚠️ {e}")
        return True
    if low == "/stop" or low in STOP_WORDS:
        PAUSED = True
        # abort any running turn
        try:
            if CURRENT_SID:
                api("POST", f"/session/{CURRENT_SID}/abort", {}, timeout=10)
        except Exception:
            pass
        send_message(chat_id, "✋ *تم الإيقاف* — التنفيذ موقوف (وضع آمن).\nاكتب *شغّل* أو /resume للمتابعة.")
        return True
    if low == "/resume" or low in RESUME_WORDS:
        PAUSED = False
        send_message(chat_id, "▶️ *تم الاستئناف* — وضع التنفيذ الكامل رجع.")
        return True
    return False


def poll_loop() -> None:
    print(f"zyvo-telegram: polling as @{(tg('getMe').get('result') or {}).get('username')}", flush=True)
    offset = 0
    while True:
        try:
            res = tg("getUpdates", {"offset": offset, "timeout": 30,
                                    "allowed_updates": json.dumps(["message"])}, timeout=45)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id"))
                if ALLOWED_CHAT and chat_id != ALLOWED_CHAT:
                    print(f"ignoring chat {chat_id}", flush=True)
                    continue
                text = msg.get("text") or ""
                voice = msg.get("voice") or msg.get("audio")
                if text:
                    if handle_control(chat_id, text):
                        continue
                    WORKQ.put((chat_id, text))
                    send_action(chat_id, "typing")
                elif voice:
                    send_action(chat_id, "typing")
                    src = os.path.join(WORK_DIR, f"in_{upd['update_id']}.oga")
                    download_file(voice["file_id"], src)
                    heard = transcribe(src)
                    if not heard:
                        send_message(chat_id, "🎙 ما قدرت أفهم الصوت (تأكد من الإنترنت).")
                        continue
                    send_message(chat_id, f"🎙 سمعتك: {heard}")
                    if handle_control(chat_id, heard):
                        continue
                    WORKQ.put((chat_id, heard))
                # photos / other ignored
        except urllib.error.URLError as e:
            print("poll network error:", e, flush=True)
            time.sleep(5)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(3)


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #
def self_test() -> None:
    print("token:", "set" if TOKEN else "MISSING")
    print("allowed chat:", ALLOWED_CHAT or "(any)")
    print("server:", "up" if server_up() else "down")
    for tool in ("ffmpeg", "espeak", "script"):
        print(tool, ":", "ok" if subprocess.run(["sh", "-c", f"command -v {tool}"],
                                                 capture_output=True).returncode == 0 else "missing")
    v = os.path.join(WORK_DIR, "selftest.ogg")
    print("tts:", "ok" if make_voice_note("اختبار الصوت من زيڨو", v) else "FAIL")


def main() -> None:
    if not TOKEN:
        sys.exit(f"Missing TELEGRAM_BOT_TOKEN (put it in {ENV_FILE})")
    try:
        subprocess.Popen(["termux-wake-lock"])
    except Exception:
        pass

    if "--send-test" in sys.argv:
        tg("sendMessage", {"chat_id": ALLOWED_CHAT,
                           "text": "✅ بوت ZYVO شغال وجاهز. ابعتلي أي أمر."})
        return
    if "--self-test" in sys.argv:
        self_test()
        return

    if ensure_server() is None:
        print("WARNING: ZYVO server not up yet; will retry on first message", flush=True)
    get_session()                     # ensure a session exists
    PermissionWatchdog().start()
    threading.Thread(target=sse_listener, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    poll_loop()


if __name__ == "__main__":
    main()
