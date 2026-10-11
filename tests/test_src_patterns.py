"""Tests for src.patterns — the five coordination patterns."""

from __future__ import annotations

from collections.abc import Sequence

from src.agents.base import BaseAgent
from src.core.types import Message
from src.patterns.blackboard import Blackboard
from src.patterns.delegation import Delegation
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline
from src.patterns.supervisor import Supervisor


class _Backend:
    """Returns a fixed string, or raises when ``fail`` is set."""

    def __init__(self, output: str = "ok", fail: bool = False) -> None:
        self.output = output
        self.fail = fail
        self.last_context: str | None = None
        self.calls = 0

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.calls += 1
        self.last_context = messages[-1].content
        if self.fail:
            raise RuntimeError("boom")
        return self.output


def _make(name: str, output: str, fail: bool) -> BaseAgent:
    agent = BaseAgent(_Backend(output, fail))
    agent.name = name
    return agent


# ── Supervisor ───────────────────────────────────────────────────────────
def test_supervisor_all_ok() -> None:
    report = Supervisor([_make("a", "X", False), _make("b", "Y", False)]).run("task")
    assert report.ok is True
    assert report.failures == []
    assert report.summary() == "2/2 agents succeeded for task: task"


def test_supervisor_with_failure() -> None:
    report = Supervisor([_make("a", "X", False), _make("b", "", True)]).run("t")
    assert not report.ok
    assert len(report.failures) == 1
    assert report.summary() == "1/2 agents succeeded for task: t"


def test_supervisor_empty() -> None:
    report = Supervisor([]).run("task")
    assert report.ok is False
    assert report.summary() == "0/0 agents succeeded for task: task"


# ── Pipeline ─────────────────────────────────────────────────────────────
def test_pipeline_chains_output() -> None:
    first = _make("a", "A", False)
    second = _make("b", "B", False)
    result = Pipeline([first, second]).run("start")
    assert result.output == "B"
    assert result.agent == "b"
    assert "A" in (second.backend.last_context or "")  # type: ignore[attr-defined]


def test_pipeline_requires_agents() -> None:
    import pytest

    with pytest.raises(ValueError):
        Pipeline([])


def test_pipeline_early_stop_on_failure() -> None:
    bad = _make("bad", "", True)
    never = _make("never", "N", False)
    result = Pipeline([bad, never]).run("t")
    assert not result.ok
    assert result.agent == "bad"
    assert never.metrics.tasks_run == 0


# ── Fanout ───────────────────────────────────────────────────────────────
def test_fanout_runs_all_in_order() -> None:
    results = Fanout([_make("a", "1", False), _make("b", "2", False)]).run("t")
    assert [r.output for r in results] == ["1", "2"]


def test_fanout_empty() -> None:
    assert Fanout([]).run("t") == []


# ── Delegation ───────────────────────────────────────────────────────────
def test_delegation_lead_then_worker() -> None:
    lead = _make("lead", "PLAN", False)
    worker = _make("worker", "DONE", False)
    result = Delegation(lead, worker).run("t")
    assert result.output == "DONE"
    assert worker.metrics.tasks_run == 1


def test_delegation_stops_when_lead_fails() -> None:
    lead = _make("lead", "", True)
    worker = _make("worker", "DONE", False)
    result = Delegation(lead, worker).run("t")
    assert not result.ok
    assert worker.metrics.tasks_run == 0


# ── Blackboard ───────────────────────────────────────────────────────────
def test_blackboard_crud() -> None:
    board = Blackboard({"x": 1})
    assert board.get("x") == 1
    assert board.has("x")
    board.set("y", 2)
    assert list(board.keys()) == ["x", "y"]
    assert dict(board.items()) == {"x": 1, "y": 2}
    board.update({"z": 3})
    assert board.snapshot() == {"x": 1, "y": 2, "z": 3}
    assert board.get("missing", "default") == "default"
    board.delete("x")
    assert not board.has("x")
    board.clear()
    assert board.snapshot() == {}


def test_blackboard_append() -> None:
    board = Blackboard()
    board.append("log", "a")
    board.append("log", "b")
    assert board.get("log") == ["a", "b"]
