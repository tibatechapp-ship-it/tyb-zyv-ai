"""Experience collector — a short-term, in-memory buffer of agent experiences.

One :class:`Experience` records *what an agent did and how it ended* — the raw
material the learning loop (Phase 2+) turns into rules and adjustments.

* **Phase 1** — the core collector: :meth:`record`, iterate, count, stats.
* **Phase 1.5** — advanced queries: :meth:`get_by_agent`, :meth:`get_by_action`,
  :meth:`get_by_outcome` and a custom-predicate :meth:`search` (all newest first).

The buffer is deliberately in-memory: it is a *working* memory for one process.
Long-term storage already lives in ``src.memory`` (episodic store); the collector
sits in front of it, cheap and thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["Experience", "Outcome", "ExperienceCollector"]


class Outcome(StrEnum):
    """How an action ended. A ``StrEnum`` so values serialise to JSON cleanly."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass(slots=True)
class Experience:
    """One recorded action plus its outcome.

    ``action`` / ``result`` are free-text detail; ``ts`` is the clock time the
    collector recorded it. Instances are immutable by convention.
    """

    agent_role: str
    action_type: str
    outcome: Outcome
    action: str = ""
    result: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this experience."""
        return {
            "agent_role": self.agent_role,
            "action_type": self.action_type,
            "outcome": self.outcome.value,
            "action": self.action,
            "result": self.result,
            "ts": self.ts,
        }


class ExperienceCollector:
    """Thread-safe in-memory buffer of :class:`Experience` records.

    Write with :meth:`record`; read with :meth:`all`, the ``get_by_*`` queries
    and :meth:`search`. Every read returns a snapshot list, so iterating is safe
    while other threads keep recording.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._buffer: list[Experience] = []
        self._lock = threading.RLock()

    # -- Phase 1 core -------------------------------------------------------
    def record(
        self,
        agent_role: str,
        action_type: str,
        outcome: Outcome,
        *,
        action: str = "",
        result: str = "",
    ) -> Experience:
        """Append one experience (timestamped with the injected clock)."""
        experience = Experience(
            agent_role=agent_role,
            action_type=action_type,
            outcome=outcome,
            action=action,
            result=result,
            ts=self._clock(),
        )
        with self._lock:
            self._buffer.append(experience)
        return experience

    def all(self) -> list[Experience]:
        """Every experience in chronological order (oldest first)."""
        with self._lock:
            return list(self._buffer)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        """Drop every recorded experience."""
        with self._lock:
            self._buffer.clear()

    def stats(self) -> dict[str, int]:
        """Overall counts, broken down by outcome."""
        with self._lock:
            total = len(self._buffer)
            successes = sum(1 for e in self._buffer if e.outcome is Outcome.SUCCESS)
            failures = sum(1 for e in self._buffer if e.outcome is Outcome.FAILURE)
            partials = total - successes - failures
        return {
            "total": total,
            "successes": successes,
            "failures": failures,
            "partials": partials,
        }

    # -- Phase 1.5 advanced queries (all newest first) ----------------------
    def get_by_agent(self, agent_role: str, limit: int = 50) -> list[Experience]:
        """Return recent experiences for a specific agent role."""
        with self._lock:
            return [e for e in reversed(self._buffer) if e.agent_role == agent_role][:limit]

    def get_by_action(self, action_type: str, limit: int = 50) -> list[Experience]:
        """Return recent experiences of a specific action type."""
        with self._lock:
            return [e for e in reversed(self._buffer) if e.action_type == action_type][:limit]

    def get_by_outcome(self, outcome: Outcome, limit: int = 50) -> list[Experience]:
        """Return recent experiences with a specific outcome."""
        with self._lock:
            return [e for e in reversed(self._buffer) if e.outcome == outcome][:limit]

    def search(
        self, predicate: Callable[[Experience], bool], limit: int = 50
    ) -> list[Experience]:
        """Return recent experiences matching a custom predicate."""
        with self._lock:
            return [e for e in reversed(self._buffer) if predicate(e)][:limit]