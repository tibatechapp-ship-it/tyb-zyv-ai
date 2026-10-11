"""Monitor view — live host metrics, refreshed once per second."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label

from src.tui.screens.base import ModalBase
from src.tui.widgets.metrics import MetricsPanel

__all__ = ["MonitorScreen"]


class MonitorScreen(ModalBase):
    title_text = "Monitor"

    def compose(self) -> ComposeResult:
        with Vertical(id="monitor-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield MetricsPanel(self.zyvo.state.llm_calls, id="metrics")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        self.query_one(MetricsPanel).refresh_panel(self.zyvo.state.llm_calls)
