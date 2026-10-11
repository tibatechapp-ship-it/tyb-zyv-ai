"""Tests for src.agents — base agent, state, metrics, registry, messaging."""

from __future__ import annotations

import pytest

from src.agents.base import BaseAgent
from src.agents.communication import AgentCommunication, Envelope
from src.agents.metrics import AgentMetrics
from src.agents.registry import AgentRegistry, DuplicateAgentError
from src.agents.state import AgentState, AgentStatus
from src.core.backend import EchoBackend
from src.core.types import Message


class _Dummy(BaseAgent):
    name = "dummy"
    group = "core"
    description = "A dummy agent."
    system_prompt = "be brief"
    tools = ("echo",)
    permissions = frozenset({"read"})


class _BoomBackend:
    def generate(self, messages: list[Message], *, model: str | None = None) -> str:
        raise RuntimeError("boom")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Message], str | None]] = []

    def generate(self, messages: list[Message], *, model: str | None = None) -> str:
        self.calls.append((list(messages), model))
        return "ok"


# ── AgentState ───────────────────────────────────────────────────────────
def test_state_defaults() -> None:
    state = AgentState()
    assert state.status is AgentStatus.IDLE
    assert state.last_task is None
    assert state.last_error is None


def test_state_mark_busy_truncates() -> None:
    state = AgentState()
    state.mark_busy("x" * 500)
    assert state.status == AgentStatus.BUSY
    assert state.last_task is not None and len(state.last_task) == 200


def test_state_mark_idle_clears_error() -> None:
    state = AgentState()
    state.mark_error("nope")
    state.mark_idle()
    assert state.status is AgentStatus.IDLE
    assert state.last_error is None


def test_state_mark_error_truncates() -> None:
    state = AgentState()
    state.mark_error("e" * 900)
    assert state.status is AgentStatus.ERROR
    assert state.last_error is not None and len(state.last_error) == 500


def test_state_to_dict() -> None:
    state = AgentState()
    state.mark_busy("task")
    data = state.to_dict()
    assert data["status"] == "busy"
    assert data["last_task"] == "task"


# ── AgentMetrics ─────────────────────────────────────────────────────────
def test_metrics_empty() -> None:
    metrics = AgentMetrics()
    assert metrics.tasks_ok == 0
    assert metrics.avg_seconds == 0.0


def test_metrics_record_ok_and_fail() -> None:
    metrics = AgentMetrics()
    metrics.record(1.0, ok=True)
    metrics.record(3.0, ok=False)
    assert metrics.tasks_run == 2
    assert metrics.tasks_failed == 1
    assert metrics.tasks_ok == 1
    assert metrics.avg_seconds == 2.0
    assert metrics.last_seconds == 3.0
    snapshot = metrics.to_dict()
    assert snapshot["tasks_run"] == 2 and snapshot["tasks_failed"] == 1


# ── BaseAgent ────────────────────────────────────────────────────────────
def test_base_agent_run_success() -> None:
    agent = _Dummy(EchoBackend())
    result = agent.run("hello")
    assert result.ok
    assert result.output == "echo: hello"
    assert result.agent == "dummy"
    assert agent.state.status is AgentStatus.IDLE
    assert agent.metrics.tasks_run == 1


def test_base_agent_build_messages_with_context() -> None:
    agent = _Dummy(EchoBackend())
    messages = agent.build_messages("task", "ctx")
    assert messages[0] == Message("system", "be brief")
    assert "ctx" in messages[1].content and "task" in messages[1].content


def test_base_agent_build_messages_without_context() -> None:
    agent = _Dummy(EchoBackend())
    assert agent.build_messages("task")[1].content == "task"


def test_base_agent_run_failure_marks_error() -> None:
    class _Boom(BaseAgent):
        name = "boom"

        def build_messages(self, task: str, context: str = "") -> list[Message]:
            raise RuntimeError("kaboom")

    agent = _Boom(EchoBackend())
    result = agent.run("x")
    assert not result.ok
    assert result.error == "kaboom"
    assert agent.state.status is AgentStatus.ERROR
    assert agent.metrics.tasks_failed == 1


