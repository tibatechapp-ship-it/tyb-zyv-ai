"""Plain data types shared across the agent framework.

Kept dependency-free (standard library only) so every layer — agents, patterns,
the TUI and the tests — can import them without pulling in heavyweight code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class Message:
    """A single chat message handed to an :class:`~src.core.backend.LLMBackend`."""

    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serialisable representation."""
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class AgentResult:
    """The outcome of one agent run."""

    agent: str
    ok: bool
    output: str = ""
    error: str | None = None
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable snapshot (used by the TUI/metrics)."""
        return {
            "agent": self.agent,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "duration_s": round(self.duration_s, 4),
        }
