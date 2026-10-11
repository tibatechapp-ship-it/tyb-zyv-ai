"""Tests for src.core — types, backend protocol, ZYVO client, system info."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from src.core import sysinfo
from src.core.backend import EchoBackend, LLMBackend
from src.core.types import AgentResult, Message
from src.core.zyvo_client import (
    ZyvoBackend,
    ZyvoClient,
    ZyvoError,
    _extract_text,
    _flatten,
)


# ── types ────────────────────────────────────────────────────────────────
def test_message_fields() -> None:
    message = Message("user", "hi")
    assert message.role == "user"
    assert message.content == "hi"


def test_agent_result_to_dict() -> None:
    result = AgentResult(agent="a", ok=True, output="x", duration_s=1.5)
    data = result.as_dict()
    assert data["agent"] == "a"
    assert data["ok"] is True
    assert data["output"] == "x"
    assert data["error"] is None
    assert data["duration_s"] == 1.5


def test_agent_result_defaults() -> None:
    result = AgentResult(agent="a", ok=False, error="boom")
    data = result.as_dict()
    assert data == {
        "agent": "a",
        "ok": False,
        "output": "",
        "error": "boom",
        "duration_s": 0.0,
    }


# ── backend protocol ─────────────────────────────────────────────────────
def test_echo_backend_generate() -> None:
    assert EchoBackend().generate([Message("user", "hi")]) == "echo: hi"


def test_echo_backend_empty_and_custom_prefix() -> None:
    assert EchoBackend(">> ").generate([]) == ">> "


def test_echo_backend_satisfies_protocol() -> None:
    assert isinstance(EchoBackend(), LLMBackend)


# ── module-level helpers ─────────────────────────────────────────────────
def test_extract_text_string() -> None:
    assert _extract_text("hello") == "hello"


@pytest.mark.parametrize("value", [None, 123, [1, 2]])
def test_extract_text_non_dict(value: object) -> None:
    assert _extract_text(value) == ""


def test_extract_text_parts_filters_synthetic_and_tools() -> None:
    data = {
        "parts": [
            {"type": "text", "text": "a"},
            {"type": "tool", "text": "b"},
            {"type": "text", "text": "c", "synthetic": True},
            {"type": "text", "text": "d"},
        ]
    }
    assert _extract_text(data) == "a\nd"


def test_extract_text_missing_or_bad_parts() -> None:
    assert _extract_text({"foo": 1}) == ""
    assert _extract_text({"parts": "nope"}) == ""
    assert _extract_text({"parts": ["x", {"type": "text", "text": "y"}]}) == "y"


def test_flatten_roles_and_model_hint() -> None:
    messages = [Message("system", "S"), Message("user", "U"), Message("assistant", "A")]
    out = _flatten(messages, "m1")
    assert "[system]" in out and "S" in out
    assert "[assistant]" in out and "A" in out
    assert "[model hint: m1]" in out


def test_flatten_without_model() -> None:
    assert "[model hint" not in _flatten([Message("user", "U")], None)


# ── ZyvoClient ───────────────────────────────────────────────────────────
class _Resp:
    """Context-manager + iterable stand-in for urlopen()'s return value."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._body.splitlines(keepends=True))


def test_base_url() -> None:
    assert ZyvoClient("host", 1234).base_url == "http://host:1234"


def test_request_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(b'{"ok": 1}'))
    assert ZyvoClient()._request("GET", "/x") == {"ok": 1}


def test_request_passes_request_positionally(monkeypatch: pytest.MonkeyPatch) -> None:
    """urlopen()'s first parameter is `url` — the Request must be positional."""
    seen: dict[str, object] = {}

    def fake_urlopen(url: object, *args: object, **kwargs: object) -> _Resp:
        seen["url"] = url
        return _Resp(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ZyvoClient()._request("GET", "/x")
    assert isinstance(seen["url"], urllib.request.Request)


def test_request_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(b""))
    assert ZyvoClient()._request("GET", "/x") is None


def test_request_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(b"plain"))
    assert ZyvoClient()._request("GET", "/x") == "plain"


