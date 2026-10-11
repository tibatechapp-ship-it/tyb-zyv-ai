"""Continual learning manager — the learning loop (Phase 5).

:class:`ContinualLearningManager` coordinates every learning component into a
single, repeatable cycle:

    collect  →  extract  →  validate  →  merge

``collect`` pulls the *new* experiences recorded since the last cycle (an
incremental cursor over the collector); ``extract`` distils and filters them
through the :class:`~src.learning.orchestrator.LearningOrchestrator`;
``validate`` applies a pluggable predicate (confidence floor + non-blank
principle by default); ``merge`` folds each survivor into the store via
:meth:`~src.learning.lesson_store.LessonStore.merge`, so a lesson that recurs
strengthens an existing entry instead of piling up near-duplicates.

:meth:`ContinualLearningManager.learn` runs one cycle on demand;
:meth:`schedule` + :meth:`run_due` make it automatic — the caller (a TUI tick,
a task completion hook, …) simply polls :meth:`run_due`, which runs a cycle only
when enough new experiences (or enough time) have accumulated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any

from src.learning.experience_collector import Experience, ExperienceCollector
from src.learning.lesson import Lesson, LessonExtractor
from src.learning.lesson_store import LessonStore
from src.learning.orchestrator import DEFAULT_MIN_CONFIDENCE, LearningOrchestrator

if TYPE_CHECKING:
    from src.learning.anti_forgetting import AntiForgetting

__all__ = [
    "ContinualLearningManager",
    "LearningCycle",
    "LessonValidator",
    "SchedulePolicy",
]

#: A predicate deciding whether an extracted lesson is good enough to keep.
LessonValidator = Callable[[Lesson], bool]


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    """When :meth:`ContinualLearningManager.run_due` should fire a cycle.

    A cycle is due once either ``every_experiences`` new experiences have piled
    up **or** ``every_seconds`` have elapsed since the previous cycle — whichever
    comes first. A non-positive bound disables that trigger.
    """

    every_experiences: int = 10
    every_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this policy."""
        return {
            "every_experiences": self.every_experiences,
            "every_seconds": self.every_seconds,
        }


