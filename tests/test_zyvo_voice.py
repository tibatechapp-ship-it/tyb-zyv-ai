"""Unit tests for ``zyvo-voice.py`` — every function is exercised.

The module is loaded once by the ``voice`` fixture (see conftest.py). Each test
patches the exact seam the function uses (``api``, ``subprocess``, ``urllib``,
``time``) so that nothing hits the network, spawns a real process, or waits on a
real clock.
"""

from __future__ import annotations

import io
import types
import urllib.error
from unittest import mock

import pytest

from conftest import FakePopen, FakeResponse


# --------------------------------------------------------------------------- #
# Terminal display: has_arabic / rtl_str / show / rule
# --------------------------------------------------------------------------- #
def test_has_arabic_detects_arabic_block(voice):
    assert voice.has_arabic("مرحبا") is True
    assert voice.has_arabic("hello world") is False
    assert voice.has_arabic("") is False
    assert voice.has_arabic("123 456") is False


def test_rtl_str_empty_and_latin_pass_through(voice, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "/bin/rtl")
    assert voice.rtl_str("") == ""
    assert voice.rtl_str("hello") == "hello"


def test_rtl_str_without_helper_returns_input(voice, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    assert voice.rtl_str("مرحبا") == "مرحبا"


def test_rtl_str_reorders_through_helper(voice, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "/bin/rtl")
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="FIXED\n"))
    assert voice.rtl_str("سطر") == "FIXED"


def test_rtl_str_keeps_blank_lines_and_falls_back_on_error(voice, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "/bin/rtl")

    def boom(*a, **k):
        raise RuntimeError("no rtl")

    monkeypatch.setattr(voice.subprocess, "run", boom)
    # blank line preserved verbatim; non-blank line falls back to original
    assert voice.rtl_str("س\n\nx") == "س\n\nx"


