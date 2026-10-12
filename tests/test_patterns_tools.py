"""Tests for running the coordination patterns with real tool execution."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from src.agents.base import BaseAgent
from src.core.types import Message
from src.patterns._stage import run_stage
from src.patterns.delegation import Delegation
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline
from src.patterns.supervisor import Supervisor
from src.tools.executor import ToolPolicy


class _ScriptedBackend:
    """Returns canned outputs in order (the last one repeats if called again)."""

    def __init__(self, *outputs: str) -> None:
        self._outputs = list(outputs) or [""]
        self.calls = 0
        self.seen: list[str] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.seen.append(messages[-1].content)
        output = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        return output


class _FailBackend:
    """Always raises — models a backend (or tool run) that fails."""

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        raise RuntimeError("boom")


def _agent(name: str, backend: object, *, tools: tuple[str, ...] = ()) -> BaseAgent:
    agent = BaseAgent(backend)  # type: ignore[arg-type]
    agent.name = name
    if tools:
        agent.tools = tools
    return agent


def _read_call(path: str) -> str:
    return f'<tool-call name="read" args=\'{{"path": "{path}"}}\'/>'


# ── ToolPolicy ───────────────────────────────────────────────────────────
def test_tool_policy_defaults() -> None:
    policy = ToolPolicy()
    assert policy.root is None
    assert policy.max_steps == 5
    assert policy.max_calls_per_step == 5


# ── run_stage ────────────────────────────────────────────────────────────
def test_run_stage_without_tools_is_single_call() -> None:
    backend = _ScriptedBackend("answer")
    outcome = run_stage(_agent("a", backend), "task")
    assert outcome.tool_run is None
    assert outcome.result.output == "answer"
    assert backend.calls == 1


def test_run_stage_with_tools_executes_read(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello world", encoding="utf-8")
    backend = _ScriptedBackend(_read_call("note.txt"), "final answer")
    agent = _agent("a", backend, tools=("read",))

    outcome = run_stage(agent, "look", tools=ToolPolicy(root=tmp_path))

    assert outcome.tool_run is not None
    assert outcome.result.ok
    assert outcome.result.output == "final answer"
    assert outcome.tool_run.steps == 2
    assert outcome.tool_run.outcomes[0].ok
    assert "hello world" in outcome.tool_run.outcomes[0].output
    # the tool result was fed back to the model on the next turn
    assert "hello world" in backend.seen[-1]


def test_run_stage_tool_refused_when_not_declared(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("secret", encoding="utf-8")
    backend = _ScriptedBackend(_read_call("f.txt"), "answer")
    agent = _agent("a", backend)  # declares no tools

    outcome = run_stage(agent, "t", tools=ToolPolicy(root=tmp_path))

    assert outcome.tool_run is not None
    assert outcome.tool_run.outcomes[0].ok is False
    assert "not permitted" in (outcome.tool_run.outcomes[0].error or "")


# ── Supervisor ───────────────────────────────────────────────────────────
def test_supervisor_without_tools_has_no_traces() -> None:
    report = Supervisor([_agent("a", _ScriptedBackend("X"))]).run("t")
    assert report.tool_runs == []


def test_supervisor_with_tools_collects_traces(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("data", encoding="utf-8")

    def make(name: str) -> BaseAgent:
        return _agent(name, _ScriptedBackend(_read_call("f.txt"), "done"), tools=("read",))

    report = Supervisor([make("a"), make("b")], tools=ToolPolicy(root=tmp_path)).run("t")

    assert report.ok is True
    assert len(report.results) == 2
    assert len(report.tool_runs) == 2
    assert all(run.outcomes[0].ok for run in report.tool_runs)


# ── Pipeline ─────────────────────────────────────────────────────────────
def test_pipeline_without_tools_has_no_traces() -> None:
    pipe = Pipeline([_agent("a", _ScriptedBackend("A")), _agent("b", _ScriptedBackend("B"))])
    assert pipe.run("t").output == "B"
    assert pipe.tool_runs == []


def test_pipeline_with_tools_chains_and_collects(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("seed", encoding="utf-8")
    first_backend = _ScriptedBackend(_read_call("f.txt"), "stage-one")
    second_backend = _ScriptedBackend("stage-two")
    first = _agent("a", first_backend, tools=("read",))
    second = _agent("b", second_backend, tools=("read",))

    pipe = Pipeline([first, second], tools=ToolPolicy(root=tmp_path))
    result = pipe.run("go")

    assert result.output == "stage-two"
    assert result.agent == "b"
    assert len(pipe.tool_runs) == 2
    assert "stage-one" in second_backend.seen[-1]  # context chained forward


def test_pipeline_with_tools_early_stop(tmp_path: Path) -> None:
    bad = _agent("bad", _FailBackend(), tools=("read",))
    never_backend = _ScriptedBackend("N")
    never = _agent("never", never_backend, tools=("read",))

    pipe = Pipeline([bad, never], tools=ToolPolicy(root=tmp_path))
    result = pipe.run("t")

    assert not result.ok
    assert result.agent == "bad"
    assert never.metrics.tasks_run == 0
    assert len(pipe.tool_runs) == 1


# ── Fanout ───────────────────────────────────────────────────────────────
def test_fanout_empty_with_tools() -> None:
    fan = Fanout([], tools=ToolPolicy())
    assert fan.run("t") == []
    assert fan.tool_runs == []


def test_fanout_with_tools_preserves_order(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("z", encoding="utf-8")

    def make(name: str) -> BaseAgent:
        return _agent(name, _ScriptedBackend(_read_call("f.txt"), name), tools=("read",))

    fan = Fanout([make("a"), make("b")], max_workers=2, tools=ToolPolicy(root=tmp_path))
    results = fan.run("t")

    assert [r.output for r in results] == ["a", "b"]
    assert len(fan.tool_runs) == 2


# ── Delegation ───────────────────────────────────────────────────────────
def test_delegation_with_tools(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("p", encoding="utf-8")
    lead = _agent("lead", _ScriptedBackend(_read_call("f.txt"), "PLAN"), tools=("read",))
    worker_backend = _ScriptedBackend("DONE")
    worker = _agent("worker", worker_backend, tools=("read",))

    result = Delegation(lead, worker, tools=ToolPolicy(root=tmp_path)).run("t")

    assert result.output == "DONE"
    assert "PLAN" in worker_backend.seen[-1]
    deleg = Delegation(lead, worker, tools=ToolPolicy(root=tmp_path))
    deleg.run("t")
    assert len(deleg.tool_runs) == 2


def test_delegation_with_tools_lead_fails(tmp_path: Path) -> None:
    lead = _agent("lead", _FailBackend(), tools=("read",))
    worker = _agent("worker", _ScriptedBackend("DONE"), tools=("read",))

    deleg = Delegation(lead, worker, tools=ToolPolicy(root=tmp_path))
    result = deleg.run("t")

    assert not result.ok
    assert worker.metrics.tasks_run == 0
    assert len(deleg.tool_runs) == 1
