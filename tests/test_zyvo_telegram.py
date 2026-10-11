"""Unit tests for ``zyvo-telegram.py`` — every function is exercised.

Strategy: the module is loaded once by the ``tg`` fixture (see conftest.py).
Each test patches the exact seam the function uses (``api``, ``tg``,
``urllib``, ``subprocess``, ``time``) so that nothing hits the network, spawns
a real process, or waits on a real clock.
"""

from __future__ import annotations

import io
import shutil
import types
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from conftest import FakePopen, FakeResponse


# --------------------------------------------------------------------------- #
# Config / env
# --------------------------------------------------------------------------- #
def test_load_env_reads_file(tg, tmp_path, monkeypatch):
    env = tmp_path / "telegram.env"
    env.write_text(
        "# comment\n"
        "\n"
        "TELEGRAM_BOT_TOKEN=abc:123\n"
        "ZYVO_TELEGRAM_CHAT_ID= 42 \n"
        "MALFORMED_LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tg, "ENV_FILE", str(env))
    cfg = tg.load_env()
    assert cfg["TELEGRAM_BOT_TOKEN"] == "abc:123"
    assert cfg["ZYVO_TELEGRAM_CHAT_ID"] == "42"     # .strip() applied
    assert "MALFORMED_LINE" not in cfg


def test_load_env_missing_file_returns_empty(tg, monkeypatch):
    monkeypatch.setattr(tg, "ENV_FILE", "/definitely/not/here.env")
    assert tg.load_env() == {}


# --------------------------------------------------------------------------- #
# Low-level HTTP: _request / tg / _multipart / tg_upload
# --------------------------------------------------------------------------- #
def test_request_parses_json(tg, monkeypatch):
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(b'{"ok":true,"result":{"id":1}}'))
    out = tg._request("http://x/y")
    assert out == {"ok": True, "result": {"id": 1}}


def test_tg_calls_api_and_returns(tg, monkeypatch):
    seen = {}

    def fake_request(url, data=None, headers=None, timeout=60):
        seen["url"] = url
        seen["data"] = data
        return {"ok": True, "result": {"username": "bot"}}

    monkeypatch.setattr(tg, "_request", fake_request)
    monkeypatch.setattr(tg, "API", "https://api.telegram.org/botTEST")
    res = tg.tg("getMe", {"a": 1})
    assert res["result"]["username"] == "bot"
    assert seen["url"].endswith("/getMe")
    assert b"a=1" in seen["data"]


def test_tg_prints_on_error(tg, monkeypatch, capsys):
    monkeypatch.setattr(tg, "_request", lambda *a, **k: {"ok": False, "x": 1})
    tg.tg("sendMessage", {})
    assert "error" in capsys.readouterr().out


def test_multipart_shape(tg):
    boundary, body = tg._multipart(
        {"chat_id": "1"},
        {"photo": ("a.png", b"\x89PNG", "image/png")},
    )
    assert boundary.startswith("----zyvo")
    assert b'name="chat_id"' in body
    assert b'filename="a.png"' in body
    assert b"\x89PNG" in body
    assert body.endswith(b"--\r\n")


def test_tg_upload_reads_file_and_posts(tg, tmp_path, monkeypatch):
    f = tmp_path / "v.ogg"
    f.write_bytes(b"OggS")
    captured = {}

    def fake_request(url, data=None, headers=None, timeout=60):
        captured["headers"] = headers
        captured["data"] = data
        return {"ok": True}

    monkeypatch.setattr(tg, "_request", fake_request)
    out = tg.tg_upload("sendVoice", {"chat_id": "1"}, "voice", str(f), "audio/ogg")
    assert out["ok"] is True
    assert "multipart/form-data" in captured["headers"]["Content-Type"]
    assert b"OggS" in captured["data"]


# --------------------------------------------------------------------------- #
# Telegram senders
# --------------------------------------------------------------------------- #
def test_send_message_chunks_long_text(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "tg", lambda m, p, **k: calls.append((m, p)) or {"ok": True})
    long = "x" * 9001
    tg.send_message("42", long)
    assert len(calls) == 3                      # 4000 + 4000 + 1001
    assert all(c[0] == "sendMessage" for c in calls)
    assert calls[0][1]["chat_id"] == "42"


def test_send_message_none_is_safe(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "tg", lambda m, p, **k: calls.append(p) or {"ok": True})
    tg.send_message("42", None)                 # type: ignore[arg-type]
    assert calls == []  # nothing is sent for an empty reply


def test_send_message_falls_back_without_parse_mode(tg, monkeypatch):
    calls = []

    def flaky(method, params, **k):
        calls.append(dict(params))
        if "parse_mode" in params:
            raise RuntimeError("bad Markdown")
        return {"ok": True}

    monkeypatch.setattr(tg, "tg", flaky)
    tg.send_message("42", "*bold*")
    assert len(calls) == 2
    assert "parse_mode" in calls[0] and "parse_mode" not in calls[1]


