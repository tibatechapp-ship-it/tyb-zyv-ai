"""Lessons view — browse, search and filter the learned principles."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label, Static

from src.learning import Lesson
from src.tui.screens.base import ModalBase

__all__ = ["LessonsScreen"]


class LessonsScreen(ModalBase):
    """A modal table of the lessons in the app's :class:`LessonStore`.

    Filter by confidence with keys ``1``–``4`` and narrow by text with the input
    box (matches principle, context or tags). Columns: confidence, principle,
    agent, evidence count and tags.
    """

    title_text = "Lessons — learned principles"
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("1", "set_confidence(0.0)", "All"),
        ("2", "set_confidence(0.3)", "≥0.3"),
        ("3", "set_confidence(0.6)", "≥0.6"),
        ("4", "set_confidence(0.8)", "≥0.8"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._min_confidence = 0.0
        self._query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="lessons-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Input(
                placeholder="filter text… (1=all 2=≥0.3 3=≥0.6 4=≥0.8)",
                id="lesson-filter",
            )
            yield Static(self.build_table(), id="lessons-body")

    # -- interaction -------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value
        self._refresh()

    def action_set_confidence(self, value: float) -> None:
        self._min_confidence = float(value)
        self._refresh()

    # -- rendering ---------------------------------------------------------
    def _refresh(self) -> None:
        self.query_one("#lessons-body", Static).update(self.build_table())

    def visible_lessons(self) -> list[Lesson]:
        """The lessons passing the current confidence + text filters."""
        needle = self._query.strip().lower()
        result: list[Lesson] = []
        for lesson in self.zyvo.state.lesson_store.all():
            if lesson.confidence < self._min_confidence:
                continue
            if needle and not (
                needle in lesson.principle.lower()
                or needle in lesson.context.lower()
                or any(needle in tag.lower() for tag in lesson.tags)
            ):
                continue
            result.append(lesson)
        return result

    def build_table(self) -> Table:
        """Build the lessons table for the current filters."""
        table = Table(expand=True)
        table.add_column("Conf", justify="right")
        table.add_column("Principle", overflow="fold")
        table.add_column("Agent")
        table.add_column("Evidence", justify="right")
        table.add_column("Tags", overflow="fold")

        lessons = self.visible_lessons()
        if not lessons:
            table.add_row("", Text("(no lessons yet)", style="dim"), "", "", "")
            return table

        for lesson in lessons:
            if lesson.confidence >= 0.7:
                confidence_style = "green"
            elif lesson.confidence >= 0.5:
                confidence_style = "yellow"
            else:
                confidence_style = "red"
            table.add_row(
                Text(f"{lesson.confidence:.2f}", style=confidence_style),
                lesson.principle,
                lesson.agent_role,
                str(len(lesson.evidence)),
                ", ".join(sorted(lesson.tags)) or "-",
            )
        return table