def test_base_agent_run_many() -> None:
    agent = _Dummy(EchoBackend())
    results = agent.run_many(["a", "b"])
    assert [r.output for r in results] == ["echo: a", "echo: b"]
    assert agent.metrics.tasks_run == 2


def test_base_agent_info() -> None:
    agent = _Dummy(EchoBackend())
    info = agent.info()
    assert info["name"] == "dummy"
    assert info["group"] == "core"
    assert info["permissions"] == ["read"]


def test_base_agent_repr() -> None:
    assert "dummy" in repr(_Dummy(EchoBackend()))


def test_base_agent_passes_model_to_backend() -> None:
    agent = _Dummy(EchoBackend())
    agent.model = "m1"
    assert agent.model == "m1"


# ── AgentRegistry ────────────────────────────────────────────────────────
def _agents() -> list[BaseAgent]:
    class _Coder(BaseAgent):
        name = "coder"
        group = "core"

    class _Planner(BaseAgent):
        name = "planner"
        group = "planning"

    return [_Dummy(EchoBackend()), _Coder(EchoBackend()), _Planner(EchoBackend())]


def test_registry_register_and_query() -> None:
    registry = AgentRegistry()
    agent = _Dummy(EchoBackend())
    registry.register(agent)
    assert len(registry) == 1
    assert "dummy" in registry
    assert registry.has("dummy")
    assert registry.get("dummy") is agent
    assert registry.names() == ["dummy"]
    assert registry.all() == [agent]


def test_registry_duplicate_raises() -> None:
    registry = AgentRegistry([_Dummy(EchoBackend())])
    with pytest.raises(DuplicateAgentError):
        registry.register(_Dummy(EchoBackend()))


def test_registry_replace() -> None:
    first = _Dummy(EchoBackend())
    second = _Dummy(EchoBackend())
    registry = AgentRegistry([first])
    registry.register(second, replace=True)
    assert registry.get("dummy") is second


def test_registry_get_missing_raises() -> None:
    with pytest.raises(KeyError):
        AgentRegistry().get("ghost")


def test_registry_unregister_and_groups() -> None:
    registry = AgentRegistry()
    coder = _Dummy(EchoBackend())
    coder.group = "core"
    planner = _Dummy(EchoBackend())
    planner.name = "planner"
    planner.group = "planning"
    registry.register(coder)
    registry.register(planner)
    assert registry.by_group("planning") == [planner]
    assert set(registry.groups()) == {"core", "planning"}
    assert registry.unregister("planner") is planner
    assert registry.unregister("planner") is None


def test_registry_snapshot_and_iter() -> None:
    registry = AgentRegistry([_Dummy(EchoBackend())])
    assert len(registry.snapshot()[0]["name"]) > 0  # type: ignore[arg-type]
    assert list(registry)[0].name == "dummy"
    assert isinstance(AgentRegistry.from_agents([_Dummy(EchoBackend())]), AgentRegistry)


def test_registry_contains_non_string() -> None:
    assert 42 not in AgentRegistry()


# ── AgentCommunication ───────────────────────────────────────────────────
def test_communication_direct_and_drain() -> None:
    bus = AgentCommunication()
    bus.subscribe("bob")
    bus.send(Envelope(sender="alice", recipient="bob", subject="hi", body="yo"))
    assert len(bus.inbox("bob")) == 1
    drained = bus.drain("bob")
    assert drained[0].subject == "hi"
    assert bus.inbox("bob") == []
    assert len(bus.log()) == 1


def test_communication_broadcast_excludes_sender() -> None:
    bus = AgentCommunication()
    bus.subscribe("alice")
    bus.subscribe("bob")
    bus.subscribe("carol")
    bus.broadcast(Envelope(sender="alice", subject="all", body="x"))
    assert bus.inbox("alice") == []
    assert len(bus.inbox("bob")) == 1
    assert len(bus.inbox("carol")) == 1


def test_communication_send_broadcast_when_no_recipient() -> None:
    bus = AgentCommunication()
    bus.subscribe("bob")
    bus.send(Envelope(sender="alice", recipient=None, subject="s", body="b"))
    assert len(bus.inbox("bob")) == 1
    assert len(bus.log()) == 1