def test_send_voice_and_photo_delegate_to_upload(tg, monkeypatch):
    seen = []
    monkeypatch.setattr(tg, "tg_upload",
                        lambda m, f, ff, p, c: seen.append((m, ff, c)) or {"ok": True})
    tg.send_voice("1", "/tmp/x.ogg", caption="cap")
    tg.send_photo("1", "/tmp/x.png")
    assert seen[0] == ("sendVoice", "voice", "audio/ogg")
    assert seen[0][1:2] == ("voice",)
    assert seen[1][0] == "sendPhoto"


def test_send_action_swallows_errors(tg, monkeypatch):
    monkeypatch.setattr(tg, "tg", mock.Mock(side_effect=RuntimeError("boom")))
    tg.send_action("1", "typing")               # must not raise


def test_download_file_ok(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "tg",
                        lambda m, p, **k: {"ok": True, "result": {"file_path": "p/x.oga"}})
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(b"BINARY"))
    dest = tmp_path / "x.oga"
    out = tg.download_file("FILEID", str(dest))
    assert out == str(dest)
    assert dest.read_bytes() == b"BINARY"


def test_download_file_not_ok(tg, monkeypatch):
    monkeypatch.setattr(tg, "tg", lambda m, p, **k: {"ok": False})
    assert tg.download_file("F", "/tmp/none") is None


# --------------------------------------------------------------------------- #
# ZYVO HTTP: api / server_up
# --------------------------------------------------------------------------- #
def test_api_get_returns_parsed_json(tg, monkeypatch):
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(b'{"id":"s1"}'))
    assert tg.api("GET", "/session") == {"id": "s1"}


def test_api_empty_body_is_null(tg, monkeypatch):
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b""))
    assert tg.api("GET", "/x") is None


def test_api_http_error_raises_runtimeerror(tg, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"gone"))

    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        tg.api("GET", "/missing")
    assert "HTTP 404" in str(ei.value)


def test_server_up_true_and_false(tg, monkeypatch):
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(status=200))
    assert tg.server_up() is True
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert tg.server_up() is False


# --------------------------------------------------------------------------- #
# Server lifecycle: kill_tree / start_server / ensure_server
# --------------------------------------------------------------------------- #
def test_kill_tree_none_is_noop(tg):
    tg.kill_tree(None)                          # must not raise


