"""Per-agent metrics (counts + timing)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

__all__ = ["AgentMetrics"]


@dataclass
class AgentMetrics:
    """Aggregate counters for one agent. Cheap to update, easy to snapshot.

    A single agent may be driven from several threads at once (fan-out,
    ``asyncio.to_thread``). Every mutation and read therefore happens under an
    instance :class:`~threading.RLock`, so concurrent ``record`` calls lose no
    updates and ``to_dict`` never observes a torn snapshot. An ``RLock`` (not a
    plain ``Lock``) is required because the read helpers nest (``to_dict`` calls
    ``tasks_ok``/``avg_seconds`` while already holding the lock).
    """

    tasks_run: int = 0
    tasks_failed: int = 0
    total_seconds: float = 0.0
    last_seconds: float = 0.0
    durations: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def record(self, seconds: float, *, ok: bool) -> None:
        with self._lock:
            self.tasks_run += 1
            if not ok:
                self.tasks_failed += 1
            self.last_seconds = seconds
            self.total_seconds += seconds
            self.durations.append(seconds)

    @property
    def tasks_ok(self) -> int:
        with self._lock:
            return self.tasks_run - self.tasks_failed

    @property
    def avg_seconds(self) -> float:
        with self._lock:
            return self.total_seconds / self.tasks_run if self.tasks_run else 0.0

    def to_dict(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "tasks_run": self.tasks_run,
                "tasks_ok": self.tasks_ok,
                "tasks_failed": self.tasks_failed,
                "avg_seconds": round(self.avg_seconds, 3),
                "last_seconds": round(self.last_seconds, 3),
            }
