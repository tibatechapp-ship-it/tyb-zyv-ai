"""A 0–100 security score with a letter grade, derived from findings.

Severity counts are converted to a penalty; the score starts at 100 and every
finding subtracts its weight. The result is deterministic and easy to reason
about: a single critical costs 30 points, a single high 15, and so on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.security.types import Severity

__all__ = ["SCORE_PENALTY", "GRADE_THRESHOLDS", "SecurityScore", "compute_score", "grade_for"]

#: How many points each finding of a given severity removes from the score.
SCORE_PENALTY: dict[Severity, int] = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 15,
    Severity.MEDIUM: 6,
    Severity.LOW: 2,
    Severity.INFO: 1,
}

#: (minimum score, grade) from best to worst.
GRADE_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (40, "E"),
    (0, "F"),
)


def compute_score(counts: Mapping[Severity, int]) -> int:
    """The 0–100 score for a mapping of severity → count."""
    penalty = sum(SCORE_PENALTY[severity] * count for severity, count in counts.items())
    return max(0, 100 - penalty)


def grade_for(score: int) -> str:
    """The letter grade for a 0–100 ``score``."""
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


@dataclass(frozen=True, slots=True)
class SecurityScore:
    """A score plus its letter grade."""

    value: int
    grade: str

    @classmethod
    def from_counts(cls, counts: Mapping[Severity, int]) -> SecurityScore:
        """Build a :class:`SecurityScore` from severity counts."""
        value = compute_score(counts)
        return cls(value=value, grade=grade_for(value))

    def to_dict(self) -> dict[str, int | str]:
        """A JSON-serialisable view."""
        return {"score": self.value, "grade": self.grade}
