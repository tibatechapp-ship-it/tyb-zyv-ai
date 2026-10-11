"""Thin, stdlib-only client for the local ZYVO HTTP API.

This is the reusable "harness": the same server the Telegram bot and the voice
assistant already drive. Agents talk to it through :class:`ZyvoBackend`.

Endpoints used (localhost, default port 4141)::

    POST /session                                -> {"id": "..."}
    POST /session/{id}/message  {parts:[...]}    -> {"parts":[...]}
    GET  /session/{id}/permission                -> [ {"id": ...}, ... ]
    POST /session/{id}/permission/{rid}/reply    {"reply": "once"|"always"|"reject"}
    GET  /event                                  -> Server-Sent Events stream

Only the Python standard library is used (``urllib``/``socket``), so this runs
unchanged on Termux where no ``requests``/``httpx`` is installed.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from typing import Any

from src.core.types import Message

__all__ = ["ZyvoClient", "ZyvoBackend", "ZyvoError", "DEFAULT_HOST", "DEFAULT_PORT"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4141


class ZyvoError(RuntimeError):
    """Raised when the ZYVO HTTP API cannot be reached or answers with an error."""


def _extract_text(data: Any) -> str:
    """Pull the assistant text out of a ZYVO ``/message`` response.

    Tool calls and synthetic parts are ignored; only genuine ``text`` parts are
    joined. Accepts both the structured dict form and a bare string.
    """
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    parts = data.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and not part.get("synthetic"):
            chunks.append(str(part.get("text", "")))
    return "\n".join(chunks).strip()


def _flatten(messages: Sequence[Message], model: str | None) -> str:
    """Reduce a conversation to a single prompt string for the ZYVO ``/message`` API."""
    blocks: list[str] = []
    for message in messages:
        if message.role == "system":
            blocks.append(f"[system]\n{message.content}")
        elif message.role == "assistant":
            blocks.append(f"[assistant]\n{message.content}")
        else:
            blocks.append(message.content)
    if model:
        blocks.append(f"[model hint: {model}]")
    return "\n\n".join(blocks)


class ZyvoClient:
    """Low-level HTTP client for one ZYVO server instance."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = 180.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ── transport ──────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise ZyvoError(f"{method} {request.full_url} -> HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network path
            raise ZyvoError(f"{method} {request.full_url} -> {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # -- health ------------------------------------------------------------
    def health(self, timeout: float = 1.0) -> bool:
        """True when a TCP connection to the server succeeds."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    # -- sessions ----------------------------------------------------------
    def create_session(self, title: str = "agent") -> str:
        data = self._request("POST", "/session", {"title": title})
        session_id = data.get("id") if isinstance(data, dict) else None
        if not session_id:
            raise ZyvoError("create_session: response contained no 'id'")
        return str(session_id)

    def send(self, session_id: str, text: str) -> str:
        """Send a text prompt and return the assistant's reply text."""
        data = self._request(
            "POST",
            f"/session/{session_id}/message",
            {"parts": [{"type": "text", "text": text}]},
        )
        return _extract_text(data)

    # -- permissions -------------------------------------------------------
    def list_permissions(self, session_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/session/{session_id}/permission")
        return data if isinstance(data, list) else []

    def reply_permission(self, session_id: str, request_id: str, reply: str = "once") -> None:
        self._request(
            "POST",
            f"/session/{session_id}/permission/{request_id}/reply",
            {"reply": reply},
        )

    # -- events (Server-Sent Events) --------------------------------------
    def events(self) -> Iterator[dict[str, Any]]:
        """Yield decoded SSE events from ``GET /event`` (blocks while streaming)."""
        req = urllib.request.Request(
            f"http://{self.host}:{self.port}/event",
            headers={"Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # pragma: no cover
            for raw in resp:  # pragma: no cover - network path
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue


class ZyvoBackend:
    """An :class:`~src.core.backend.LLMBackend` that drives the ZYVO HTTP API.

    A session is created lazily on first use and reused for every later call, so
    an agent keeps conversational context across tasks.
    """

    def __init__(
        self,
        client: ZyvoClient,
        *,
        session_id: str | None = None,
        title: str = "agent",
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._title = title

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = self._client.create_session(self._title)
        return self._session_id

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        session_id = self._ensure_session()
        return self._client.send(session_id, _flatten(messages, model))
