"""Per-agent metrics (counts + timing)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["AgentMetrics"]


@dataclass
class AgentMetrics:
    """Aggregate counters for one agent. Cheap to update, easy to snapshot."""

    tasks_run: int = 0
    tasks_failed: int = 0
    total_seconds: float = 0.0
    last_seconds: float = 0.0
    durations: list[float] = field(default_factory=list)

    def record(self, seconds: float, *, ok: bool) -> None:
        self.tasks_run += 1
        if not ok:
            self.tasks_failed += 1
        self.last_seconds = seconds
        self.total_seconds += seconds
        self.durations.append(seconds)

    @property
    def tasks_ok(self) -> int:
        return self.tasks_run - self.tasks_failed

    @property
    def avg_seconds(self) -> float:
        return self.total_seconds / self.tasks_run if self.tasks_run else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tasks_run": self.tasks_run,
            "tasks_ok": self.tasks_ok,
            "tasks_failed": self.tasks_failed,
            "avg_seconds": round(self.avg_seconds, 3),
            "last_seconds": round(self.last_seconds, 3),
        }
