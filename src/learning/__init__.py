"""The agent learning package.

Phase 1 — :class:`ExperienceCollector`: a thread-safe in-memory buffer that
records what each agent did and how it ended (:class:`Experience` /
:class:`Outcome`) — newest-first queries by agent, action, outcome or any
custom predicate. Phase 2 (turning experiences into rules) builds on this.
"""

from __future__ import annotations

from src.learning.experience_collector import (
    Experience,
    ExperienceCollector,
    Outcome,
)

__all__ = ["Experience", "ExperienceCollector", "Outcome"]