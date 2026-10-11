"""The interactive terminal application (Textual)."""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import App

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.core.backend import LLMBackend
from src.tui.screens.agents import AgentsScreen
from src.tui.screens.dashboard import DashboardScreen
from src.tui.screens.help import HelpScreen
from src.tui.screens.lessons import LessonsScreen
from src.tui.screens.logs import LogsScreen
from src.tui.screens.monitor import MonitorScreen
from src.tui.screens.tasks import TasksScreen
from src.tui.state import TuiState

__all__ = ["ZyvoTUI", "run_app"]


class ZyvoTUI(App[None]):
    """The tyb-zyv-ai console: a dashboard plus four modal views."""

    CSS_PATH = "app.tcss"
    TITLE = "tyb-zyv-ai"
    SUB_TITLE = "20-agent console"
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "show_lessons", "Lessons"),
    ]

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        backend: LLMBackend | None = None,
    ) -> None:
        super().__init__()
        # When no registry is given, TuiState auto-registers the 20 built-ins
        # (it needs a backend to instantiate them).
        self.state = TuiState(registry, backend)

    # -- screen actions ----------------------------------------------------
    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())

    def action_show_agents(self) -> None:
        self.push_screen(AgentsScreen())

    def action_show_tasks(self) -> None:
        self.push_screen(TasksScreen())

    def action_show_monitor(self) -> None:
        self.push_screen(MonitorScreen())

    def action_show_logs(self) -> None:
        self.push_screen(LogsScreen())

    def action_show_lessons(self) -> None:
        self.push_screen(LessonsScreen())

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_quit(self) -> None:
        self.exit()


def run_app(
    registry: AgentRegistry | None = None,
    backend: LLMBackend | None = None,
    agents: Iterable[BaseAgent] | None = None,
) -> None:
    """Convenience entry point: build a registry from ``agents`` if given, then run."""
    reg = registry
    if reg is None:
        reg = AgentRegistry(agents or ())
    ZyvoTUI(registry=reg, backend=backend).run()
