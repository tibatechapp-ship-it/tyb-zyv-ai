"""Per-agent runtime state (status + last activity)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

__all__ = ["AgentStatus", "AgentState"]


class AgentStatus(str, Enum):
    """Lifecycle status shown in the TUI and reported by metrics."""

    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    DISABLED = "disabled"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AgentState:
    """Mutable state of a single agent.

    Kept deliberately small: a status, the last task (truncated), the last error
    and an ISO timestamp of the last change.
    """

    status: AgentStatus = AgentStatus.IDLE
    last_task: str | None = None
    last_error: str | None = None
    updated_at: str = field(default_factory=_now)

    def mark_busy(self, task: str) -> None:
        self.status = AgentStatus.BUSY
        self.last_task = task[:200]
        self.updated_at = _now()

    def mark_idle(self) -> None:
        self.status = AgentStatus.IDLE
        self.last_error = None
        self.updated_at = _now()

    def mark_error(self, error: str) -> None:
        self.status = AgentStatus.ERROR
        self.last_error = error[:500]
        self.updated_at = _now()

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status.value,
            "last_task": self.last_task,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }
