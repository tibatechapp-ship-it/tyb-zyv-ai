"""The agent learning package.

Phase 1 + 1.5 — :class:`ExperienceCollector`: a thread-safe in-memory buffer
that records what each agent did and how it ended (:class:`Experience` /
:class:`Outcome`) — newest-first queries, statistics, JSON/JSONL export/import,
retention pruning and fail-safe recording against the canonical
:data:`KNOWN_AGENT_ROLES` / :data:`KNOWN_ACTION_TYPES`. Phase 2 (turning
experiences into rules) builds on this.
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

__all__ = [
    "DAY",
    "KNOWN_ACTION_TYPES",
    "KNOWN_AGENT_ROLES",
    "Experience",
    "ExperienceCollector",
    "Outcome",
]