def test_kill_tree_sends_sigterm_then_sigkill(tg, monkeypatch):
    signals = []
    monkeypatch.setattr(tg.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(tg.os, "killpg", lambda pg, sig: signals.append(sig))
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    class AlwaysAlive:
        pid = 7
        def poll(self):
            return None

    tg.kill_tree(AlwaysAlive())                 # type: ignore[arg-type]
    assert signals[0] == tg.signal.SIGTERM
    assert signals[-1] == tg.signal.SIGKILL


def test_start_server_success(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tg, "LOG_SERVER", str(tmp_path / "srv.log"))
    monkeypatch.setattr(tg.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(tg, "server_up", lambda: True)
    proc = tg.start_server()
    assert proc is FakePopen.last


def test_start_server_failure_raises(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tg, "LOG_SERVER", str(tmp_path / "srv.log"))

    class Dead(FakePopen):
        def poll(self):
            return 1

    monkeypatch.setattr(tg.subprocess, "Popen", Dead)
    monkeypatch.setattr(tg, "server_up", lambda: False)
    monkeypatch.setattr(tg, "kill_tree", lambda p: None)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not come up"):
        tg.start_server()


def test_start_server_missing_script_raises(tg, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="script"):
        tg.start_server()


def test_ensure_server_returns_existing_when_up(tg, monkeypatch):
    monkeypatch.setattr(tg, "server_up", lambda: True)
    tg._PROC = "sentinel"
    assert tg.ensure_server() == "sentinel"


def test_ensure_server_starts_when_down(tg, monkeypatch):
    monkeypatch.setattr(tg, "server_up", lambda: False)
    monkeypatch.setattr(tg, "kill_tree", lambda p: None)
    monkeypatch.setattr(tg, "start_server", lambda: "NEWPROC")
    tg._PROC = None
    assert tg.ensure_server() == "NEWPROC"


def test_ensure_server_gives_up_returns_none(tg, monkeypatch):
    monkeypatch.setattr(tg, "server_up", lambda: False)
    monkeypatch.setattr(tg, "kill_tree", lambda p: None)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    def boom():
        raise RuntimeError("cannot start")

    monkeypatch.setattr(tg, "start_server", boom)
    assert tg.ensure_server() is None


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
def test_get_session_resumes_valid(tg, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("SID-1", encoding="utf-8")
    monkeypatch.setattr(tg, "STATE_FILE", str(state))
    monkeypatch.setattr(tg, "api", lambda *a, **k: {"id": "SID-1"})
    assert tg.get_session() == "SID-1"


def test_get_session_force_new(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "STATE_FILE", str(tmp_path / "sess"))
    monkeypatch.setattr(tg, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tg, "api", lambda *a, **k: {"id": "FRESH"})
    assert tg.get_session(force_new=True) == "FRESH"
    assert (tmp_path / "sess").read_text() == "FRESH"


def test_get_session_stale_file_creates_new(tg, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(tg, "STATE_FILE", str(state))
    monkeypatch.setattr(tg, "STATE_DIR", str(tmp_path))

    def api(method, path, *a, **k):
        if method == "GET":
            raise RuntimeError("404")
        return {"id": "NEW"}

    monkeypatch.setattr(tg, "api", api)
    assert tg.get_session() == "NEW"


# --------------------------------------------------------------------------- #
# SSE + permission watchdog
# --------------------------------------------------------------------------- #
def test_sse_listener_records_tool_part_then_stops(tg, monkeypatch):
    event = (b'data: {"type":"message.part.updated","properties":'
             b'{"part":{"type":"tool","id":"P1","tool":"bash"}}}\n')

    class Stream(FakeResponse):
        def __init__(self):
            super().__init__(body=b"")
            self.status = 200

        def __iter__(self):
            return iter([event])

    calls = {"n": 0}

    def urlopen(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return Stream()
        raise KeyboardInterrupt                     # leave the infinite loop

    monkeypatch.setattr(tg.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    tg.PARTS.clear()
    with pytest.raises(KeyboardInterrupt):
        tg.sse_listener()
    assert "P1" in tg.PARTS and tg.PARTS["P1"]["tool"] == "bash"


def test_permission_watchdog_answers_and_respects_pause(tg, monkeypatch):
    replies = []
    monkeypatch.setattr(tg, "CURRENT_SID", "S")
    monkeypatch.setattr(tg, "PAUSED", True)

    def api(method, path, body=None, *a, **k):
        if method == "GET" and path.endswith("/permission"):
            return [{"id": "R1"}]
        if method == "POST":
            replies.append(body["reply"])
        return []

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))

    wd = tg.PermissionWatchdog()
    wd.stop = types.MethodType(lambda self: None, wd)   # avoid attribute clash
    with pytest.raises(KeyboardInterrupt):
        wd.run()
    assert replies and replies[0] == "reject"          # PAUSED -> reject


# --------------------------------------------------------------------------- #
# Report builders (pure)
# --------------------------------------------------------------------------- #
def test_short_truncates_and_collapses(tg):
    assert tg._short("  a\n b\tc  ") == "a b c"
    out = tg._short("x" * 500, 10)
    assert out.endswith("…") and len(out) == 11


def test_action_detail_variants(tg):
    assert tg.action_detail({"command": "ls"}) == "ls"
    assert tg.action_detail({"filePath": "/tmp/x"}) == "/tmp/x"
    assert tg.action_detail({"pattern": "  "}) == ""
    assert tg.action_detail("not-a-dict") == ""


def test_collect_turn_tools_filters_by_snapshot(tg):
    tg.PARTS.clear()
    tg.PARTS["old"] = {"id": "old", "tool": "read", "sessionID": "sid"}
    tg.PARTS["new"] = {"id": "new", "tool": "bash", "sessionID": "sid"}
    out = tg.collect_turn_tools("sid", {"old"})
    assert [p["id"] for p in out] == ["new"]


def test_parse_tools_success_failure_and_files(tg):
    tools = [
        {"tool": "bash", "state": {"input": {"command": "ls"}, "status": "completed",
                                   "metadata": {"exit": 0}}},
        {"tool": "bash", "state": {"status": "completed",
                                   "metadata": {"exit": 1}, "output": "boom"}},
        {"tool": "write", "state": {"input": {"filePath": "/new.txt"},
                                    "status": "completed",
                                    "metadata": {"exists": False}}},
        {"tool": "edit", "state": {"input": {"filePath": "/mod.txt"},
                                   "status": "completed", "metadata": {"exists": True}}},
        {"tool": "todowrite", "state": {"status": "completed"}},   # skipped
    ]
    actions, created, modified = tg.parse_tools(tools)
    assert [a["tool"] for a in actions] == ["bash", "bash", "write", "edit"]
    assert actions[0]["ok"] is True
    assert actions[1]["ok"] is False and "boom" in actions[1]["error"]
    assert created == ["/new.txt"]
    assert modified == ["/mod.txt"]


def test_run_turn_success(tg, monkeypatch):
    tg.PARTS.clear()
    monkeypatch.setattr(tg, "api",
                        lambda *a, **k: {"parts": [{"type": "text", "text": "hello"}]})
    monkeypatch.setattr(tg, "collect_turn_tools", lambda sid, pre: [])
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    reply, tools = tg.run_turn("S", "hi")
    assert reply == "hello" and tools == []


def test_run_turn_retries_then_recovers(tg, monkeypatch):
    state = {"n": 0}

    def api(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("down")
        return {"parts": [{"type": "text", "text": "recovered"}]}

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg, "ensure_server", lambda: "PROC")
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    monkeypatch.setattr(tg, "collect_turn_tools", lambda sid, pre: [])
    reply, _ = tg.run_turn("S", "hi")
    assert reply == "recovered"


def test_text_report_and_spoken_report(tg):
    actions = [
        {"tool": "bash", "detail": "ls -la", "ok": True, "error": ""},
        {"tool": "write", "detail": "/tmp/a.md", "ok": False, "error": "disk full"},
    ]
    text = tg.text_report("do it", "done", actions, ["/tmp/new.md"], ["/tmp/x.md"])
    assert "فهمت منك" in text and "ls -la" in text and "/tmp/new.md" in text
    assert "disk full" in text

    spoken = tg.spoken_report("do it", "done", actions, [], [])
    assert "نجحت" in spoken or "فشل" in spoken
    assert "ls -la" in spoken


def test_text_report_empty_actions(tg):
    out = tg.text_report("h", "", [], [], [])
    assert "ما نفّذت" in out


# --------------------------------------------------------------------------- #
# Speech helpers
# --------------------------------------------------------------------------- #
def test_clean_for_speech_strips_markup(tg):
    src = "**bold** `code` [link](http://x) https://y\n# heading"
    out = tg.clean_for_speech(src)
    assert "*" not in out and "`" not in out
    assert "http" not in out
    assert "bold" in out and "code" in out


def test_make_voice_note_empty_returns_none(tg):
    assert tg.make_voice_note("   ", "/tmp/x.ogg") is None


def test_make_voice_note_espeak_fallback(tg, tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gtts", None)      # force ImportError
    monkeypatch.setattr(tg.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0))
    monkeypatch.setattr(tg.os.path, "getsize", lambda p: 10)
    out = tg.make_voice_note("hello", str(tmp_path / "v.ogg"))
    assert out == str(tmp_path / "v.ogg")


def test_transcribe_returns_empty_without_sr(tg, tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(tg.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0))
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    assert tg.transcribe(str(tmp_path / "a.oga")) == ""


def test_transcribe_with_fake_sr(tg, tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(tg.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0))

    fake = types.ModuleType("speech_recognition")

    class Recognizer:
        def record(self, *a):
            return b"audio"

        def recognize_google(self, audio, language="ar-SA"):
            return "  salam  "

    class AudioFile:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake.Recognizer = Recognizer
    fake.AudioFile = AudioFile
    monkeypatch.setitem(sys.modules, "speech_recognition", fake)
    assert tg.transcribe(str(tmp_path / "a.oga")) == "salam"


# --------------------------------------------------------------------------- #
# Message flow: build_and_send / worker / handle_control
# --------------------------------------------------------------------------- #
def test_build_and_send_text_voice_and_photos(tg, tmp_path, monkeypatch):
    calls = {"msgs": [], "voice": [], "photos": []}
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    tools = [{"tool": "write",
              "state": {"status": "completed", "input": {"filePath": str(img)},
                        "metadata": {"exists": False}}}]
    monkeypatch.setattr(tg, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: calls["msgs"].append(t))
    monkeypatch.setattr(tg, "make_voice_note",
                        lambda t, p, **k: (Path(p).write_bytes(b"o") or p))
    monkeypatch.setattr(tg, "send_action", lambda c, a="typing": None)
    monkeypatch.setattr(tg, "send_voice", lambda c, p: calls["voice"].append(p))
    monkeypatch.setattr(tg, "send_photo", lambda c, p, **k: calls["photos"].append(p))
    tg.build_and_send("42", "hi", "done", tools)
    assert calls["msgs"] and calls["voice"] and calls["photos"] == [str(img)]


def test_worker_processes_one_item(tg, monkeypatch):
    finished = []
    monkeypatch.setattr(tg, "ensure_server", lambda: object())
    monkeypatch.setattr(tg, "get_session", lambda: "S")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "run_turn", lambda sid, text: ("reply", []))
    monkeypatch.setattr(tg, "build_and_send",
                        lambda c, h, r, t: finished.append((c, h, r)))
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)

    class OneQueue:
        def __init__(self):
            self.items = [("42", "hi")]

        def get(self):
            if self.items:
                return self.items.pop()
            raise KeyboardInterrupt

        def task_done(self):
            pass

        def qsize(self):
            return 0

    monkeypatch.setattr(tg, "WORKQ", OneQueue())
    with pytest.raises(KeyboardInterrupt):
        tg.worker()
    assert finished == [("42", "hi", "reply")]


def test_handle_control_branches(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: sent.append(t))
    monkeypatch.setattr(tg, "get_session", lambda force_new=False: "NEW-SID")
    monkeypatch.setattr(tg, "api", lambda *a, **k: {})
    monkeypatch.setattr(tg, "PAUSED", False)

    assert tg.handle_control("42", "/help") is True
    assert tg.handle_control("42", "/status") is True
    assert tg.handle_control("42", "/new") is True
    assert tg.handle_control("42", "stop") is True        # enters safe mode
    assert tg.PAUSED is True
    assert tg.handle_control("42", "resume") is True
    assert tg.PAUSED is False
    assert tg.handle_control("42", "just a normal message") is False
    assert len(sent) >= 5


def test_poll_loop_dispatches_and_ignores(tg, monkeypatch):
    queued = []
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "42")
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "handle_control", lambda c, t: False)

    class Q:
        def put(self, item):
            queued.append(item)

        def qsize(self):
            return len(queued)

    monkeypatch.setattr(tg, "WORKQ", Q())

    updates = [{"result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "hello"}},
        {"update_id": 2, "message": {"chat": {"id": 999}, "text": "intruder"}},
    ]}]
    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "bot"}}
        if method == "getUpdates":
            state["i"] += 1
            if state["i"] == 1:
                return updates[0]
            raise KeyboardInterrupt
        return {}

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert queued == [("42", "hello")]      # chat 999 ignored


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def test_self_test_runs(tg, monkeypatch, capsys):
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "42")
    monkeypatch.setattr(tg, "server_up", lambda: True)
    monkeypatch.setattr(tg.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0))
    monkeypatch.setattr(tg, "make_voice_note", lambda t, p, **k: p)
    tg.self_test()
    out = capsys.readouterr().out
    assert "token: set" in out and "server: up" in out


