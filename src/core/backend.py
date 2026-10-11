"""The LLM backend abstraction.

An *agent* never talks to a model directly — it talks to a small ``LLMBackend``
object. That indirection is what makes the whole framework testable offline
(``EchoBackend``) while the real system drives the local ZYVO HTTP API
(:class:`src.core.zyvo_client.ZyvoBackend`).

The interface is intentionally tiny: one synchronous ``generate`` call. Retries,
queues, caching, rate limiting and model fallback live *above* it, composed by
:class:`src.core.resilience.ResilientBackend`, not here.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from src.core.types import Message

__all__ = ["LLMBackend", "EchoBackend"]


@runtime_checkable
class LLMBackend(Protocol):
    """Anything that can turn a list of messages into a text completion."""

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        """Return the model's reply for ``messages``.

        ``messages`` is an ordered conversation; a leading ``role="system"``
        message (if present) carries the agent's persona.
        """
        ...  # pragma: no cover - protocol definition


class EchoBackend:
    """Offline backend used by tests and for running without a server.

    It echoes the last message back with a configurable prefix, which makes the
    whole agent/pattern/TUI stack exercisable with zero network and zero ZYVO.
    """

    def __init__(self, prefix: str = "echo: ") -> None:
        self._prefix = prefix

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        last = messages[-1].content if messages else ""
        return f"{self._prefix}{last}"
