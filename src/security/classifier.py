"""Turns raw findings into a verdict (Phase 9)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from src.security.types import Finding, RiskLevel, SEVERITY_WEIGHTS, Severity


class Classification:
    """A classified scan: deduped, scored, judged."""

    __slots__ = (
        "verdict",
        "risk_score",
        "counts",
        "finding_count",
        "deduped_count",
        "top_findings",
    )

    def __init__(
        self,
        *,
        verdict: RiskLevel,
        risk_score: int,
        counts: Counter[Severity],
        finding_count: int,
        deduped_count: int,
        top_findings: tuple[Finding, ...],
    ) -> None:
        self.verdict = verdict
        self.risk_score = risk_score
        self.counts = counts
        self.finding_count = finding_count
        self.deduped_count = deduped_count
        self.top_findings = top_findings

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of the classification."""
        return {
            "verdict": self.verdict.value,
            "risk_score": self.risk_score,
            "counts": {severity.value: count for severity, count in self.counts.items()},
            "finding_count": self.finding_count,
            "deduped_count": self.deduped_count,
            "top_findings": [finding.to_dict() for finding in self.top_findings],
        }


def _verdict_for(counts: Counter[Severity]) -> RiskLevel:
    if counts[Severity.CRITICAL]:
        return RiskLevel.CRITICAL
    if counts[Severity.HIGH] or counts[Severity.MEDIUM] >= 3:
        return RiskLevel.HIGH
    if counts[Severity.MEDIUM] or counts[Severity.LOW] >= 5:
        return RiskLevel.MEDIUM
    if counts[Severity.LOW] or counts[Severity.INFO]:
        return RiskLevel.LOW
    return RiskLevel.CLEAN


class FindingClassifier:
    """Deduplicate, score and judge a scan's findings."""

    def __init__(self, *, top_limit: int = 10) -> None:
        if top_limit < 1:
            raise ValueError("top_limit must be >= 1")
        self._top_limit = top_limit

    def classify(self, findings: Sequence[Finding]) -> Classification:
        """Return a :class:`Classification` for ``findings``."""
        deduped: list[Finding] = []
        seen: set[tuple[str, str, str, int, str]] = set()
        for finding in findings:
            key = (finding.agent, finding.rule, finding.path, finding.line, finding.message)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
        counts: Counter[Severity] = Counter(finding.severity for finding in deduped)
        risk_score = sum(SEVERITY_WEIGHTS[finding.severity] for finding in deduped)
        ordered = sorted(
            deduped,
            key=lambda finding: (-SEVERITY_WEIGHTS[finding.severity], finding.path, finding.line),
        )
        return Classification(
            verdict=_verdict_for(counts),
            risk_score=risk_score,
            counts=counts,
            finding_count=len(findings),
            deduped_count=len(deduped),
            top_findings=tuple(ordered[: self._top_limit]),
        )