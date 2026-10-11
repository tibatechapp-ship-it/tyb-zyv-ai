"""Tests for src.tui — command parsing, state, widgets, screens and the app."""

from __future__ import annotations

import asyncio

from textual.widgets import Input

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.core.backend import EchoBackend
from src.tui.app import ZyvoTUI, run_app
from src.tui.commands import KNOWN_COMMANDS, parse_command
from src.tui.screens.agents import AgentsScreen
from src.tui.screens.dashboard import DashboardScreen
from src.tui.screens.help import HelpScreen
from src.tui.screens.logs import LogsScreen
from src.tui.screens.monitor import MonitorScreen
from src.tui.screens.tasks import TasksScreen
from src.tui.state import TuiState
from src.tui.widgets.agent_list import AgentListPanel
from src.tui.widgets.metrics import MetricsPanel


class _Echo(BaseAgent):
    name = "echo"
    group = "core"


class _Bad(BaseAgent):
    name = "bad"
    group = "ops"

    def build_messages(self, task: str, context: str = "") -> list:
        raise RuntimeError("nope")


def _registry() -> AgentRegistry:
    return AgentRegistry([_Echo(EchoBackend()), _Bad(EchoBackend())])


# ── parse_command ────────────────────────────────────────────────────────
def test_parse_empty() -> None:
    parsed = parse_command("   ")
    assert parsed.kind == "empty" and parsed.is_empty


def test_parse_command() -> None:
    parsed = parse_command(":agents")
    assert parsed.kind == "command" and parsed.name == "agents" and parsed.text == ""
    assert "agents" in KNOWN_COMMANDS


def test_parse_command_with_args() -> None:
    parsed = parse_command(":logs error")
    assert parsed.kind == "command" and parsed.name == "logs" and parsed.text == "error"


def test_parse_agent() -> None:
    parsed = parse_command("@coder write a loop")
    assert parsed.kind == "agent" and parsed.name == "coder" and parsed.text == "write a loop"


def test_parse_agent_without_task() -> None:
    parsed = parse_command("@coder")
    assert parsed.kind == "agent" and parsed.name == "coder" and parsed.text == ""


def test_parse_plain_task() -> None:
    parsed = parse_command("explain this error")
    assert parsed.kind == "task" and parsed.text == "explain this error"


# ── TuiState ─────────────────────────────────────────────────────────────
def test_state_run_agent_success_and_views() -> None:
    state = TuiState(_registry(), EchoBackend())
    result = state.run_agent("echo", "hi")
    assert result.ok
    assert state.llm_calls == 1
    assert [t.status for t in state.tasks] == ["done"]
    assert len(state.completed_tasks()) == 1
    assert state.active_tasks() == []
    assert state.failed_tasks() == []


def test_state_run_agent_failure() -> None:
    state = TuiState(_registry(), EchoBackend())
    state.run_agent("bad", "boom")
    assert state.tasks[-1].status == "failed"
    assert len(state.failed_tasks()) == 1


def test_state_new_task_ids_increment() -> None:
    state = TuiState(_registry(), EchoBackend())
    assert state.new_task("a", "t").id == 1
    assert state.new_task("a", "t2").id == 2


def test_state_active_tasks_includes_queued() -> None:
    state = TuiState(_registry(), EchoBackend())
    state.new_task("a", "queued thing")
    assert len(state.active_tasks()) == 1


def test_state_filtered_logs() -> None:
    state = TuiState(_registry(), EchoBackend())
    state.add_log("info", "hello world")
    state.add_log("error", "boom")
    assert len(state.filtered_logs()) == 2
    assert [r["level"] for r in state.filtered_logs(level="ERROR")] == ["ERROR"]
    assert len(state.filtered_logs(query="world")) == 1
    assert len(state.filtered_logs(level="error", query="world")) == 0


# ── widgets ──────────────────────────────────────────────────────────────
def test_agent_list_panel_empty() -> None:
    panel = AgentListPanel(AgentRegistry())
    assert panel.build_table().row_count == 1


def test_agent_list_panel_with_agents() -> None:
    panel = AgentListPanel(_registry())
    assert panel.build_table().row_count == 2


def test_metrics_panel_table() -> None:
    panel = MetricsPanel(llm_calls=7)
    table = panel.build_table()
    assert table.row_count >= 3


# ── app (headless) ───────────────────────────────────────────────────────
def test_app_runs_agent_and_commands() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            assert isinstance(app.screen, DashboardScreen)
            box = app.screen.query_one("#command", Input)

            box.value = "@echo hello"
            await pilot.press("enter")
            assert app.state.tasks[-1].status == "done"

            box.value = "plain task"
            await pilot.press("enter")
            assert app.state.tasks[-1].agent == "echo"

            box.value = "@ghost hi"
            await pilot.press("enter")
            assert len(app.state.tasks) == 2  # unknown agent → no task recorded

            box.value = "@echo"
            await pilot.press("enter")
            assert len(app.state.tasks) == 2  # empty task → usage message

            box.value = ":nope"
            await pilot.press("enter")
            assert len(app.state.tasks) == 2  # unknown command

    asyncio.run(scenario())


def test_app_modal_views_open_and_close() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_agents()
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            await pilot.press("escape")
            await pilot.pause()

            app.action_show_tasks()
            await pilot.pause()
            assert isinstance(app.screen, TasksScreen)
            app.screen.build_table()
            await pilot.press("escape")
            await pilot.pause()

            app.action_show_monitor()
            await pilot.pause()
            assert isinstance(app.screen, MonitorScreen)
            app.screen._refresh()
            await pilot.press("escape")
            await pilot.pause()

            app.action_show_logs()
            await pilot.pause()
            assert isinstance(app.screen, LogsScreen)
            app.screen.query_one("#log-filter", Input).focus()
            await pilot.press("h", "i")
            for level in ("all", "info", "warn", "error"):
                app.screen.action_set_level(level)
            app.screen.build_table()
            await pilot.press("escape")
            await pilot.pause()

            app.action_show_help()
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()

            assert isinstance(app.screen, DashboardScreen)

    asyncio.run(scenario())


def test_app_column_commands() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            box = app.screen.query_one("#command", Input)
            box.value = ":agents"
            await pilot.press("enter")
            assert isinstance(app.screen, AgentsScreen)
            await pilot.press("escape")
            box.value = ":quit"
            await pilot.press("enter")

    asyncio.run(scenario())


def test_run_app_builds_registry(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(self: ZyvoTUI) -> None:
        captured["registry"] = self.state.registry

    monkeypatch.setattr(ZyvoTUI, "run", fake_run)
    run_app(agents=[_Echo(EchoBackend())])
    registry = captured["registry"]
    assert isinstance(registry, AgentRegistry) and registry.has("echo")


def test_run_app_with_registry(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(ZyvoTUI, "run", lambda self: captured.update(ok=True))
    run_app(registry=_registry())
    assert captured.get("ok") is True
