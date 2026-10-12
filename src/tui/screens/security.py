"""Security view — run a static scan from inside the TUI and read the report.

A modal that drives a :class:`SecurityOrchestrator`: pick a target (a file, a
directory, or an ``.apk``/``.zip``) and a profile, run the scan, then browse the
findings with their CWE, CVSS and remediation. Findings can be filtered by
profile, searched by text and exported to a markdown report.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Static

from src.security import SecurityReport, metadata_for
from src.security.profiles import PROFILE_ORDER
from src.security.types import Finding, Severity
from src.tui.screens.base import ModalBase

__all__ = ["SecurityScreen", "SEVERITY_STYLE"]


#: How each severity is coloured in the table.
SEVERITY_STYLE: dict[Severity, str] = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}

#: The severity order used when printing the counts line.
_COUNT_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)


class SecurityScreen(ModalBase):
    """A modal that runs the security scanner and renders the report.

    Keys: ``r`` run scan · ``e`` export · ``f`` cycle profile · ``esc`` close.
    The buttons below the inputs do the same. Findings are shown worst-first;
    moving the cursor shows each finding's CWE, CVSS and remediation.
    """

    title_text = "Security — static scan"
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("r", "run_scan", "Run scan"),
        ("e", "export_report", "Export"),
        ("f", "cycle_profile", "Filter"),
    ]

    def __init__(self, *, target: str = ".", profile: str = "full") -> None:
        super().__init__()
        self._profile = profile
        self._query = ""
        self._report: SecurityReport | None = None
        self._visible: list[Finding] = []
        self._target_default = target

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="security-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Input(value=self._target_default, placeholder="target path or .apk …", id="security-target")
            yield Input(placeholder="search findings…", id="security-search")
            with Horizontal(id="security-buttons"):
                yield Button("Run Scan", id="btn-run", variant="primary")
                yield Button("Export Report", id="btn-export")
                yield Button("Filter", id="btn-filter")
                yield Button("Search", id="btn-search")
            yield Static(self.summary_text(), id="security-summary", markup=True)
            yield DataTable(id="security-findings")
            yield Static("", id="security-detail", markup=True)
            yield Label("press esc to close", classes="modal-hint")

    def on_mount(self) -> None:
        table = self.query_one("#security-findings", DataTable)
        table.cursor_type = "row"
        table.add_columns("Severity", "Rule", "Location", "Message")
        self._refresh()

    # -- interaction -------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "security-search":
            self._query = event.value
            self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "btn-run": self.action_run_scan,
            "btn-export": self.action_export_report,
            "btn-filter": self.action_cycle_profile,
            "btn-search": self.action_focus_search,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key is not None else None
        try:
            index = int(key)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if 0 <= index < len(self._visible):
            self.query_one("#security-detail", Static).update(
                self.detail_text(self._visible[index])
            )

    # -- actions -----------------------------------------------------------
    def action_run_scan(self) -> None:
        self.run_scan()

    def action_export_report(self) -> None:
        self.export_report()

    def action_cycle_profile(self) -> None:
        index = PROFILE_ORDER.index(self._profile)
        self._profile = PROFILE_ORDER[(index + 1) % len(PROFILE_ORDER)]
        self._refresh()

    def action_focus_search(self) -> None:
        self.query_one("#security-search", Input).focus()

    # -- core operations ---------------------------------------------------
    def run_scan(self, target: str | None = None) -> SecurityReport | None:
        """Scan ``target`` (or the input box's value) and show the report."""
        if target is None:
            target = self.query_one("#security-target", Input).value
        target = target.strip() or "."
        try:
            report = self.zyvo.state.security_orchestrator.scan(target, profile=self._profile)
        except (FileNotFoundError, OSError) as exc:
            self._report = None
            self.query_one("#security-summary", Static).update(f"[red]scan failed:[/] {exc}")
            return None
        self._report = report
        self._refresh()
        table = self.query_one("#security-findings", DataTable)
        table.focus()
        return report

    def export_report(self, path: str | Path | None = None) -> Path | None:
        """Write a markdown report; returns the path (or ``None`` if nothing scanned)."""
        if self._report is None:
            self.query_one("#security-summary", Static).update(
                "[yellow]Nothing to export — run a scan first.[/]"
            )
            return None
        markdown = self.zyvo.state.security_orchestrator.render_markdown(
            self._report, title=f"Security scan — {self._report.target}"
        )
        if path is not None:
            out = Path(path)
        else:
            # Keep generated reports out of the working tree (and out of git):
            # a dedicated per-user data directory, created on demand.
            reports_dir = Path.home() / ".local" / "share" / "tyb-zyv-ai" / "reports"
            try:
                reports_dir.mkdir(parents=True, exist_ok=True)
            except OSError:  # pragma: no cover - read-only home, fall back to cwd
                reports_dir = Path.cwd()
            out = reports_dir / f"security-report-{time.strftime('%Y%m%d-%H%M%S')}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        self.zyvo.notify(f"Report written to {out}", title="Security")
        return out

    # -- rendering ---------------------------------------------------------
    def _refresh(self) -> None:
        self.query_one("#security-summary", Static).update(self.summary_text())
        self._refresh_table()
        self.query_one("#security-detail", Static).update("")

    def _refresh_table(self) -> None:
        table = self.query_one("#security-findings", DataTable)
        table.clear()
        self._visible = self.visible_findings()
        if not self._visible:
            table.add_row("—", "—", "—", self._empty_message())
            return
        for index, finding in enumerate(self._visible):
            table.add_row(
                Text(finding.severity.value.upper(), style=SEVERITY_STYLE[finding.severity]),
                finding.rule,
                f"{finding.path}:{finding.line}",
                finding.message,
                key=str(index),
            )

    def _empty_message(self) -> str:
        if self._report is None:
            return "No scan yet — set a target and press Run Scan."
        return "No findings match the current search."

    def visible_findings(self) -> list[Finding]:
        """The findings passing the current text search."""
        if self._report is None:
            return []
        needle = self._query.strip().lower()
        if not needle:
            return list(self._report.findings)
        return [
            finding
            for finding in self._report.findings
            if needle in finding.rule.lower()
            or needle in finding.message.lower()
            or needle in finding.path.lower()
            or needle in finding.agent.lower()
        ]

    def severity_counts(self) -> dict[str, int]:
        """Counts per severity for the current report (lower-case keys)."""
        if self._report is None:
            return {}
        counts = self._report.classification.counts
        return {severity.value: counts.get(severity, 0) for severity in _COUNT_ORDER}

    def summary_text(self) -> str:
        """The score / verdict / counts line."""
        profile = f"[b]profile:[/b] {self._profile}"
        if self._report is None:
            return f"No scan yet.  {profile}  ([b]r[/b] to run, [b]f[/b] to change profile)"
        score = self._report.score
        verdict = self._report.classification.verdict.value.upper()
        parts = [f"{name}={count}" for name, count in self.severity_counts().items() if count]
        counts = ", ".join(parts) if parts else "clean"
        return (
            f"[b]Score:[/b] {score.value}/100 ({score.grade})  ·  "
            f"[b]Verdict:[/b] {verdict}  ·  {counts}  ·  {profile}"
        )

    def detail_text(self, finding: Finding) -> str:
        """The CWE / CVSS / remediation block for one finding."""
        meta = metadata_for(finding.rule)
        snippet = finding.snippet or "-"
        return (
            f"[b]{finding.rule}[/b]  ({finding.agent})\n"
            f"{finding.path}:{finding.line}   severity=[{SEVERITY_STYLE[finding.severity]}]{finding.severity.value}[/]\n"
            f"[b]CWE:[/b] {meta.cwe}   [b]CVSS:[/b] {meta.cvss}\n"
            f"[b]Remediation:[/b] {meta.remediation}\n"
            f"[b]Snippet:[/b] {snippet}"
        )