def test_show_applies_color(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    voice.show("hello", voice.C_CYAN)
    out = capsys.readouterr().out
    assert "hello" in out
    assert voice.C_CYAN in out and voice.C_RESET in out


def test_rule_draws_a_line(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    voice.rule("=")
    assert "=" * 46 in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def test_api_get_returns_parsed_json(voice, monkeypatch):
    monkeypatch.setattr(voice.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(b'{"id":"s1"}'))
    assert voice.api("GET", "/session") == {"id": "s1"}


def test_api_empty_body_is_null(voice, monkeypatch):
    monkeypatch.setattr(voice.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b""))
    assert voice.api("GET", "/x") is None


def test_api_http_error_raises_runtimeerror(voice, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"bad"))

    monkeypatch.setattr(voice.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        voice.api("GET", "/broken")
    assert "HTTP 500" in str(ei.value)


def test_server_up_true_and_false(voice, monkeypatch):
    monkeypatch.setattr(voice.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(status=200))
    assert voice.server_up() is True
    monkeypatch.setattr(voice.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert voice.server_up() is False


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #
def test_kill_tree_none_is_noop(voice):
    voice.kill_tree(None)                        # must not raise


def test_kill_tree_sends_sigterm_then_sigkill(voice, monkeypatch):
    signals = []
    monkeypatch.setattr(voice.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(voice.os, "killpg", lambda pg, sig: signals.append(sig))
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    class AlwaysAlive:
        pid = 9

        def poll(self):
            return None

    voice.kill_tree(AlwaysAlive())               # type: ignore[arg-type]
    assert signals[0] == voice.signal.SIGTERM
    assert signals[-1] == voice.signal.SIGKILL


def test_start_server_success(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(voice, "LOG", str(tmp_path / "v.log"))
    monkeypatch.setattr(voice.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(voice, "server_up", lambda: True)
    proc = voice.start_server()
    assert proc is FakePopen.last


def test_start_server_missing_script_exits(voice, monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: None)
    with pytest.raises(SystemExit):
        voice.start_server()


def test_start_server_failure_raises(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(voice, "LOG", str(tmp_path / "v.log"))

    class Dead(FakePopen):
        def poll(self):
            return 1

    monkeypatch.setattr(voice.subprocess, "Popen", Dead)
    monkeypatch.setattr(voice, "server_up", lambda: False)
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not come up"):
        voice.start_server()


def test_ensure_server_returns_existing_when_up(voice, monkeypatch):
    monkeypatch.setattr(voice, "server_up", lambda: True)
    voice._PROC = "sentinel"
    assert voice.ensure_server() == "sentinel"


def test_ensure_server_starts_when_down(voice, monkeypatch):
    monkeypatch.setattr(voice, "server_up", lambda: False)
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    monkeypatch.setattr(voice, "start_server", lambda: "NEWPROC")
    voice._PROC = None
    assert voice.ensure_server() == "NEWPROC"


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
def test_get_session_reuses_state_file(voice, tmp_path, monkeypatch):
    state = tmp_path / "voice-session"
    state.write_text("S123")
    monkeypatch.setattr(voice, "STATE_FILE", str(state))
    monkeypatch.setattr(voice, "api", lambda m, p, **k: {"id": "S123"})
    assert voice.get_session() == "S123"


def test_get_session_force_new_creates_and_persists(voice, tmp_path, monkeypatch):
    state = tmp_path / "voice-session"
    monkeypatch.setattr(voice, "STATE_FILE", str(state))
    monkeypatch.setattr(voice, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "api", lambda m, p, *a, **k: {"id": "BRANDNEW"})
    assert voice.get_session(force_new=True) == "BRANDNEW"
    assert state.read_text() == "BRANDNEW"


def test_get_session_sends_model_when_env_set(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "STATE_FILE", str(tmp_path / "s"))
    monkeypatch.setattr(voice, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "MODEL_ENV", "openai/gpt-x")
    captured: dict[str, object] = {}

    def fake_api(method, path, body=None, **k):
        captured["body"] = body
        return {"id": "M1"}

    monkeypatch.setattr(voice, "api", fake_api)
    voice.get_session(force_new=True)
    assert captured["body"] == {"title": "ZYVO Voice",
                                "model": {"providerID": "openai", "id": "gpt-x"}}


# --------------------------------------------------------------------------- #
# Permission watchdog
# --------------------------------------------------------------------------- #
def test_watchdog_stop_sets_flag(voice):
    wd = voice.PermissionWatchdog()
    assert wd._stop is False
    wd.stop()
    assert wd._stop is True


def test_watchdog_replies_then_stops(voice, monkeypatch):
    posted: list[tuple[str, object]] = []

    def fake_api(method, path, body=None, timeout=None):
        if method == "GET":
            return [{"id": "REQ1"}]
        posted.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(voice, "api", fake_api)
    wd = voice.PermissionWatchdog()
    wd.sid = "S"
    monkeypatch.setattr(voice.time, "sleep", lambda s: wd.stop())
    wd.run()
    assert posted and posted[0][1] == {"reply": "always"}


def test_watchdog_waits_without_sid(voice, monkeypatch):
    wd = voice.PermissionWatchdog()
    monkeypatch.setattr(voice.time, "sleep", lambda s: wd.stop())
    wd.run()                                     # no sid -> just sleeps, no crash


# --------------------------------------------------------------------------- #
# Turn execution
# --------------------------------------------------------------------------- #
def test_action_detail_prefers_command(voice):
    assert voice.action_detail({"command": "ls -la"}) == "ls -la"
    assert voice.action_detail({"filePath": "/x"}) == "/x"
    assert voice.action_detail({"path": "/y"}) == "/y"
    assert voice.action_detail({"pattern": "  "}) == ""
    assert voice.action_detail("not-a-dict") == ""


def test_session_message_count_variants(voice, monkeypatch):
    monkeypatch.setattr(voice, "api", lambda *a, **k: [1, 2, 3])
    assert voice.session_message_count("s") == 3
    monkeypatch.setattr(voice, "api", lambda *a, **k: {"not": "a list"})
    assert voice.session_message_count("s") == 0
    monkeypatch.setattr(voice, "api",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert voice.session_message_count("s") == 0


def test_collect_turn_tools_extracts_tools_and_text(voice, monkeypatch):
    msgs = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "q"}]},
        {"info": {"role": "assistant"}, "parts": [
            {"type": "tool", "tool": "bash"},
            {"type": "text", "text": "final answer"},
            {"type": "text", "text": "  ", "synthetic": False},
        ]},
    ]
    monkeypatch.setattr(voice, "api", lambda *a, **k: msgs)
    tools, last = voice.collect_turn_tools("s", 1)
    assert [t["tool"] for t in tools] == ["bash"]
    assert last == "final answer"


def test_collect_turn_tools_handles_errors(voice, monkeypatch):
    monkeypatch.setattr(voice, "api",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert voice.collect_turn_tools("s", 0) == ([], "")
    monkeypatch.setattr(voice, "api", lambda *a, **k: {"not": "list"})
    assert voice.collect_turn_tools("s", 0) == ([], "")


def test_parse_tools_roles_tools_temp(voice):
    tools = [
        {"tool": "bash", "state": {"input": {"command": "ls"}, "status": "completed",
                                   "metadata": {"exit": 0}}},
        {"tool": "bash", "state": {"status": "completed",
                                   "metadata": {"exit": 2}, "output": "boom"}},
        {"tool": "write", "state": {"input": {"filePath": "/new.txt"},
                                    "status": "completed",
                                    "metadata": {"exists": False}}},
        {"tool": "edit", "state": {"input": {"filePath": "/mod.txt"},
                                   "status": "completed", "metadata": {"exists": True}}},
        {"tool": "todoread", "state": {"status": "completed"}},   # skipped
    ]
    actions, created, modified = voice.parse_tools(tools)
    assert [a["tool"] for a in actions] == ["bash", "bash", "write", "edit"]
    assert actions[0]["ok"] is True
    assert actions[1]["ok"] is False and "boom" in actions[1]["error"]
    assert created == ["/new.txt"]
    assert modified == ["/mod.txt"]


def test_run_turn_returns_reply_and_tools(voice, monkeypatch):
    monkeypatch.setattr(voice, "session_message_count", lambda s: 0)
    monkeypatch.setattr(voice, "collect_turn_tools", lambda s, n: ([], ""))
    monkeypatch.setattr(voice, "api",
                        lambda *a, **k: {"parts": [{"type": "text", "text": "pong"}]})
    reply, tools = voice.run_turn("s", "ping")
    assert reply == "pong" and tools == []


def test_run_turn_falls_back_to_last_text(voice, monkeypatch):
    monkeypatch.setattr(voice, "session_message_count", lambda s: 0)
    monkeypatch.setattr(voice, "collect_turn_tools",
                        lambda s, n: ([{"tool": "bash"}], "recovered"))
    monkeypatch.setattr(voice, "api", lambda *a, **k: {"parts": []})
    reply, tools = voice.run_turn("s", "hi")
    assert reply == "recovered"
    assert tools == [{"tool": "bash"}]


def test_run_turn_surfaces_api_error(voice, monkeypatch):
    monkeypatch.setattr(voice, "session_message_count", lambda s: 0)
    monkeypatch.setattr(voice, "collect_turn_tools", lambda s, n: ([], ""))
    msg = {"parts": [], "info": {"error": {"data": {"message": "boom"}, "name": "X"}}}
    monkeypatch.setattr(voice, "api", lambda *a, **k: msg)
    reply, _ = voice.run_turn("s", "hi")
    assert "boom" in reply


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def test_short_truncates(voice):
    assert voice._short("  a   b ") == "a b"
    long = "x" * 200
    assert voice._short(long, 10) == "x" * 10 + "…"


def test_render_report_prints_card(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    actions = [{"tool": "bash", "detail": "ls", "ok": True, "status": "completed", "error": ""},
               {"tool": "edit", "detail": "/f", "ok": False, "status": "error", "error": "no"}]
    voice.render_report("do it", "done", actions, ["/new"], ["/mod"])
    out = capsys.readouterr().out
    assert "do it" in out and "done" in out
    assert "/new" in out and "/mod" in out


def test_render_report_no_actions(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    voice.render_report("hi", "", [], [], [])
    assert "hi" in capsys.readouterr().out


def test_speech_report_success(voice):
    actions = [{"tool": "bash", "detail": "ls", "ok": True, "status": "completed", "error": ""}]
    s = voice.speech_report("do it", "done", actions, ["/a"], ["/b"])
    assert "فهمت منك: do it." in s
    assert "نفذت 1 إجراء." in s
    assert "أنشأت الملفات: /a." in s
    assert "عدلت الملفات: /b." in s
    assert "نجحت كل الإجراءات." in s
    assert "الرد: done" in s


def test_speech_report_failure_and_no_files(voice):
    actions = [{"tool": "bash", "detail": "ls", "ok": False, "status": "error", "error": "nope"}]
    s = voice.speech_report("do", "", actions, [], [])
    assert "فشل" in s
    assert "ما في ملفات" in s
    assert "فيه إجراءات فشلت." in s
    assert "ما نفذت أي إجراء" not in s


def test_speech_report_chat_only(voice):
    s = voice.speech_report("hi", "answer", [], [], [])
    assert "ما نفذت أي إجراء، جاوبت فقط." in s
    assert "الرد: answer" in s


# --------------------------------------------------------------------------- #
# Voice in / out
# --------------------------------------------------------------------------- #
def test_listen_json_text(voice, monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout='{"text":"hi"}'))
    assert voice.listen() == "hi"


def test_listen_json_error_returns_empty(voice, monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout='{"error":"no mic"}'))
    assert voice.listen() == ""


def test_listen_plain_and_failure(voice, monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="plain words"))
    assert voice.listen() == "plain words"
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=""))
    assert voice.listen() == ""
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert voice.listen() == ""


def test_clean_for_speech_strips_noise(voice):
    assert voice.clean_for_speech("`code`") == "code"
    assert voice.clean_for_speech("[label](http://x)") == "label"
    assert voice.clean_for_speech("http://x.com") == ""
    assert voice.clean_for_speech("```a\nb```") == ""
    assert voice.clean_for_speech("- item") == "item"
    assert voice.clean_for_speech("## head") == "head"
    assert voice.clean_for_speech("a    b") == "a b"


def test_speak_respects_no_speak(voice, monkeypatch):
    calls = []
    monkeypatch.setattr(voice.subprocess, "run", lambda cmd, *a, **k: calls.append(cmd))
    voice.speak("hello", no_speak=True)
    assert calls == []


def test_speak_empty_is_noop(voice, monkeypatch):
    calls = []
    monkeypatch.setattr(voice.subprocess, "run", lambda cmd, *a, **k: calls.append(cmd))
    voice.speak("   ", no_speak=False)
    assert calls == []


def test_speak_chunks_and_uses_lang(voice, monkeypatch):
    calls = []
    monkeypatch.setattr(voice.subprocess, "run", lambda cmd, *a, **k: calls.append(cmd))
    monkeypatch.setattr(voice, "TTS_LANG", "ar")
    voice.speak("x" * 3500)
    assert len(calls) == 2
    assert calls[0][:3] == ["termux-tts-speak", "-l", "ar"]


def test_buzz_swallows_errors(voice, monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    voice.buzz()                                 # must not raise


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_main_text_mode(voice, monkeypatch):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--text", "hi"])
    monkeypatch.setattr(voice, "ensure_server", lambda: "P")
    monkeypatch.setattr(voice, "get_session", lambda force_new=False: "S")
    monkeypatch.setattr(voice, "run_turn", lambda s, t: ("done", []))
    monkeypatch.setattr(voice, "parse_tools", lambda t: ([], [], []))
    rendered: list[object] = []
    monkeypatch.setattr(voice, "render_report",
                        lambda *a: rendered.append(a))
    monkeypatch.setattr(voice, "speak", lambda *a, **k: None)

    class FakeWD:
        def __init__(self):
            self.sid = None

        def start(self):
            pass

    monkeypatch.setattr(voice, "PermissionWatchdog", FakeWD)
    voice.main()
    assert rendered and rendered[0][0] == "hi"


# --------------------------------------------------------------------------- #
# Extra edge paths (coverage hardening)
# --------------------------------------------------------------------------- #
def test_show_without_color(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    voice.show("plain")
    out = capsys.readouterr().out
    assert "plain" in out
    assert voice.C_RESET not in out


def test_start_server_waits_until_ready(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(voice, "LOG", str(tmp_path / "v.log"))
    monkeypatch.setattr(voice.subprocess, "Popen", FakePopen)
    state = {"n": 0}

    def server_up():
        state["n"] += 1
        return state["n"] >= 2

    monkeypatch.setattr(voice, "server_up", server_up)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    proc = voice.start_server()
    assert proc is FakePopen.last


def test_kill_tree_fallback_when_getpgid_fails(voice, monkeypatch):
    monkeypatch.setattr(voice.os, "getpgid",
                        lambda pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    class Zombie:
        pid = 9

        def poll(self):
            return None

        def terminate(self):
            raise RuntimeError("cannot terminate")

    voice.kill_tree(Zombie())                       # type: ignore[arg-type]


def test_kill_tree_returns_when_proc_already_dead(voice, monkeypatch):
    monkeypatch.setattr(voice.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(voice.os, "killpg", lambda pg, sig: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    class Dead:
        pid = 9

        def poll(self):
            return 0

    voice.kill_tree(Dead())                         # type: ignore[arg-type]


def test_ensure_server_waits_for_existing_proc(voice, monkeypatch):
    monkeypatch.setattr(voice, "server_up", lambda: False)
    killed = []
    monkeypatch.setattr(voice, "kill_tree", lambda p: killed.append(p))
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    class Live:
        def poll(self):
            return None

    live = Live()
    voice._PROC = live
    monkeypatch.setattr(voice, "start_server", lambda: "NEW")
    assert voice.ensure_server() == "NEW"
    assert killed == [live]


def test_ensure_server_gives_up_returns_none(voice, monkeypatch):
    monkeypatch.setattr(voice, "server_up", lambda: False)
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    def boom():
        raise RuntimeError("cannot start")

    monkeypatch.setattr(voice, "start_server", boom)
    assert voice.ensure_server() is None


def test_get_session_creates_when_state_empty(voice, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("", encoding="utf-8")
    monkeypatch.setattr(voice, "STATE_FILE", str(state))
    monkeypatch.setattr(voice, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "api", lambda *a, **k: {"id": "NEW"})
    assert voice.get_session() == "NEW"


def test_get_session_creates_when_api_response_falsy(voice, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(voice, "STATE_FILE", str(state))
    monkeypatch.setattr(voice, "STATE_DIR", str(tmp_path))

    def api(method, path, *a, **k):
        if method == "GET":
            return None
        return {"id": "NEW2"}

    monkeypatch.setattr(voice, "api", api)
    assert voice.get_session() == "NEW2"


def test_get_session_creates_when_api_raises(voice, tmp_path, monkeypatch):
    state = tmp_path / "sess"
    state.write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(voice, "STATE_FILE", str(state))
    monkeypatch.setattr(voice, "STATE_DIR", str(tmp_path))

    def api(method, path, *a, **k):
        if method == "GET":
            raise RuntimeError("404")
        return {"id": "NEW3"}

    monkeypatch.setattr(voice, "api", api)
    assert voice.get_session() == "NEW3"


def test_watchdog_stop_flag_and_immediate_return(voice):
    wd = voice.PermissionWatchdog()
    assert wd._stop is False
    wd.stop()
    assert wd._stop is True
    wd.run()                                        # returns at once


def test_watchdog_skips_without_sid(voice, monkeypatch):
    monkeypatch.setattr(voice.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = voice.PermissionWatchdog()
    wd.sid = None
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_watchdog_nonlist_missing_rid_and_post_fallback(voice, monkeypatch):
    posted = []

    def api(method, path, body=None, *a, **k):
        if method == "GET":
            if path == "/session/S/permission":
                return [{"id": "R1"}, {}]           # 2nd has no rid
            return {"not": "a list"}                # non-list branch
        posted.append(path)
        if path.startswith("/session/S/permission/R1/reply"):
            raise RuntimeError("bad")
        return {}

    monkeypatch.setattr(voice, "api", api)
    monkeypatch.setattr(voice.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = voice.PermissionWatchdog()
    wd.sid = "S"
    with pytest.raises(KeyboardInterrupt):
        wd.run()
    assert any(p.endswith("/permission/R1/reply") for p in posted)


def test_watchdog_get_error_is_swallowed(voice, monkeypatch):
    def api(method, path, body=None, *a, **k):
        if method == "GET":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(voice, "api", api)
    monkeypatch.setattr(voice.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    wd = voice.PermissionWatchdog()
    wd.sid = "S"
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_collect_turn_tools_skips_non_assistant(voice, monkeypatch):
    msgs = [
        {"info": {"role": "user"}, "parts": [{"type": "tool", "tool": "x"}]},
        {"info": {"role": "assistant"}, "parts": [
            {"type": "tool", "tool": "bash"},
            {"type": "text", "text": "hi", "synthetic": False},
        ]},
    ]
    monkeypatch.setattr(voice, "api", lambda *a, **k: msgs)
    tools, last_text = voice.collect_turn_tools("S", 0)
    assert [t["tool"] for t in tools] == ["bash"]
    assert last_text == "hi"


def test_parse_tools_file_tool_without_path(voice):
    actions, created, modified = voice.parse_tools([
        {"tool": "edit", "state": {"input": {}, "status": "completed",
                                   "metadata": {}}},
    ])
    assert created == [] and modified == []
    assert len(actions) == 1


def test_run_turn_exhausts_all_retries(voice, monkeypatch):
    monkeypatch.setattr(voice, "session_message_count", lambda sid: 0)
    monkeypatch.setattr(voice, "api", mock.Mock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(voice, "ensure_server", lambda: "P")
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    monkeypatch.setattr(voice, "collect_turn_tools", lambda sid, n0: ([], ""))
    reply, tools = voice.run_turn("S", "hi")
    assert reply == "" and tools == []


def test_run_turn_uses_long_sleep_when_server_missing(voice, monkeypatch):
    sleeps = []
    state = {"n": 0}

    def api(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("down")
        return {"parts": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(voice, "session_message_count", lambda sid: 0)
    monkeypatch.setattr(voice, "api", api)
    monkeypatch.setattr(voice, "ensure_server", lambda: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(voice, "collect_turn_tools", lambda sid, n0: ([], ""))
    reply, _ = voice.run_turn("S", "hi")
    assert reply == "ok"
    assert 2 in sleeps


def test_render_report_action_without_detail(voice, capsys, monkeypatch):
    monkeypatch.setattr(voice, "_RTL", "")
    actions = [{"tool": "read", "detail": "", "ok": True,
                "status": "completed", "error": ""}]
    voice.render_report("h", "r", actions, [], [])
    assert "قرأت" in capsys.readouterr().out


def test_listen_json_non_dict_returns_raw(voice, monkeypatch):
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="[1,2]"))
    assert voice.listen() == "[1,2]"


def test_speak_without_lang_and_swallows_error(voice, monkeypatch):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        raise OSError("no tts")

    monkeypatch.setattr(voice, "TTS_LANG", "")
    monkeypatch.setattr(voice.subprocess, "run", run)
    voice.speak("hello")                            # must not raise
    assert calls[0] == ["termux-tts-speak", "hello"]


def test_main_exits_when_server_unavailable(voice, monkeypatch):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--text", "hi"])
    monkeypatch.setattr(voice, "ensure_server", lambda: None)
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    with pytest.raises(SystemExit):
        voice.main()


class _FakeWD:
    def __init__(self):
        self.sid = None

    def start(self):
        pass


def _patch_main_common(voice, monkeypatch, run_turn=None):
    monkeypatch.setattr(voice, "ensure_server", lambda: "P")
    monkeypatch.setattr(voice, "get_session", lambda force_new=False: "S")
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    monkeypatch.setattr(voice, "PermissionWatchdog", _FakeWD)
    if run_turn is not None:
        monkeypatch.setattr(voice, "run_turn", run_turn)


def test_main_interactive_loop_no_mic(voice, monkeypatch, capsys):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--no-mic", "--no-speak"])
    _patch_main_common(voice, monkeypatch, lambda s, t: ("done", []))
    monkeypatch.setattr(voice, "parse_tools", lambda t: ([], [], []))
    monkeypatch.setattr(voice, "render_report", lambda *a: None)
    monkeypatch.setattr(voice, "speak", lambda *a, **k: None)

    inputs = iter(["hello", "", "stop"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    voice.main()
    assert "banaf-fez" in capsys.readouterr().out


def test_main_mic_loop_no_report(voice, monkeypatch):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--no-report"])
    _patch_main_common(voice, monkeypatch, lambda s, t: ("answer", []))
    monkeypatch.setattr(voice, "parse_tools", lambda t: ([], [], []))
    monkeypatch.setattr(voice, "render_report", lambda *a: None)
    spoken = []
    monkeypatch.setattr(voice, "speak", lambda t, *a, **k: spoken.append(t))
    monkeypatch.setattr(voice, "buzz", lambda: None)
    slept = []
    monkeypatch.setattr(voice.time, "sleep", lambda s: slept.append(s))

    seq = iter(["", "do it", "stop"])
    monkeypatch.setattr(voice, "listen", lambda: next(seq))
    voice.main()
    assert any("ما سمعتك" in s for s in spoken)      # empty-heard branch
    assert "answer" in spoken                        # --no-report -> reply only
    assert 0.4 in slept


def test_main_handles_keyboard_interrupt(voice, monkeypatch, capsys):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--no-mic"])
    _patch_main_common(voice, monkeypatch)

    def boom(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", boom)
    voice.main()
    assert "et2af" in capsys.readouterr().out


def test_main_chat_mode_sets_reject(voice, monkeypatch):
    monkeypatch.setattr(voice.sys, "argv",
                        ["zyvo-voice.py", "--chat", "--text", "hi"])
    monkeypatch.setattr(voice, "ensure_server", lambda: "P")
    monkeypatch.setattr(voice, "get_session", lambda force_new=False: "S")
    monkeypatch.setattr(voice, "run_turn", lambda s, t: ("done", []))
    monkeypatch.setattr(voice, "parse_tools", lambda t: ([], [], []))
    monkeypatch.setattr(voice, "render_report", lambda *a: None)
    monkeypatch.setattr(voice, "speak", lambda *a, **k: None)
    monkeypatch.setattr(voice, "PermissionWatchdog", _DummyWD)
    voice.main()
    assert voice.ACTION == "reject"


class _DummyWD:
    def __init__(self) -> None:
        self.sid: str | None = None

    def start(self) -> None:
        pass


def test_start_server_raises_when_never_ready(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(voice, "LOG", str(tmp_path / "v.log"))

    class Zombie(FakePopen):
        def poll(self):
            return None                             # never exits

    monkeypatch.setattr(voice.subprocess, "Popen", Zombie)
    monkeypatch.setattr(voice, "server_up", lambda: False)
    monkeypatch.setattr(voice, "kill_tree", lambda p: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not come up"):
        voice.start_server()


def test_ensure_server_wait_finds_server(voice, monkeypatch):
    calls = {"n": 0}

    def server_up():
        calls["n"] += 1
        return calls["n"] >= 2                       # False at top, True in wait

    monkeypatch.setattr(voice, "server_up", server_up)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    class Live:
        def poll(self):
            return None

    live = Live()
    voice._PROC = live
    assert voice.ensure_server() is live


def test_module_detects_rtl_via_path_lookup(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path as _P

    root = _P(__file__).resolve().parent.parent
    monkeypatch.setattr("os.path.exists", lambda p: False)      # "~/bin/rtl" absent
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/rtl")
    spec = importlib.util.spec_from_file_location(
        "zyvo_voice_reload", str(root / "zyvo-voice.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["zyvo_voice_reload"] = module
    spec.loader.exec_module(module)
    assert module._RTL == "/usr/bin/rtl"


def test_watchdog_exhausts_reply_paths(voice, monkeypatch):
    monkeypatch.setattr(voice.time, "sleep",
                        lambda s: (_ for _ in ()).throw(KeyboardInterrupt))

    def api(method, path, body=None, *a, **k):
        if method == "GET":
            return [{"id": "R1"}]
        raise RuntimeError("both reply paths fail")

    monkeypatch.setattr(voice, "api", api)
    wd = voice.PermissionWatchdog()
    wd.sid = "S"
    with pytest.raises(KeyboardInterrupt):
        wd.run()


def test_main_eof_breaks_loop(voice, monkeypatch, capsys):
    monkeypatch.setattr(voice.sys, "argv", ["zyvo-voice.py", "--no-mic", "--no-speak"])
    _patch_main_common(voice, monkeypatch)

    def raise_eof(*a):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    voice.main()
    assert "session S" in capsys.readouterr().out