@dataclass(frozen=True, slots=True)
class LearningCycle:
    """The outcome of one :meth:`ContinualLearningManager.learn` run."""

    collected: int
    extracted: int
    validated: int
    merged: int
    added: int
    dropped: int
    lessons: tuple[Lesson, ...]
    duration: float
    conflicts: int = 0

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this cycle."""
        return {
            "collected": self.collected,
            "extracted": self.extracted,
            "validated": self.validated,
            "merged": self.merged,
            "added": self.added,
            "dropped": self.dropped,
            "conflicts": self.conflicts,
            "lessons": [lesson.to_dict() for lesson in self.lessons],
            "duration": self.duration,
        }


class ContinualLearningManager:
    """Runs the collect → extract → validate → merge learning loop."""

    def __init__(
        self,
        collector: ExperienceCollector,
        extractor: LessonExtractor,
        store: LessonStore,
        *,
        orchestrator: LearningOrchestrator | None = None,
        validator: LessonValidator | None = None,
        anti_forgetting: AntiForgetting | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._collector = collector
        self._extractor = extractor
        self._store = store
        self._orchestrator = orchestrator or LearningOrchestrator(
            collector, extractor, store, min_confidence=min_confidence
        )
        self._validator = validator or self._passes_default_validation
        self._anti_forgetting = anti_forgetting
        self._min_confidence = min_confidence
        self._clock = clock
        self._cursor = 0
        self._policy: SchedulePolicy | None = None
        self._last_run_at: float | None = None
        self._history: list[LearningCycle] = []
        self._lock = RLock()

    # -- introspection ------------------------------------------------------
    @property
    def min_confidence(self) -> float:
        """The confidence floor used by the default validator."""
        return self._min_confidence

    @property
    def orchestrator(self) -> LearningOrchestrator:
        """The orchestrator driving the extract step."""
        return self._orchestrator

    @property
    def anti_forgetting(self) -> AntiForgetting | None:
        """The anti-forgetting layer guarding the store, if any."""
        return self._anti_forgetting

    @property
    def policy(self) -> SchedulePolicy | None:
        """The active schedule policy, or ``None`` when unscheduled."""
        with self._lock:
            return self._policy

    @property
    def history(self) -> tuple[LearningCycle, ...]:
        """Every cycle run so far, oldest first."""
        with self._lock:
            return tuple(self._history)

    def pending(self) -> int:
        """How many recorded experiences are awaiting the next cycle."""
        with self._lock:
            return max(0, len(self._collector.all()) - self._cursor)

    # -- the learning loop --------------------------------------------------
    def learn(self, *, experiences: list[Experience] | None = None) -> LearningCycle:
        """Run one collect → extract → validate → merge cycle.

        By default the cycle is **incremental**: it consumes only the
        experiences recorded since the previous cycle. Pass an explicit
        ``experiences`` batch to learn from that batch instead (the cursor is
        left untouched, so those experiences are not consumed).
        """
        start = self._clock()
        with self._lock:
            collected = self._collect_new() if experiences is None else list(experiences)
            if not collected:
                return self._record(LearningCycle(0, 0, 0, 0, 0, 0, (), 0.0))
            report = self._orchestrator.auto_extract(
                experiences=collected, store_results=False
            )
            validated: list[Lesson] = []
            merged = 0
            added = 0
            conflicts = 0
            for lesson in report.lessons:
                if not self._validator(lesson):
                    continue
                if self._anti_forgetting is not None:
                    result = self._anti_forgetting.consolidate(lesson)
                    stored = result.lesson
                    if result.action == "merged":
                        merged += 1
                    elif result.action == "conflict":
                        conflicts += 1
                    else:
                        added += 1
                else:
                    stored = self._store.merge(lesson)
                    if stored.id == lesson.id:
                        added += 1
                    else:
                        merged += 1
                validated.append(stored)
            cycle = LearningCycle(
                collected=len(collected),
                extracted=report.extracted,
                validated=len(validated),
                merged=merged,
                added=added,
                dropped=report.dropped + (report.kept - len(validated)),
                lessons=tuple(validated),
                duration=round(self._clock() - start, 6),
                conflicts=conflicts,
            )
            return self._record(cycle)

    # -- scheduling ---------------------------------------------------------
    def schedule(
        self, *, every_experiences: int = 10, every_seconds: float = 0.0
    ) -> SchedulePolicy:
        """Install an automatic schedule; returns the active policy.

        ``every_experiences`` and ``every_seconds`` are the two triggers
        (see :class:`SchedulePolicy`). At least one must be non-zero.
        """
        if every_experiences < 1 and every_seconds <= 0:
            raise ValueError("schedule needs every_experiences >= 1 or every_seconds > 0")
        with self._lock:
            self._policy = SchedulePolicy(
                every_experiences=every_experiences, every_seconds=every_seconds
            )
            if self._last_run_at is None:
                self._last_run_at = self._clock()
            return self._policy

    def unschedule(self) -> None:
        """Remove the active schedule."""
        with self._lock:
            self._policy = None

    def due(self) -> bool:
        """Whether a cycle is due under the active policy (and there is work)."""
        with self._lock:
            if self._policy is None or self.pending() == 0:
                return False
            if self.pending() >= self._policy.every_experiences:
                return True
            if self._policy.every_seconds > 0:
                reference = self._last_run_at if self._last_run_at is not None else self._clock()
                return (self._clock() - reference) >= self._policy.every_seconds
            return False

    def run_due(self) -> LearningCycle | None:
        """Run a cycle if :meth:`due`, else do nothing; returns the cycle or ``None``."""
        if self.due():
            return self.learn()
        return None

    # -- internals ----------------------------------------------------------
    def _collect_new(self) -> list[Experience]:
        buffered = self._collector.all()
        total = len(buffered)
        if self._cursor > total:  # the buffer was pruned under us — resync
            self._cursor = 0
        fresh = buffered[self._cursor:]
        self._cursor = total
        return fresh

    def _passes_default_validation(self, lesson: Lesson) -> bool:
        return lesson.confidence >= self._min_confidence and bool(lesson.principle.strip())

    def _record(self, cycle: LearningCycle) -> LearningCycle:
        self._last_run_at = self._clock()
        self._history.append(cycle)
        return cycle
