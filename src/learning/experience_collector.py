"""Experience collector — a short-term, in-memory buffer of agent experiences.

One :class:`Experience` records *what an agent did and how it ended* — the raw
material the learning loop (Phase 2+) turns into rules and adjustments.

The collector is deliberately in-memory: it is a *working* memory for one
process. Long-term storage already lives in ``src.memory`` (episodic store);
the collector sits in front of it, cheap and thread-safe.

Feature map:

* **Phase 1** — the core collector: :meth:`record`, iterate, count, stats.
* **Phase 1.5** — advanced queries (:meth:`get_by_agent`, :meth:`get_by_action`,
  :meth:`get_by_outcome`, :meth:`search`), outcome statistics, JSON **and**
  JSONL export/import, retention pruning, and a fail-safe :meth:`record` that
  validates roles/actions against :data:`KNOWN_AGENT_ROLES` /
  :data:`KNOWN_ACTION_TYPES`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "DAY",
    "Experience",
    "ExperienceCollector",
    "KNOWN_ACTION_TYPES",
    "KNOWN_AGENT_ROLES",
    "Outcome",
]

logger = logging.getLogger(__name__)

DAY = 86_400.0

#: The canonical agent roles the collector accepts.
KNOWN_AGENT_ROLES = frozenset(
    {
        "architect", "coder", "reviewer", "tester", "debugger",
        "planner", "analyst", "researcher", "designer", "estimator",
        "security_auditor", "performance", "documentation", "refactor", "compliance",
        "orchestrator", "devops", "monitor", "recovery", "reporter",
    }
)

#: The canonical action types the collector accepts.
KNOWN_ACTION_TYPES = frozenset(
    {
        "read_file", "write_file", "edit_file", "list_files", "search_files",
        "run_command", "web_search", "web_fetch", "delegate_task", "write_todos",
    }
)


class Outcome(StrEnum):
    """How an action ended. A ``StrEnum`` so values serialise to JSON cleanly."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass(slots=True)