def test_main_exits_without_token(tg, monkeypatch):
    monkeypatch.setattr(tg, "TOKEN", "")
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram"])
    with pytest.raises(SystemExit):
        tg.main()


def test_main_send_test_path(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "42")
    monkeypatch.setattr(tg.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(tg, "tg", lambda m, p, **k: sent.append((m, p)) or {"ok": True})
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram", "--send-test"])
    tg.main()
    assert sent and sent[0][0] == "sendMessage"


def test_main_self_test_path(tg, monkeypatch):
    called = []
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(tg, "self_test", lambda: called.append(True))
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram", "--self-test"])
    tg.main()
    assert called == [True]


def test_main_full_path_wires_threads(tg, monkeypatch):
    started = []
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(tg, "ensure_server", lambda: object())
    monkeypatch.setattr(tg, "get_session", lambda *a, **k: "S")
    monkeypatch.setattr(tg, "poll_loop", lambda: started.append("poll"))
    monkeypatch.setattr(tg, "PermissionWatchdog",
                        lambda: types.SimpleNamespace(start=lambda: started.append("wd")))
    monkeypatch.setattr(tg.threading, "Thread",
                        lambda target, **k: types.SimpleNamespace(start=lambda: started.append(target.__name__)))
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram"])
    tg.main()
    assert "poll" in started


# --------------------------------------------------------------------------- #
# Extra edge paths (coverage hardening)
# --------------------------------------------------------------------------- #
class _OneShotQueue:
    """A WORKQ stand-in that yields one item then raises to stop the loop."""

    def __init__(self, items):
        self.items = list(items)

    def get(self):
        if self.items:
            return self.items.pop(0)
        raise KeyboardInterrupt

    def task_done(self):
        pass

    def qsize(self):
        return len(self.items)

    def put(self, item):
        self.items.append(item)


def test_tg_upload_prints_on_error(tg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tg, "_request", lambda *a, **k: {"ok": False})
    p = tmp_path / "f.bin"
    p.write_bytes(b"x")
    tg.tg_upload("sendPhoto", {}, "photo", str(p), "image/png")
    assert "upload error" in capsys.readouterr().out


def test_send_voice_no_caption_and_photo_with_caption(tg, monkeypatch):
    seen = []
    monkeypatch.setattr(tg, "tg_upload",
                        lambda m, f, ff, p, c: seen.append((m, dict(f))) or {"ok": True})
    tg.send_voice("1", "/tmp/x.ogg")                 # no caption -> 162->164
    tg.send_photo("1", "/tmp/x.png", caption="hi")   # caption   -> 169-170
    assert seen[0][0] == "sendVoice" and "caption" not in seen[0][1]
    assert seen[1][0] == "sendPhoto" and seen[1][1]["caption"] == "hi"


def test_kill_tree_fallback_when_getpgid_fails(tg, monkeypatch):
    monkeypatch.setattr(tg.os, "getpgid",
                        lambda pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    class Zombie:
        pid = 9

        def poll(self):
            return None

        def terminate(self):
            raise RuntimeError("cannot terminate")

    tg.kill_tree(Zombie())                           # type: ignore[arg-type]


def test_kill_tree_returns_when_proc_already_dead(tg, monkeypatch):
    monkeypatch.setattr(tg.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(tg.os, "killpg", lambda pg, sig: None)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    class Dead:
        pid = 9

        def poll(self):
            return 0

    tg.kill_tree(Dead())                             # type: ignore[arg-type]


def test_start_server_waits_until_ready(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tg, "LOG_SERVER", str(tmp_path / "srv.log"))
    monkeypatch.setattr(tg.subprocess, "Popen", FakePopen)
    state = {"n": 0}

    def server_up():
        state["n"] += 1
        return state["n"] >= 2

    monkeypatch.setattr(tg, "server_up", server_up)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    proc = tg.start_server()
    assert proc is FakePopen.last


def test_ensure_server_waits_for_existing_proc(tg, monkeypatch):
    monkeypatch.setattr(tg, "server_up", lambda: False)
    killed = []
    monkeypatch.setattr(tg, "kill_tree", lambda p: killed.append(p))
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    class Live:
        def poll(self):
            return None

    live = Live()
    tg._PROC = live
    monkeypatch.setattr(tg, "start_server", lambda: "NEW")
    assert tg.ensure_server() == "NEW"
    assert killed == [live]


def test_ensure_server_wait_loop_finds_server(tg, monkeypatch):
    calls = {"n": 0}

    def server_up():
        calls["n"] += 1
        return calls["n"] >= 2                       # False at top, True in wait

    monkeypatch.setattr(tg, "server_up", server_up)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)

    class Live:
        def poll(self):
            return None

    live = Live()
    tg._PROC = live
    assert tg.ensure_server() is live


def test_get_session_creates_when_state_empty(tg, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("", encoding="utf-8")
    monkeypatch.setattr(tg, "STATE_FILE", str(state))
    monkeypatch.setattr(tg, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tg, "api", lambda *a, **k: {"id": "NEW"})
    assert tg.get_session() == "NEW"


def test_get_session_creates_when_api_response_falsy(tg, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(tg, "STATE_FILE", str(state))
    monkeypatch.setattr(tg, "STATE_DIR", str(tmp_path))

    def api(method, path, *a, **k):
        if method == "GET":
            return None
        return {"id": "NEW2"}

    monkeypatch.setattr(tg, "api", api)
    assert tg.get_session() == "NEW2"


def test_sse_listener_parses_many_line_shapes(tg, monkeypatch):
    lines = [
        b"\n",                                                     # not data:
        b"data: \n",                                               # empty payload
        b"data: not-json\n",                                       # bad json
        b'data: {"type":"other"}\n',                               # wrong type
        b'data: {"type":"message.part.updated","properties":'
        b'{"part":{"type":"tool"}}}\n',                            # tool w/o id
        b'data: {"type":"message.part.updated","properties":'
        b'{"part":{"type":"tool","id":"P2","tool":"read"}}}\n',    # good
    ]

    class Stream(FakeResponse):
        def __init__(self):
            super().__init__(body=b"")
            self.status = 200

        def __iter__(self):
            return iter(lines)

    calls = {"n": 0}

    def urlopen(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return Stream()
        raise KeyboardInterrupt

    monkeypatch.setattr(tg.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        tg.sse_listener()
    assert "P2" in tg.PARTS and tg.PARTS["P2"]["tool"] == "read"


def test_sse_listener_reconnects_after_error(tg, monkeypatch, capsys):
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        tg.sse_listener()
    assert "reconnect" in capsys.readouterr().out


def test_watchdog_stop_flag_and_immediate_return(tg):
    wd = tg.PermissionWatchdog()
    assert wd._stop is False
    wd.stop()
    assert wd._stop is True
    wd.run()                                        # returns at once, no sleep


def test_watchdog_skips_when_no_sid(tg, monkeypatch):
    monkeypatch.setattr(tg, "CURRENT_SID", None)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = tg.PermissionWatchdog()
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_watchdog_handles_nonlist_missing_rid_and_post_fallback(tg, monkeypatch):
    monkeypatch.setattr(tg, "CURRENT_SID", "S")
    posted = []

    def api(method, path, body=None, *a, **k):
        if method == "GET":
            if path.endswith("/permission") and path == "/session/S/permission":
                return [{"id": "1"}, {}]            # 2nd item has no rid
            return "not-a-list"                     # non-list branch
        posted.append(path)
        if path.startswith("/session/S/permission/1/reply"):
            raise RuntimeError("bad")               # first reply path fails
        return {}

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = tg.PermissionWatchdog()
    with pytest.raises(KeyboardInterrupt):
        wd.run()
    assert any(p.endswith("/permission/1/reply") for p in posted)


def test_collect_turn_tools_skips_other_sessions(tg):
    tg.PARTS.clear()
    tg.PARTS["a"] = {"id": "a", "tool": "read", "sessionID": "side"}
    tg.PARTS["b"] = {"id": "b", "tool": "bash", "sessionID": "sid"}
    out = tg.collect_turn_tools("sid", set())
    assert [p["id"] for p in out] == ["b"]


def test_parse_tools_file_tool_without_path(tg):
    actions, created, modified = tg.parse_tools([
        {"tool": "edit", "state": {"input": {}, "status": "completed",
                                   "metadata": {}}},
    ])
    assert created == [] and modified == []
    assert len(actions) == 1


def test_run_turn_uses_long_sleep_when_server_missing(tg, monkeypatch):
    sleeps = []
    state = {"n": 0}

    def api(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("down")
        return {"parts": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg, "ensure_server", lambda: None)
    monkeypatch.setattr(tg.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(tg, "collect_turn_tools", lambda sid, pre: [])
    reply, _ = tg.run_turn("S", "hi")
    assert reply == "ok"
    assert 3 in sleeps


def test_run_turn_exhausts_all_retries(tg, monkeypatch):
    monkeypatch.setattr(tg, "api", mock.Mock(side_effect=OSError("down")))
    monkeypatch.setattr(tg, "ensure_server", lambda: "PROC")
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    monkeypatch.setattr(tg, "collect_turn_tools", lambda sid, pre: [])
    reply, tools = tg.run_turn("S", "hi")
    assert reply == "" and tools == []


def test_text_report_action_without_detail(tg):
    actions = [{"tool": "read", "detail": "", "ok": True, "error": ""}]
    out = tg.text_report("h", "r", actions, [], [])
    assert "قرأت" in out


def test_spoken_report_with_files_and_empty_reply(tg):
    out = tg.spoken_report("h", "", [], ["/a.md"], ["/b.md"])
    assert "أنشأت الملفات" in out and "عدلت الملفات" in out


def test_make_voice_note_uses_gtts_when_available(tg, tmp_path, monkeypatch):
    import sys

    class FakeGtts:
        def __init__(self, text, lang="ar"):
            self.text = text

        def save(self, path):
            Path(path).write_bytes(b"mp3")

    mod = types.ModuleType("gtts")
    mod.gTTS = FakeGtts                                    # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gtts", mod)
    monkeypatch.setattr(tg.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0))
    monkeypatch.setattr(tg.os.path, "getsize", lambda p: 10)
    out = tg.make_voice_note("hello", str(tmp_path / "v.ogg"))
    assert out == str(tmp_path / "v.ogg")


def test_make_voice_note_none_when_espeak_fails(tg, tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gtts", None)
    monkeypatch.setattr(tg.subprocess, "run",
                        mock.Mock(side_effect=RuntimeError("no espeak")))
    assert tg.make_voice_note("hi", str(tmp_path / "v.ogg")) is None


def test_make_voice_note_none_when_ffmpeg_fails(tg, tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gtts", None)

    def run(cmd, **k):
        if cmd[0] == "ffmpeg":
            raise RuntimeError("no ffmpeg")
        return mock.Mock(returncode=0)

    monkeypatch.setattr(tg.subprocess, "run", run)
    assert tg.make_voice_note("hi", str(tmp_path / "v.ogg")) is None


def test_transcribe_empty_when_ffmpeg_fails(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(tg.subprocess, "run",
                        mock.Mock(side_effect=RuntimeError("no ffmpeg")))
    assert tg.transcribe(str(tmp_path / "a.oga")) == ""


def test_build_and_send_without_voice_and_photo_error(tg, tmp_path, monkeypatch, capsys):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    tools = [{"tool": "write", "state": {"status": "completed",
              "input": {"filePath": str(img)}, "metadata": {"exists": False}}}]
    monkeypatch.setattr(tg, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "make_voice_note", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("photo fail")

    monkeypatch.setattr(tg, "send_photo", boom)
    tg.build_and_send("1", "h", "r", tools)
    assert "sendPhoto failed" in capsys.readouterr().out


def test_worker_warns_when_server_unavailable(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "ensure_server", lambda: None)
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: sent.append(t))
    monkeypatch.setattr(tg, "WORKQ", _OneShotQueue([("42", "hi")]))
    with pytest.raises(KeyboardInterrupt):
        tg.worker()
    assert any("تعذّر تشغيل" in t for t in sent)


def test_worker_reports_turn_error(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "ensure_server", lambda: "PROC")
    monkeypatch.setattr(tg, "get_session", lambda: "S")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: sent.append(t))
    monkeypatch.setattr(tg, "run_turn", mock.Mock(side_effect=RuntimeError("kaboom")))
    monkeypatch.setattr(tg, "WORKQ", _OneShotQueue([("42", "hi")]))
    with pytest.raises(KeyboardInterrupt):
        tg.worker()
    assert any("خطأ" in t for t in sent)


def test_handle_control_new_session_error(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: sent.append(t))
    monkeypatch.setattr(tg, "get_session",
                        mock.Mock(side_effect=RuntimeError("no server")))
    assert tg.handle_control("1", "/new") is True
    assert any("⚠️" in t for t in sent)


def test_handle_control_stop_without_sid(tg, monkeypatch):
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "CURRENT_SID", None)
    assert tg.handle_control("1", "stop") is True
    assert tg.PAUSED is True


def test_handle_control_stop_abort_error_swallowed(tg, monkeypatch):
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "CURRENT_SID", "S")
    monkeypatch.setattr(tg, "api", mock.Mock(side_effect=RuntimeError("abort fail")))
    assert tg.handle_control("1", "/stop") is True


def test_poll_loop_control_message_is_not_queued(tg, monkeypatch):
    queued = []
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "handle_control", lambda c, t: True)
    monkeypatch.setattr(tg, "WORKQ", _OneShotQueue(queued))

    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        state["i"] += 1
        if state["i"] == 1:
            return {"result": [{"update_id": 1,
                    "message": {"chat": {"id": 5}, "text": "/help"}}]}
        raise KeyboardInterrupt

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert queued == []


def test_poll_loop_handles_voice_transcription(tg, monkeypatch):
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "download_file", lambda fid, dest: dest)
    monkeypatch.setattr(tg, "transcribe", lambda p, **k: "شغل الأداة")
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "handle_control", lambda c, t: False)
    q = _OneShotQueue([])
    monkeypatch.setattr(tg, "WORKQ", q)

    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        state["i"] += 1
        if state["i"] == 1:
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 5},
                    "voice": {"file_id": "F"}}}]}
        raise KeyboardInterrupt

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert q.items == [("5", "شغل الأداة")]


