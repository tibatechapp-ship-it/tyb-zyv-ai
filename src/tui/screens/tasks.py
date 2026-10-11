"""Tasks view — active, completed and failed tasks."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

from src.tui.screens.base import ModalBase

__all__ = ["TasksScreen"]

_STATUS_STYLE = {"queued": "dim", "running": "yellow", "done": "green", "failed": "red"}


class TasksScreen(ModalBase):
    title_text = "Tasks"

    def compose(self) -> ComposeResult:
        with Vertical(id="tasks-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.build_table(), id="tasks-table")
            yield Label("press esc to close", classes="modal-hint")

    def build_table(self) -> Table:
        table = Table(expand=True)
        table.add_column("#", justify="right")
        table.add_column("Agent")
        table.add_column("Task", overflow="fold")
        table.add_column("Status")
        table.add_column("Time", justify="right")
        tasks = self.zyvo.state.tasks
        if not tasks:
            table.add_row("", Text("(no tasks yet)", style="dim"), "", "", "")
            return table
        for task in tasks:
            table.add_row(
                str(task.id),
                task.agent,
                task.task,
                Text(task.status, style=_STATUS_STYLE.get(task.status, "white")),
                f"{task.duration_s:.2f}s",
            )
        return table
