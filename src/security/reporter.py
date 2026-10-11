"""Render classified scans as markdown or JSON (Phase 9)."""

from __future__ import annotations

import json
from typing import Any

from src.security.classifier import Classification
from src.security.orchestrator import ScanResult
from src.security.types import Severity

_SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)


class SecurityReporter:
    """Human-readable and machine-readable scan reports."""

    def render_markdown(
        self, result: ScanResult, classification: Classification, *, title: str = "Security scan report"
    ) -> str:
        """A compact markdown report: verdict, per-severity table, top findings."""
        lines: list[str] = [f"# {title}", ""]
        lines.append(f"**Root**: `{result.root}`")
        lines.append(
            f"**Files scanned**: {result.scanned_files} · "
            f"**Findings**: {classification.finding_count} "
            f"(unique {classification.deduped_count}) · "
            f"**Verdict**: `{classification.verdict.value.upper()}`"
        )
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("| --- | --- |")
        emitted = 0
        for severity in _SEVERITY_ORDER:
            count = classification.counts.get(severity, 0)
            if count:
                lines.append(f"| {severity.value} | {count} |")
                emitted += 1
        if not emitted:
            lines.append("| clean | 0 |")
        lines.append("")
        if not classification.top_findings:
            lines.append("No findings. Clean bill of health.")
            return "\n".join(lines)
        lines.append("## Top findings")
        lines.append("")
        for finding in classification.top_findings:
            lines.append(
                f"- `{finding.path}:{finding.line}` **[{finding.severity.value.upper()}]** "
                f"`{finding.rule}` ({finding.agent}) — {finding.message}"
            )
        return "\n".join(lines)

    def render_json(self, result: ScanResult, classification: Classification) -> str:
        """A JSON document combining the scan and its classification."""
        payload = {
            "scan": result.to_dict(),
            "classification": classification.to_dict(),
        }
        return json.dumps(payload, indent=2)

    def render_summary(self, classification: Classification) -> dict[str, Any]:
        """The small, embeddable summary dict (verdict, score, counts)."""
        return {
            "verdict": classification.verdict.value,
            "risk_score": classification.risk_score,
            "counts": {severity.value: count for severity, count in classification.counts.items()},
            "findings": classification.finding_count,
        }