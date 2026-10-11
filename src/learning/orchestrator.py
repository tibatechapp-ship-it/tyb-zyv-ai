"""Learning orchestrator — wire the learning pipeline together (Phase 4).

:class:`LearningOrchestrator` connects the three learning components into one
callable pipeline:

    ExperienceCollector  →  LessonExtractor  →  LessonStore

:meth:`LearningOrchestrator.auto_extract` reads experiences, distils lessons,
**drops the weak ones** (``confidence < min_confidence``, default ``0.5``) and
stores the survivors, returning an :class:`ExtractionReport` so callers can see
exactly what happened. It touches nothing else, so it composes freely with the
:class:`~src.learning.manager.ContinualLearningManager` (Phase 5) and the
anti-forgetting layer (Phase 6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.learning.experience_collector import Experience, ExperienceCollector
from src.learning.lesson import Lesson, LessonExtractor
from src.learning.lesson_store import LessonStore

__all__ = ["DEFAULT_MIN_CONFIDENCE", "ExtractionReport", "LearningOrchestrator"]

#: Lessons below this confidence are considered too weak to keep.
DEFAULT_MIN_CONFIDENCE = 0.5

#: A hard ceiling on how many experiences one task lookup scans.
_SCAN_LIMIT = 1_000_000


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    """The outcome of one :meth:`LearningOrchestrator.auto_extract` run."""

    extracted: int
    kept: int
    dropped: int
    lessons: tuple[Lesson, ...]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this report."""
        return {
            "extracted": self.extracted,
            "kept": self.kept,
            "dropped": self.dropped,
            "lessons": [lesson.to_dict() for lesson in self.lessons],
        }


class LearningOrchestrator:
    """Runs the experiences → lessons → store pipeline."""

    def __init__(
        self,
        collector: ExperienceCollector,
        extractor: LessonExtractor,
        store: LessonStore,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        self._collector = collector
        self._extractor = extractor
        self._store = store
        self._min_confidence = min_confidence

    @property
    def min_confidence(self) -> float:
        """The confidence floor below which a lesson is dropped."""
        return self._min_confidence

    def auto_extract(
        self,
        *,
        task_id: str | None = None,
        experiences: Sequence[Experience] | None = None,
        min_confidence: float | None = None,
        store_results: bool = True,
    ) -> ExtractionReport:
        """Extract, filter and store lessons in a single call.

        The experiences to learn from are resolved in this order: an explicit
        ``experiences`` sequence, else the collected experiences of ``task_id``
        (oldest first), else **every** collected experience. Lessons scoring
        below ``min_confidence`` (defaults to the orchestrator's own floor) are
        dropped before storing.

        Pass ``store_results=False`` to get the filtered lessons **without**
        writing them to the store — the :class:`ContinualLearningManager` uses
        this to run its own validate/merge step before persisting.
        """
        threshold = self._min_confidence if min_confidence is None else min_confidence
        source = self._resolve_source(task_id, experiences)
        lessons = self._extractor.extract_batch(source)
        kept = [lesson for lesson in lessons if lesson.confidence >= threshold]
        if kept and store_results:
            self._store.add_many(kept)
        return ExtractionReport(
            extracted=len(lessons),
            kept=len(kept),
            dropped=len(lessons) - len(kept),
            lessons=tuple(kept),
        )

    def _resolve_source(
        self,
        task_id: str | None,
        experiences: Sequence[Experience] | None,
    ) -> list[Experience]:
        if experiences is not None:
            return list(experiences)
        if task_id is not None:
            newest_first = self._collector.search(
                lambda experience: experience.task_id == task_id, limit=_SCAN_LIMIT
            )
            return list(reversed(newest_first))
        return self._collector.all()
