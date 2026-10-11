"""Logs view — structured log table with level filtering and text search."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label, Static

from src.tui.screens.base import ModalBase

__all__ = ["LogsScreen"]

_LEVEL_STYLE = {"INFO": "cyan", "WARN": "yellow", "ERROR": "red", "DEBUG": "dim"}
_LEVEL_KEYS = {"1": None, "2": "INFO", "3": "WARN", "4": "ERROR"}


class LogsScreen(ModalBase):
    title_text = "Logs"
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("1", "set_level('all')", "All"),
        ("2", "set_level('info')", "Info"),
        ("3", "set_level('warn')", "Warn"),
        ("4", "set_level('error')", "Error"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._level: str | None = None
        self._query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="logs-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Input(placeholder="filter text… (1=all 2=info 3=warn 4=error)", id="log-filter")
            yield Static(self.build_table(), id="logs-body")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value
        self._refresh()

    def action_set_level(self, value: str) -> None:
        self._level = None if value == "all" else _LEVEL_KEYS.get(value, None) or value.upper()
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#logs-body", Static).update(self.build_table())

    def build_table(self) -> Table:
        table = Table(expand=True)
        table.add_column("Time")
        table.add_column("Level")
        table.add_column("Message", overflow="fold")
        rows = self.zyvo.state.filtered_logs(level=self._level, query=self._query)
        if not rows:
            table.add_row("", "", Text("(no log entries)", style="dim"))
            return table
        for row in rows:
            table.add_row(
                row["time"],
                Text(row["level"], style=_LEVEL_STYLE.get(row["level"], "white")),
                row["message"],
            )
        return table