def test_poll_loop_voice_not_understood(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "download_file", lambda fid, dest: dest)
    monkeypatch.setattr(tg, "transcribe", lambda p, **k: "")
    monkeypatch.setattr(tg, "send_message", lambda c, t, **k: sent.append(t))
    monkeypatch.setattr(tg, "WORKQ", _OneShotQueue([]))

    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        state["i"] += 1
        if state["i"] == 1:
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 5},
                    "voice": {"file_id": "F"}}}]}
        raise KeyboardInterrupt

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert any("ما قدرت أفهم" in t for t in sent)


def test_poll_loop_voice_control_message_not_queued(tg, monkeypatch):
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "download_file", lambda fid, dest: dest)
    monkeypatch.setattr(tg, "transcribe", lambda p, **k: "توقف")
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "handle_control", lambda c, t: True)
    q = _OneShotQueue([])
    monkeypatch.setattr(tg, "WORKQ", q)

    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        state["i"] += 1
        if state["i"] == 1:
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 5},
                    "voice": {"file_id": "F"}}}]}
        raise KeyboardInterrupt

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert q.items == []


def test_poll_loop_network_error(tg, monkeypatch, capsys):
    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        raise urllib.error.URLError("net down")

    monkeypatch.setattr(tg, "tg", fake_tg)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert "poll network error" in capsys.readouterr().out


