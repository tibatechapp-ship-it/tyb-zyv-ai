"""Tests for src.tui — command parsing, state, widgets, screens and the app."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Input

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.core.backend import EchoBackend
from src.learning import Lesson, Outcome
from src.tui.app import ZyvoTUI, run_app
from src.tui.commands import KNOWN_COMMANDS, parse_command
from src.tui.screens.agents import AgentsScreen
from src.tui.screens.dashboard import DashboardScreen
from src.tui.screens.help import HelpScreen
from src.tui.screens.lessons import LessonsScreen
from src.tui.screens.logs import LogsScreen
from src.tui.screens.monitor import MonitorScreen
from src.tui.screens.security import SecurityScreen
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


def _make_lesson(lid: str = "l1", *, principle: str = "always run the tests", confidence: float = 0.8, tags: tuple[str, ...] = ("deploy",)) -> Lesson:
    return Lesson(
        id=lid,
        principle=principle,
        context="deploy",
        confidence=confidence,
        evidence=("t1:s1",),
        source_outcome=Outcome.FAILURE,
        created_at=1.0,
        agent_role="coder",
        tags=frozenset(tags),
    )


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
    assert "lessons" in KNOWN_COMMANDS


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


def test_state_lesson_store_default_and_injected() -> None:
    from src.learning import LessonStore

    state = TuiState(_registry(), EchoBackend())
    assert isinstance(state.lesson_store, LessonStore)
    assert len(state.lesson_store) == 0

    store = LessonStore()
    store.add(_make_lesson("x"))
    injected = TuiState(_registry(), EchoBackend(), lesson_store=store)
    assert injected.lesson_store is store


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

            app.action_show_lessons()
            await pilot.pause()
            assert isinstance(app.screen, LessonsScreen)
            app.screen.build_table()
            app.screen.action_set_confidence(0.0)
            await pilot.press("escape")
            await pilot.pause()

            app.action_show_help()
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()

            assert isinstance(app.screen, DashboardScreen)

    asyncio.run(scenario())


def test_lessons_screen_renders_and_filters() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())
    app.state.lesson_store.add(
        _make_lesson("a", principle="always run the tests before deploy", confidence=0.9, tags=("deploy",))
    )
    app.state.lesson_store.add(
        _make_lesson("b", principle="never force push to main", confidence=0.4, tags=("git",))
    )

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_lessons()
            await pilot.pause()
            assert isinstance(app.screen, LessonsScreen)

            # Both lessons show with the default (all) filter.
            assert app.screen.build_table().row_count == 2

            # Confidence filter hides the weak lesson.
            app.screen.action_set_confidence(0.6)
            assert app.screen.build_table().row_count == 1
            app.screen.action_set_confidence(0.0)

            # Text filter matches principle / tag.
            box = app.screen.query_one("#lesson-filter", Input)
            box.value = "force push"
            await pilot.pause()
            assert [lesson.id for lesson in app.screen.visible_lessons()] == ["b"]
            box.value = "tests"
            await pilot.pause()
            assert [lesson.id for lesson in app.screen.visible_lessons()] == ["a"]
            box.value = "nothing-matches"
            await pilot.pause()
            assert app.screen.build_table().row_count == 1  # "(no lessons yet)" row
            box.value = ""

            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(scenario())


def test_lessons_screen_confidence_colour_bands() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())
    app.state.lesson_store.add(_make_lesson("hi", confidence=0.9))
    app.state.lesson_store.add(_make_lesson("mid", confidence=0.6))
    app.state.lesson_store.add(_make_lesson("lo", confidence=0.3))

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_lessons()
            await pilot.pause()
            # green / yellow / red bands all render.
            assert app.screen.build_table().row_count == 3
            await pilot.press("escape")

    asyncio.run(scenario())


def test_lessons_screen_empty_store() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_lessons()
            await pilot.pause()
            assert isinstance(app.screen, LessonsScreen)
            assert app.screen.build_table().row_count == 1  # the "(no lessons yet)" row
            await pilot.press("escape")

    asyncio.run(scenario())


def test_ctrl_l_opens_lessons() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+l")
            await pilot.pause()
            assert isinstance(app.screen, LessonsScreen)
            await pilot.press("escape")

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
            box.value = ":lessons"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, LessonsScreen)
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


# ── Security screen ──────────────────────────────────────────────────────
_BAD_SOURCE = "eval(input())\npassword = 'hunter2xyz'\nimport pickle\npickle.loads(x)\n"


def _bad_tree(root: Path) -> None:
    (root / "bad.py").write_text(_BAD_SOURCE, encoding="utf-8")
    (root / "clean.py").write_text("x = 1\n", encoding="utf-8")


def test_state_has_security_orchestrator() -> None:
    from src.security import SecurityOrchestrator

    state = TuiState(_registry(), EchoBackend())
    assert isinstance(state.security_orchestrator, SecurityOrchestrator)
    injected = SecurityOrchestrator(top_limit=3)
    assert TuiState(_registry(), EchoBackend(), security_orchestrator=injected).security_orchestrator is injected


def test_security_screen_empty_state() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            assert isinstance(app.screen, SecurityScreen)
            assert app.screen._report is None
            assert app.screen.severity_counts() == {}
            assert app.screen.visible_findings() == []
            assert "no scan" in app.screen.summary_text().lower()
            assert app.screen.export_report() is None  # nothing scanned
            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_screen_scan_filter_and_detail(tmp_path: Path) -> None:
    _bad_tree(tmp_path)
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SecurityScreen)

            screen.query_one("#security-target", Input).value = str(tmp_path)
            report = screen.run_scan()
            await pilot.pause()
            assert report is not None and report.findings
            assert sum(screen.severity_counts().values()) == len(report.findings)
            assert screen.query_one("#security-findings").row_count == len(report.findings)

            # Text search narrows the visible findings.
            box = screen.query_one("#security-search", Input)
            box.value = "eval"
            await pilot.pause()
            assert screen.visible_findings()
            assert all("eval" in f.rule or "eval" in f.message.lower() for f in screen.visible_findings())
            box.value = "no-such-finding"
            await pilot.pause()
            assert screen.visible_findings() == []
            box.value = ""
            await pilot.pause()

            # Detail block carries CWE / CVSS / remediation.
            detail = screen.detail_text(screen.visible_findings()[0])
            assert "CWE" in detail and "CVSS" in detail and "Remediation" in detail

            # Cycling the profile changes it.
            before = screen._profile
            screen.action_cycle_profile()
            assert screen._profile != before

            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_screen_export_report(tmp_path: Path) -> None:
    _bad_tree(tmp_path)
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SecurityScreen)
            screen.run_scan(str(tmp_path))
            out = screen.export_report(tmp_path / "report.md")
            assert out is not None and out.exists()
            assert "# " in out.read_text(encoding="utf-8")
            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_screen_export_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default export must land in the per-user data dir, never in cwd."""
    _bad_tree(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SecurityScreen)
            screen.run_scan(str(tmp_path))
            out = screen.export_report()
            assert out is not None and out.exists()
            assert out.parent == tmp_path / ".local" / "share" / "tyb-zyv-ai" / "reports"
            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_screen_scan_missing_target(tmp_path: Path) -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SecurityScreen)
            assert screen.run_scan(str(tmp_path / "ghost")) is None
            assert screen._report is None
            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_screen_buttons_dispatch(tmp_path: Path) -> None:
    _bad_tree(tmp_path)
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            app.action_show_security()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SecurityScreen)
            screen.query_one("#security-target", Input).value = str(tmp_path)
            screen.query_one("#btn-run", Button).press()
            await pilot.pause()
            assert screen._report is not None
            screen.query_one("#btn-export", Button).press()
            await pilot.pause()
            screen.query_one("#btn-filter", Button).press()
            await pilot.pause()
            screen.query_one("#btn-search", Button).press()
            await pilot.pause()
            assert screen.query_one("#security-search", Input).has_focus
            await pilot.press("escape")

    asyncio.run(scenario())


def test_ctrl_s_opens_security() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, SecurityScreen)
            await pilot.press("escape")

    asyncio.run(scenario())


def test_security_command_opens_screen() -> None:
    app = ZyvoTUI(registry=_registry(), backend=EchoBackend())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            box = app.screen.query_one("#command", Input)
            box.value = ":security"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SecurityScreen)
            await pilot.press("escape")

    asyncio.run(scenario())


def test_dashboard_has_security_binding() -> None:
    keys = {binding[0] for binding in DashboardScreen.BINDINGS}
    assert "s" in keys
