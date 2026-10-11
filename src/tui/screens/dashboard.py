"""Dashboard — the main screen: sidebar, conversation log and command bar."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, RichLog

from src.tui.commands import parse_command
from src.tui.widgets.agent_list import AgentListPanel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.tui.app import ZyvoTUI

__all__ = ["DashboardScreen"]


class DashboardScreen(Screen[None]):
    """The default view: agents on the left, conversation on the right."""

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("a", "app.show_agents", "Agents"),
        ("t", "app.show_tasks", "Tasks"),
        ("m", "app.show_monitor", "Monitor"),
        ("l", "app.show_logs", "Logs"),
        ("question_mark", "app.show_help", "Help"),
    ]

    @property
    def zyvo(self) -> ZyvoTUI:
        return cast("ZyvoTUI", self.app)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("AGENTS", classes="section-title")
                yield AgentListPanel(self.zyvo.state.registry, id="agent-list")
            with Vertical(id="conversation"):
                yield RichLog(id="log", wrap=True, markup=True, highlight=True)
                yield Input(placeholder="task · @agent task · :command", id="command")
        yield Footer()

    def on_mount(self) -> None:
        self._log("[b cyan]tyb-zyv-ai[/] — 20-agent console. Type [b]:help[/b] for commands.")
        self.query_one("#command", Input).focus()

    # -- input handling ----------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value
        box = self.query_one("#command", Input)
        box.value = ""
        command = parse_command(raw)
        if command.is_empty:
            return
        if command.kind == "command":
            self._run_ui_command(command.name)
        elif command.kind == "agent":
            self._run_agent(command.name, command.text)
        else:
            self._run_default(command.text)

    def _run_ui_command(self, name: str) -> None:
        dispatch: dict[str, Callable[[], None]] = {
            "agents": self.zyvo.action_show_agents,
            "tasks": self.zyvo.action_show_tasks,
            "monitor": self.zyvo.action_show_monitor,
            "logs": self.zyvo.action_show_logs,
            "lessons": self.zyvo.action_show_lessons,
            "help": self.zyvo.action_show_help,
            "quit": self.zyvo.exit,
        }
        handler = dispatch.get(name)
        if handler is None:
            self._log(f"[red]unknown command[/]: :{name}")
            return
        handler()

    def _run_agent(self, name: str, task: str) -> None:
        registry = self.zyvo.state.registry
        if not registry.has(name):
            self._log(f"[red]no such agent[/]: {name}")
            return
        if not task.strip():
            self._log(f"[yellow]usage:[/] @{name} <task>")
            return
        self._log(f"[cyan]{name} ▸[/] {task}")
        result = self.zyvo.state.run_agent(name, task)
        self._log(result.output if result.ok else f"[red]error:[/] {result.error}")
        self._refresh_agents()

    def _run_default(self, task: str) -> None:
        names = self.zyvo.state.registry.names()
        if not names:
            self._log("[yellow]No agents registered yet.[/] Try [b]:help[/b].")
            return
        self._run_agent(names[0], task)

    # -- helpers -----------------------------------------------------------
    def _refresh_agents(self) -> None:
        self.query_one("#agent-list", AgentListPanel).refresh_panel()

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)
