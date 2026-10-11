"""The agent learning package.

Phase 1 + 1.5 — :class:`ExperienceCollector`: a thread-safe in-memory buffer
that records what each agent did and how it ended (:class:`Experience` /
:class:`Outcome`) — newest-first queries, statistics, JSON/JSONL export/import,
retention pruning and fail-safe recording against the canonical
:data:`KNOWN_AGENT_ROLES` / :data:`KNOWN_ACTION_TYPES`.

Phase 2 — :class:`LessonExtractor`: distils those experiences into reusable
:class:`Lesson` principles (via an :class:`~src.core.backend.LLMBackend`),
merging similar ones and scoring confidence.

Phase 3 — :class:`LessonStore` + :class:`LessonInjector`: rank the stored
lessons against a task and fold the relevant ones into an agent's prompt
(:func:`render_lessons`), closing the learn → apply loop.

Phase 4 — :class:`LearningOrchestrator` runs the whole pipeline
(collector → extractor → store) in one call and drops weak lessons, while
:class:`LessonDatabase` persists lessons to SQLite.
"""

from __future__ import annotations

from src.learning.experience_collector import (
    DAY,
    KNOWN_ACTION_TYPES,
    KNOWN_AGENT_ROLES,
    Experience,
    ExperienceCollector,
    Outcome,
)
from src.learning.lesson import Lesson, LessonExtractor
from src.learning.lesson_store import (
    LessonDatabase,
    LessonInjector,
    LessonStore,
    default_lesson_db_path,
    render_lessons,
)
from src.learning.orchestrator import (
    DEFAULT_MIN_CONFIDENCE,
    ExtractionReport,
    LearningOrchestrator,
)

__all__ = [
    "DAY",
    "DEFAULT_MIN_CONFIDENCE",
    "KNOWN_ACTION_TYPES",
    "KNOWN_AGENT_ROLES",
    "Experience",
    "ExperienceCollector",
    "ExtractionReport",
    "Lesson",
    "LessonDatabase",
    "LessonExtractor",
    "LessonInjector",
    "LessonStore",
    "LearningOrchestrator",
    "Outcome",
    "default_lesson_db_path",
    "render_lessons",
]