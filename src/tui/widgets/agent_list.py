"""Sidebar widget listing every registered agent and its status."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from src.agents.registry import AgentRegistry
from src.agents.state import AgentStatus

__all__ = ["AgentListPanel"]

_STATUS_STYLE = {
    AgentStatus.IDLE.value: "green",
    AgentStatus.BUSY.value: "yellow",
    AgentStatus.ERROR.value: "red",
    AgentStatus.DISABLED.value: "dim",
}


class AgentListPanel(Static):
    """Renders the registry as a compact, colour-coded table."""

    DEFAULT_CSS = """
    AgentListPanel {
        border: round $primary;
        padding: 0 1;
    }
    """

    def __init__(self, registry: AgentRegistry, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._registry = registry

    def on_mount(self) -> None:
        self.refresh_panel()

    def refresh_panel(self) -> None:
        self.update(self.build_table())

    def build_table(self) -> Table:
        table = Table(expand=True, show_edge=False, pad_edge=False)
        table.add_column("Agent", overflow="fold")
        table.add_column("Status", justify="right")
        agents = self._registry.all()
        if not agents:
            table.add_row(Text("(no agents registered)", style="dim"), Text(""))
            return table
        for agent in agents:
            status = agent.state.status.value
            table.add_row(
                Text(f"{agent.name} · {agent.group}", style="cyan"),
                Text(status, style=_STATUS_STYLE.get(status, "white")),
            )
        return table