class Experience:
    """One recorded action plus its outcome.

    ``task_id`` / ``step_id`` tie the experience to the run that produced it;
    ``action`` / ``result`` are free-text detail; ``ts`` is the clock time.
    Instances are immutable by convention.
    """

    task_id: str
    step_id: str
    agent_role: str
    action_type: str
    outcome: Outcome
    action: str = ""
    result: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this experience."""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "agent_role": self.agent_role,
            "action_type": self.action_type,
            "outcome": self.outcome.value,
            "action": self.action,
            "result": self.result,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Experience:
        """Rebuild an experience from :meth:`to_dict` output.

        Raises ``KeyError`` for missing required fields, ``ValueError`` for an
        unknown outcome value.
        """
        return cls(
            task_id=str(data["task_id"]),
            step_id=str(data["step_id"]),
            agent_role=str(data["agent_role"]),
            action_type=str(data["action_type"]),
            outcome=Outcome(str(data["outcome"])),
            action=str(data.get("action", "")),
            result=str(data.get("result", "")),
            ts=float(data.get("ts", 0.0)),
        )


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
        task_id: str,
        step_id: str,
        agent_role: str,
        action_type: str,
        outcome: Outcome,
        *,
        action: str = "",
        result: str = "",
    ) -> Experience:
        """Append one experience, validating every field first.

        Raises ``ValueError`` (and logs a warning) when ``task_id`` /
        ``step_id`` are blank, or ``agent_role`` / ``action_type`` are not in
        :data:`KNOWN_AGENT_ROLES` / :data:`KNOWN_ACTION_TYPES`. Only the
        canonical lowercase forms are accepted.
        """
        self._validate(task_id=task_id, step_id=step_id, agent_role=agent_role, action_type=action_type)
        experience = Experience(
            task_id=task_id,
            step_id=step_id,
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

    @staticmethod
    def _validate(*, task_id: str, step_id: str, agent_role: str, action_type: str) -> None:
        if not task_id or not task_id.strip():
            logger.warning("record rejected: empty task_id")
            raise ValueError("task_id must be a non-empty string")
        if not step_id or not step_id.strip():
            logger.warning("record rejected: empty step_id")
            raise ValueError("step_id must be a non-empty string")
        if agent_role not in KNOWN_AGENT_ROLES:
            logger.warning("record rejected: unknown agent_role %r", agent_role)
            raise ValueError(f"unknown agent_role {agent_role!r}; expected one of {sorted(KNOWN_AGENT_ROLES)}")
        if action_type not in KNOWN_ACTION_TYPES:
            logger.warning("record rejected: unknown action_type %r", action_type)
            raise ValueError(
                f"unknown action_type {action_type!r}; expected one of {sorted(KNOWN_ACTION_TYPES)}"
            )

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

    # -- Phase 1.5 statistics -------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """Overall statistics, sorted descending by count where applicable.

        Returns ``total``, ``success_rate`` / ``failure_rate`` (floats in
        [0, 1], 0.0 when empty), ``by_agent`` / ``by_action`` / ``by_outcome``
        (counts desc) and ``time_range`` (first/last/span seconds).
        """
        with self._lock:
            total = len(self._buffer)
            if total == 0:
                success_rate = 0.0
                failure_rate = 0.0
            else:
                success_rate = sum(1 for e in self._buffer if e.outcome is Outcome.SUCCESS) / total
                failure_rate = sum(1 for e in self._buffer if e.outcome is Outcome.FAILURE) / total
            by_agent = dict(Counter(e.agent_role for e in self._buffer).most_common())
            by_action = dict(Counter(e.action_type for e in self._buffer).most_common())
            by_outcome = {
                outcome.value: count
                for outcome, count in Counter(e.outcome for e in self._buffer).most_common()
            }
            first = self._buffer[0].ts if self._buffer else 0.0
            last = self._buffer[-1].ts if self._buffer else 0.0
        return {
            "total": total,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "by_agent": by_agent,
            "by_action": by_action,
            "by_outcome": by_outcome,
            "time_range": {
                "first": first,
                "last": last,
                "span_seconds": last - first,
            },
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

    # -- Phase 1.5 export / import -------------------------------------------
    def export(self, path: str | Path) -> int:
        """Write every experience as a JSON list; return the number written."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = [experience.to_dict() for experience in self._buffer]
        with target.open("w", encoding="utf-8") as handle:
            json.dump(records, handle, ensure_ascii=False, indent=2)
        return len(records)

    def import_from(self, path: str | Path) -> int:
        """Load a JSON list produced by :meth:`export`; return the number added.

        Invalid items (missing fields, unknown outcome) are skipped with a
        warning instead of aborting the whole file.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return self._append_records(data)

    def export_to_jsonl(self, path: str | Path) -> int:
        """Write every experience as one JSON object per line; return count."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = [experience.to_dict() for experience in self._buffer]
        with target.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(records)

    def import_from_jsonl(self, path: str | Path) -> int:
        """Load a JSONL file produced by :meth:`export_to_jsonl`.

        Blank lines and malformed lines are skipped with a warning; valid lines
        are appended. Returns how many experiences were added.
        """
        imported = 0
        with Path(path).open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("import_from_jsonl: skipping line %d: %s", lineno, exc)
                    continue
                added = self._append_records([item])
                if added:
                    imported += 1
        return imported

    def _append_records(self, items: list[dict[str, Any]]) -> int:
        """Append valid experience dicts, skipping invalid ones (with warning)."""
        imported = 0
        for item in items:
            if not isinstance(item, dict):
                logger.warning("import: skipping non-dict item %r", item)
                continue
            try:
                experience = Experience.from_dict(item)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("import: skipping invalid experience: %s", exc)
                continue
            with self._lock:
                self._buffer.append(experience)
            imported += 1
        return imported

    # -- Phase 1.5 retention ---------------------------------------------------
    def prune(self, max_age_days: int = 30, max_count: int = 10_000) -> int:
        """Drop experiences older than ``max_age_days``, then trim to the most
        recent ``max_count``. Returns how many were removed."""
        cutoff = self._clock() - float(max_age_days) * DAY
        with self._lock:
            kept = [experience for experience in self._buffer if experience.ts >= cutoff]
            removed = len(self._buffer) - len(kept)
            if len(kept) > max_count:
                removed += len(kept) - max_count
                kept = kept[-max_count:]
            self._buffer[:] = kept
        return removed

    def prune_by_outcome(self, outcome: Outcome, max_count: int = 1000) -> int:
        """Keep the most recent ``max_count`` experiences of ``outcome``, delete
        the older ones of that outcome. Returns how many were removed."""
        with self._lock:
            kept: list[Experience] = []
            removed = 0
            remaining = max_count
            for experience in reversed(self._buffer):
                if experience.outcome is not outcome:
                    kept.append(experience)
                elif remaining > 0:
                    kept.append(experience)
                    remaining -= 1
                else:
                    removed += 1
            self._buffer[:] = list(reversed(kept))
        return removed