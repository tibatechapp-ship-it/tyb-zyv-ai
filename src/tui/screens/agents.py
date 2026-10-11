"""Agents view — every registered agent with its live status."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label

from src.tui.screens.base import ModalBase
from src.tui.widgets.agent_list import AgentListPanel

__all__ = ["AgentsScreen"]


class AgentsScreen(ModalBase):
    title_text = "Agents — Core · Planning · Quality · Ops"

    def compose(self) -> ComposeResult:
        with Vertical(id="agents-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield AgentListPanel(self.zyvo.state.registry)
            yield Label("press esc to close", classes="modal-hint")