def test_poll_loop_generic_error(tg, monkeypatch, capsys):
    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        raise ValueError("weird")

    monkeypatch.setattr(tg, "tg", fake_tg)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert "poll error" in capsys.readouterr().out


def test_main_warns_when_server_down(tg, monkeypatch, capsys):
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(tg, "ensure_server", lambda: None)
    monkeypatch.setattr(tg, "get_session", lambda: "S")
    monkeypatch.setattr(tg, "PermissionWatchdog",
                        lambda: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(tg.threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(tg, "poll_loop", lambda: None)
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram"])
    tg.main()
    assert "WARNING" in capsys.readouterr().out


def test_start_server_raises_when_never_ready(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tg, "LOG_SERVER", str(tmp_path / "srv.log"))

    class Zombie(FakePopen):
        def poll(self):
            return None                             # never exits

    monkeypatch.setattr(tg.subprocess, "Popen", Zombie)
    monkeypatch.setattr(tg, "server_up", lambda: False)
    monkeypatch.setattr(tg, "kill_tree", lambda p: None)
    monkeypatch.setattr(tg.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not come up"):
        tg.start_server()


def test_watchdog_get_error_is_swallowed(tg, monkeypatch):
    monkeypatch.setattr(tg, "CURRENT_SID", "S")

    def api(method, path, body=None, *a, **k):
        if method == "GET":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = tg.PermissionWatchdog()
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_watchdog_exhausts_reply_paths(tg, monkeypatch):
    monkeypatch.setattr(tg, "CURRENT_SID", "S")

    def api(method, path, body=None, *a, **k):
        if method == "GET":
            return [{"id": "1"}] if path == "/session/S/permission" else []
        raise RuntimeError("both reply paths fail")

    monkeypatch.setattr(tg, "api", api)
    monkeypatch.setattr(tg.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = tg.PermissionWatchdog()
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_poll_loop_continues_after_voice(tg, monkeypatch):
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "")
    monkeypatch.setattr(tg, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(tg, "download_file", lambda fid, dest: dest)
    monkeypatch.setattr(tg, "transcribe", lambda p, **k: "شغل")
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(tg, "handle_control", lambda c, t: False)
    q = _OneShotQueue([])
    monkeypatch.setattr(tg, "WORKQ", q)

    state = {"i": 0}

    def fake_tg(method, params=None, **k):
        if method == "getMe":
            return {"result": {"username": "b"}}
        state["i"] += 1
        if state["i"] == 1:
            return {"result": [
                {"update_id": 1, "message": {"chat": {"id": 5},
                 "voice": {"file_id": "F"}}},
                {"update_id": 2, "message": {"chat": {"id": 5},
                 "photo": [{"file_id": "P"}]}},   # ignored -> loop continues
            ]}
        raise KeyboardInterrupt

    monkeypatch.setattr(tg, "tg", fake_tg)
    with pytest.raises(KeyboardInterrupt):
        tg.poll_loop()
    assert q.items == [("5", "شغل")]


def test_main_survives_wake_lock_failure(tg, monkeypatch):
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "ALLOWED_CHAT", "42")
    monkeypatch.setattr(tg.subprocess, "Popen",
                        mock.Mock(side_effect=OSError("no termux")))
    monkeypatch.setattr(tg, "tg", lambda m, p, **k: {"ok": True})
    monkeypatch.setattr(tg.sys, "argv", ["zyvo-telegram", "--send-test"])
    tg.main()                                       # must not raise
