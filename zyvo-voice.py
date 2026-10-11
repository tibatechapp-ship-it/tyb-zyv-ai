#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zyvo-voice.py — a complete, always-on VOICE assistant for Termux/Android
with FULL ACTION MODE and a detailed Arabic action report.

    termux-speech-to-text  ->  ZYVO (local HTTP brain)  ->  termux-tts-speak

Continuous loop: vibrate -> listen -> ZYVO EXECUTES what you asked (bash, file
writes/edits, ...) -> the script reads you a detailed Arabic report of every
action (what it understood, what it ran, success/failure, files changed) plus
the answer -> listens again. Runs until you say a stop word or press Ctrl+C.

Design notes (why it NEVER freezes):
  * `zyvo run` / `zyvo serve` are broken on this build ("Service not found:
    @opencode/Provider"), so we launch the full app `zyvo --port P` under a PTY
    and talk to its HTTP server.
  * A background PermissionWatchdog auto-answers any tool-permission prompt, so
    a turn can never block waiting for approval. (This build auto-approves too.)
  * Before each turn the server is health-checked and restarted if it died; the
    chat session is remembered on disk across restarts. 3 retries per request.
  * The action report is built from real tool data in the session history
    (GET /session/{id}/message -> parts of type "tool"), not guessed.

Usage:
    ./zyvo-voice.py                 # FULL ACTION mode, voice loop, Arabic report
    ./zyvo-voice.py --chat          # chat only (no tool execution)
    ./zyvo-voice.py --no-mic        # type instead of speak (still loops)
    ./zyvo-voice.py --no-speak      # silent (no TTS)
    ./zyvo-voice.py --no-report     # speak only the answer, not the action report
    ./zyvo-voice.py --text "..."    # one typed command, then exit
    ./zyvo-voice.py --new           # start a fresh session

Env knobs:
    ZYVO_VOICE_PORT=4141        server port
    ZYVO_VOICE_TTS_LANG=ar      force TTS language (else device default)
    ZYVO_VOICE_MODEL=           e.g. "opencode/big-pickle" (else ZYVO default)

Stop words: stop / exit / quit / bye / توقف / خلاص / كفاية / انتهى
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOST: str = "127.0.0.1"
PORT: int = int(os.environ.get("ZYVO_VOICE_PORT", "4141"))
BASE: str = f"http://{HOST}:{PORT}"
STATE_DIR: str = os.path.expanduser("~/.config/zyvo")
STATE_FILE: str = os.path.join(STATE_DIR, "voice-session")
LOG: str = os.path.join(os.environ.get("TMPDIR", "/tmp"), "zyvo-voice-server.log")

# Full action mode by default; --chat flips this to "reject".
ACTION: str = "always"
TTS_LANG: str = os.environ.get("ZYVO_VOICE_TTS_LANG", "")
MODEL_ENV: str = os.environ.get("ZYVO_VOICE_MODEL", "")

STOP_WORDS: set[str] = {
    "stop", "exit", "quit", "bye", "goodbye", "stop listening", "that's all",
    "توقف", "قف", "خلاص", "كفاية", "انتهى", "انتهينا", "وداعا", "سلام", "مع السلامة",
}
STOP_WORDS = {w.lower().strip() for w in STOP_WORDS}

_PROC: subprocess.Popen[bytes] | None = None
_RTL: str = os.path.expanduser("~/bin/rtl")
if not os.path.exists(_RTL):
    _RTL = shutil.which("rtl") or ""

# ANSI colours (Termux supports them)
C_RESET, C_DIM, C_BOLD = "\033[0m", "\033[2m", "\033[1m"
C_CYAN, C_GREEN, C_RED, C_YEL, C_MAG, C_BLUE = (
    "\033[36m", "\033[32m", "\033[31m", "\033[33m", "\033[35m", "\033[34m")


# --------------------------------------------------------------------------- #
# Terminal display (Arabic through ~/bin/rtl; colour applied AFTER reordering so
# bidi never sees the escape codes)
# --------------------------------------------------------------------------- #
def has_arabic(s: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in s)


def rtl_str(text: str) -> str:
    if not text or not has_arabic(text) or not _RTL:
        return text
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            continue
        try:
            r = subprocess.run([_RTL], input=line, capture_output=True,
                               text=True, timeout=5)
            fixed = (r.stdout or "").rstrip("\n")
            out.append(fixed if fixed else line)
        except Exception:
            out.append(line)
    return "\n".join(out)


def show(line: str = "", color: str = "") -> None:
    s = rtl_str(line)
    if color:
        s = color + s + C_RESET
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def rule(ch: str = "─", color: str = C_DIM) -> None:
    show(ch * 46, color)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def api(method: str, path: str, body: Any = None, timeout: int = 600) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {raw[:300]}")


def server_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/session", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #
def start_server() -> subprocess.Popen[bytes]:
    if shutil.which("script") is None:
        sys.exit("Missing 'script' (util-linux). Run: pkg install util-linux")
    logf = open(LOG, "w")
    proc = subprocess.Popen(
        ["script", "-qec", f"zyvo --port {PORT} --print-logs", "/dev/null"],
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("zyvo-voice: starting ZYVO server ...", flush=True)
    for _ in range(60):
        if server_up():
            return proc
        if proc.poll() is not None:
            break
        time.sleep(1)
    kill_tree(proc)
    raise RuntimeError(f"ZYVO server did not come up on port {PORT}. See {LOG}")


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
        print(f"zyvo-voice: (re)starting server (try {attempt}) ...", flush=True)
        try:
            _PROC = start_server()
            return _PROC
        except Exception as e:
            print("zyvo-voice:", e, flush=True)
            time.sleep(2)
    return None


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
def get_session(force_new: bool = False) -> str:
    if not force_new and os.path.exists(STATE_FILE):
        sid = open(STATE_FILE).read().strip()
        if sid:
            try:
                if api("GET", f"/session/{sid}", timeout=10):
                    return sid
            except Exception:
                pass
    body: dict[str, Any] = {"title": "ZYVO Voice"}
    if MODEL_ENV and "/" in MODEL_ENV:
        prov, mid = MODEL_ENV.split("/", 1)
        body["model"] = {"providerID": prov, "id": mid}
    sid = api("POST", "/session", body)["id"]
    os.makedirs(STATE_DIR, exist_ok=True)
    open(STATE_FILE, "w").write(sid)
    return sid


# --------------------------------------------------------------------------- #
# Permission watchdog (anti-freeze)
# --------------------------------------------------------------------------- #
class PermissionWatchdog(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop: bool = False
        self.sid: str | None = None

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            sid = self.sid
            if not sid:
                time.sleep(0.5)
                continue
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
                        api("POST", rpath, {"reply": ACTION}, timeout=3)
                        print(f"zyvo-voice: permission {rid} -> {ACTION}", flush=True)
                        break
                    except Exception:
                        continue
            time.sleep(0.5)


# --------------------------------------------------------------------------- #
# Turn execution + action report
# --------------------------------------------------------------------------- #
ARABIC_VERB = {
    "bash": "نفّذت أمر", "shell": "نفّذت أمر", "write": "كتبت ملف",
    "edit": "عدّلت ملف", "patch": "طبّقت تعديل", "read": "قرأت ملف",
    "glob": "بحثت عن ملفات", "grep": "بحثت داخل الملفات", "list": "استعرضت مجلد",
    "webfetch": "جلبت صفحة", "fetch": "جلبت صفحة", "task": "شغّلت مهمة",
    "todowrite": "حدّثت المهام", "todoread": "قرأت المهام",
}
FILE_TOOLS = {"write", "edit", "patch"}
SKIP_TOOLS = {"todowrite", "todoread", "todoread", "step-start", "step-finish"}
FILE_ARG_KEYS = ("filePath", "filepath", "path", "filename", "file")


def action_detail(inp: Any) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in ("command",) + FILE_ARG_KEYS + ("pattern", "url", "query", "description"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def session_message_count(sid: str) -> int:
    try:
        msgs = api("GET", f"/session/{sid}/message", timeout=30)
        return len(msgs) if isinstance(msgs, list) else 0
    except Exception:
        return 0


def collect_turn_tools(sid: str, n0: int) -> tuple[list[dict[str, Any]], str]:
    """Tool parts of the assistant messages added after index n0."""
    try:
        msgs = api("GET", f"/session/{sid}/message", timeout=60)
    except Exception:
        return [], ""
    if not isinstance(msgs, list):
        return [], ""
    tools: list[dict[str, Any]] = []
    last_text: str = ""
    for m in msgs[n0:]:
        if (m.get("info") or {}).get("role") != "assistant":
            continue
        for p in m.get("parts", []):
            if p.get("type") == "tool":
                tools.append(p)
            elif p.get("type") == "text" and not p.get("synthetic") and p.get("text", "").strip():
                last_text = p["text"].strip()
    return tools, last_text


def parse_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Return (actions, created, modified) from raw tool parts."""
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
        detail = action_detail(inp)
        ok = status == "completed"
        err = st.get("error") or ""
        # A bash/shell command can "complete" while the command itself failed
        # (non-zero exit). Treat that as a failure and surface the output.
        meta = st.get("metadata") or {}
        if name in ("bash", "shell") and isinstance(meta.get("exit"), int):
            if meta.get("exit") != 0:
                ok = False
                err = err or (st.get("output") or "")[:200]
        actions.append({"tool": name, "detail": detail, "ok": ok,
                        "status": status, "error": err})
        if name in FILE_TOOLS:
            fp = inp.get("filePath") or inp.get("filepath") or inp.get("path")
            if fp:
                exists_before = (st.get("metadata") or {}).get("exists")
                if name == "write" and exists_before is False:
                    created.append(fp)
                else:
                    modified.append(fp)
    return actions, created, modified


def run_turn(sid: str, text: str) -> tuple[str, list[dict[str, Any]]]:
    """Send one message; return (reply_text, tools). Never raises."""
    n0 = session_message_count(sid)
    reply = ""
    for attempt in (1, 2, 3):
        try:
            msg = api("POST", f"/session/{sid}/message",
                      {"parts": [{"type": "text", "text": text}]}, timeout=900)
            parts = msg.get("parts", []) if isinstance(msg, dict) else []
            reply = "\n".join(p.get("text", "") for p in parts
                              if p.get("type") == "text" and not p.get("synthetic")).strip()
            err = (msg.get("info") or {}).get("error") if isinstance(msg, dict) else None
            if not reply and err:
                reply = "7asal khata2: " + str((err.get("data") or {}).get("message")
                                               or err.get("name"))
            break
        except Exception as e:
            print(f"zyvo-voice: request error ({attempt}/3): {e}", flush=True)
            if ensure_server() is None:
                time.sleep(2)
            else:
                time.sleep(1)

    tools, last_text = collect_turn_tools(sid, n0)
    if not reply:
        reply = last_text
    return reply, tools


# --------------------------------------------------------------------------- #
# Build + render + speak the report
# --------------------------------------------------------------------------- #
def _short(s: str, n: int = 140) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def render_report(heard: str, reply: str, actions: list[dict[str, Any]],
                  created: list[str], modified: list[str]) -> None:
    """Print a coloured action card. Arabic labels and Latin details go on
    SEPARATE lines so bidi reordering never mangles file paths/commands."""
    print()
    rule()
    show("🎙  فهمت منك", C_CYAN)
    show(f"     {heard}", C_CYAN)
    show(f"🛠  الإجراءات: {len(actions)}", C_YEL)
    if not actions:
        show("     — ما نفّذت أي إجراء خارجي —", C_DIM)
    for a in actions:
        verb = ARABIC_VERB.get(a["tool"], "استخدمت أداة")
        if a["ok"]:
            show(f"   ✓ {verb}", C_GREEN)
        else:
            show(f"   ✗ {verb} — فشل", C_RED)
        if a["detail"]:
            show(f"     {_short(a['detail'], 120)}", C_DIM)
        if not a["ok"] and a["error"]:
            show(f"     ! {_short(a['error'], 130)}", C_RED)
    show(f"📄  الملفات: أنشأت {len(created)}، عدّلت {len(modified)}",
         C_MAG if (created or modified) else C_DIM)
    for f in created:
        show(f"   + {f}", C_GREEN)
    for f in modified:
        show(f"   ~ {f}", C_YEL)
    show("🤖  الرد", C_BLUE)
    show(f"     {reply or '—'}", C_BLUE)
    rule()


def speech_report(heard: str, reply: str, actions: list[dict[str, Any]],
                  created: list[str], modified: list[str]) -> str:
    """Compose the Arabic report that will be spoken aloud."""
    ok_all = all(a["ok"] for a in actions) if actions else True
    lines = []
    lines.append(f"فهمت منك: {heard}.")
    if actions:
        lines.append(f"نفذت {len(actions)} إجراء.")
        for i, a in enumerate(actions, 1):
            verb = ARABIC_VERB.get(a["tool"], "استخدمت أداة")
            d = _short(a["detail"], 120)
            if a["ok"]:
                lines.append(f"الإجراء رقم {i}: {verb}: {d}. نجح.")
            else:
                reason = _short(a["error"], 120)
                lines.append(f"الإجراء رقم {i}: {verb}: {d}. فشل. السبب: {reason}.")
    else:
        lines.append("ما نفذت أي إجراء، جاوبت فقط.")
    if created:
        lines.append("أنشأت الملفات: " + "، ".join(created) + ".")
    if modified:
        lines.append("عدلت الملفات: " + "، ".join(modified) + ".")
    if not created and not modified:
        lines.append("ما في ملفات انشأت أو اتعدلت.")
    lines.append("النتيجة العامة: " + ("نجحت كل الإجراءات." if ok_all else "فيه إجراءات فشلت."))
    if reply:
        lines.append("الرد: " + reply)
    return " ".join(lines)


# --------------------------------------------------------------------------- #
# Voice in / out
# --------------------------------------------------------------------------- #
def listen() -> str:
    try:
        out = subprocess.run(["termux-speech-to-text"], capture_output=True,
                             text=True, timeout=90)
    except Exception:
        return ""
    raw = (out.stdout or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            if obj.get("error"):
                return ""
            return (obj.get("text") or "").strip()
        return raw
    except Exception:
        return raw


def clean_for_speech(t: str) -> str:
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s{0,3}[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"[#>*_~|^]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def speak(text: str, no_speak: bool = False) -> None:
    if no_speak:
        return
    text = clean_for_speech(text)
    if not text:
        return
    for i in range(0, len(text), 3000):
        cmd = ["termux-tts-speak"]
        if TTS_LANG:
            cmd += ["-l", TTS_LANG]
        cmd.append(text[i:i + 3000])
        try:
            subprocess.run(cmd)
        except Exception:
            pass


def buzz() -> None:
    try:
        subprocess.run(["termux-vibrate", "-d", "70"], timeout=3)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Voice assistant via ZYVO (full action mode).")
    ap.add_argument("--text", help="send one typed command instead of listening")
    ap.add_argument("--new", action="store_true", help="start a fresh session")
    ap.add_argument("--no-speak", action="store_true", help="don't use TTS")
    ap.add_argument("--no-mic", action="store_true", help="type input instead of speaking")
    ap.add_argument("--chat", action="store_true", help="chat only (do NOT execute tools)")
    ap.add_argument("--no-report", action="store_true", help="speak only the answer")
    args = ap.parse_args()

    global ACTION
    if args.chat:
        ACTION = "reject"

    try:
        if ensure_server() is None:
            sys.exit("zyvo-voice: could not start the ZYVO server.")

        sid = get_session(force_new=args.new)
        print(f"zyvo-voice: session {sid}  |  mode={'chat' if args.chat else 'ACTION'}",
              flush=True)

        wd = PermissionWatchdog()
        wd.sid = sid
        wd.start()

        if args.text is not None:
            reply, tools = run_turn(sid, args.text)
            actions, created, modified = parse_tools(tools)
            render_report(args.text, reply, actions, created, modified)
            speak(speech_report(args.text, reply, actions, created, modified),
                  args.no_speak)
            return

        speak("أنا زيڨو، في وضع التنفيذ، اسمعك دلوقتي", args.no_speak)
        print("\nاتكلم دلوقتي (قول stop للتوقف)...\n", flush=True)

        while True:
            if args.no_mic:
                try:
                    heard = input("you> ").strip()
                except EOFError:
                    break
            else:
                buzz()
                heard = listen()

            if not heard:
                print("zyvo-voice: maa sem3tek, garray tani", flush=True)
                if not args.no_mic:
                    speak("ما سمعتك، جرب تاني", args.no_speak)
                continue

            if heard.lower().strip() in STOP_WORDS:
                speak("تمام، مع السلامة", args.no_speak)
                print("zyvo-voice: weda3an 👋", flush=True)
                break

            print("zyvo-voice: banaf-fez...", flush=True)
            reply, tools = run_turn(sid, heard)
            actions, created, modified = parse_tools(tools)
            render_report(heard, reply, actions, created, modified)

            if args.no_report:
                speak(reply, args.no_speak)
            else:
                speak(speech_report(heard, reply, actions, created, modified),
                      args.no_speak)
            if not args.no_mic:
                time.sleep(0.4)

    except KeyboardInterrupt:
        print("\nzyvo-voice: et2af", flush=True)
    finally:
        kill_tree(_PROC)


if __name__ == "__main__":
    main()
