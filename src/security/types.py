"""Security scanner types (Phase 9)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How serious one finding is."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    """The verdict a classified scan comes to."""

    CLEAN = "clean"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Numeric weight per severity, used by the classifier's risk score.
SEVERITY_WEIGHTS: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Finding:
    """One pattern match: where, which rule, how bad, and a human message."""

    __slots__ = ("agent", "rule", "path", "line", "severity", "message", "snippet")

    def __init__(
        self,
        *,
        agent: str,
        rule: str,
        path: str,
        line: int,
        severity: Severity,
        message: str,
        snippet: str = "",
    ) -> None:
        self.agent = agent
        self.rule = rule
        self.path = path
        self.line = line
        self.severity = severity
        self.message = message
        self.snippet = snippet

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of the finding."""
        return {
            "agent": self.agent,
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "severity": self.severity.value,
            "message": self.message,
            "snippet": self.snippet,
        }

    def __repr__(self) -> str:
        return (
            f"Finding(agent={self.agent!r}, rule={self.rule!r}, path={self.path!r}, "
            f"line={self.line}, severity={self.severity})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (
            self.agent,
            self.rule,
            self.path,
            self.line,
            self.severity,
            self.message,
        ) == (
            other.agent,
            other.rule,
            other.path,
            other.line,
            other.severity,
            other.message,
        )

    def __hash__(self) -> int:
        return hash((self.agent, self.rule, self.path, self.line, self.message))