def test_request_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise urllib.error.HTTPError("u", 500, "err", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ZyvoError):
        ZyvoClient()._request("GET", "/x")


def test_request_url_error_with_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ZyvoError):
        ZyvoClient()._request("POST", "/x", {"a": 1})


def test_health_true(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Conn())
    assert ZyvoClient().health() is True


def test_health_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("refused")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert ZyvoClient().health() is False


def test_create_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZyvoClient()
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"id": "s1"})
    assert client.create_session("t") == "s1"


@pytest.mark.parametrize("reply", [{}, "nope", None])
def test_create_session_without_id(monkeypatch: pytest.MonkeyPatch, reply: Any) -> None:
    client = ZyvoClient()
    monkeypatch.setattr(client, "_request", lambda *a, **k: reply)
    with pytest.raises(ZyvoError):
        client.create_session()


def test_send_extracts_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZyvoClient()
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"parts": [{"type": "text", "text": "yo"}]}
    )
    assert client.send("s", "hi") == "yo"


def test_list_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZyvoClient()
    monkeypatch.setattr(client, "_request", lambda *a, **k: [{"id": "1"}])
    assert client.list_permissions("s") == [{"id": "1"}]


def test_list_permissions_non_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZyvoClient()
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"x": 1})
    assert client.list_permissions("s") == []


def test_reply_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    client = ZyvoClient()

    def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> None:
        captured.update(method=method, path=path, body=body)

    monkeypatch.setattr(client, "_request", fake_request)
    client.reply_permission("s", "r1")
    assert captured["path"] == "/session/s/permission/r1/reply"
    assert captured["body"] == {"reply": "once"}


def test_events_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = b'data: {"a": 1}\n\nnotdata\ndata: \ndata: badjson\n: comment\ndata: {"b": 2}\n'
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(lines))
    assert list(ZyvoClient().events()) == [{"a": 1}, {"b": 2}]


# ── ZyvoBackend ──────────────────────────────────────────────────────────
class _FakeClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def create_session(self, title: str = "agent") -> str:
        self.created.append(title)
        return f"s{len(self.created)}"

    def send(self, session_id: str, text: str) -> str:
        self.sent.append((session_id, text))
        return "reply"


def test_zyvo_backend_creates_and_reuses_session() -> None:
    fake = _FakeClient()
    backend = ZyvoBackend(fake)  # type: ignore[arg-type]
    assert backend.session_id is None
    assert backend.generate([Message("system", "S"), Message("user", "U")]) == "reply"
    assert backend.session_id == "s1"
    assert fake.created == ["agent"]
    backend.generate([Message("user", "U2")])
    assert fake.created == ["agent"]  # reused
    assert fake.sent[1][1].endswith("U2")


def test_zyvo_backend_with_given_session() -> None:
    fake = _FakeClient()
    backend = ZyvoBackend(fake, session_id="given", title="t")  # type: ignore[arg-type]
    backend.generate([Message("user", "x")])
    assert fake.created == []
    assert fake.sent == [("given", "x")]


# ── sysinfo ──────────────────────────────────────────────────────────────
def test_cpu_count_positive() -> None:
    assert sysinfo.cpu_count() >= 1


def test_load_average_shape() -> None:
    load = sysinfo.load_average()
    assert load is None or len(load) == 3


def test_memory_shape() -> None:
    mem = sysinfo.memory()
    assert set(mem) == {"total_kb", "available_kb", "used_kb", "used_percent"}
    assert mem["total_kb"] >= 0


def test_uptime_non_negative() -> None:
    assert sysinfo.uptime() >= 0.0


def test_battery_shape() -> None:
    battery = sysinfo.battery()
    assert battery is None or {"percent", "status", "name"} <= set(battery)


def test_snapshot_keys() -> None:
    snapshot = sysinfo.snapshot()
    assert {"cpu_count", "load_average", "memory", "uptime", "battery"} <= set(snapshot)


def test_memory_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(sysinfo, "_PROC", Path("/nonexistent-proc"))
    assert sysinfo.memory() == {}
