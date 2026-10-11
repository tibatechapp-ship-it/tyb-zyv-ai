"""Tests for sub-agents — tool-restricted child agents with a depth guard.

Uses the real :class:`CoderAgent` as the parent and an offline recording
backend, so nothing touches the network.
"""

from __future__ import annotations

import pytest

from src.agents import CoderAgent, SubAgent, SubAgentRunner, SubAgentSpec
from src.core import EchoBackend, ModelRouter
from src.core.types import Message


class RecordingBackend:
    """Echoes the last message and records the ``model`` argument per call."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def generate(self, messages: list[Message], *, model: str | None = None) -> str:
        self.calls.append(model)
        return f"sub-echo: {messages[-1].content}"


# -- SubAgent ---------------------------------------------------------------
def test_sub_agent_metadata_and_persona() -> None:
    sub = SubAgent(EchoBackend(), role="linter", tools=("read",))
    assert sub.name == "linter"
    assert sub.group == "sub"
    assert sub.tools == ("read",)
    assert sub.permissions == frozenset({"read"})
    assert sub.depth == 1
    assert sub.parent is None
    assert sub.description == "Sub-agent (linter)."


def test_sub_agent_carries_depth_and_parent() -> None:
    parent = CoderAgent(EchoBackend())
    sub = SubAgent(EchoBackend(), role="inner", tools=("read",), parent=parent, depth=2)
    assert sub.parent is parent
    assert sub.depth == 2


# -- SubAgentRunner ---------------------------------------------------------
def test_spawn_runs_subtask_and_echoes() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)
    result = runner.spawn(parent, role="linter", task="lint main.py")
    assert result.ok is True
    assert "lint main.py" in result.output


def test_spawn_restricts_tools_to_parents() -> None:
    parent = CoderAgent(EchoBackend())
    runner = SubAgentRunner(EchoBackend())
    with pytest.raises(ValueError, match="does not hold"):
        runner.spawn(parent, role="ssh", task="x", tools=("ssh",))


def test_spawn_depth_guard_returns_failed_result() -> None:
    backend = EchoBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend, max_depth=1)
    sub = SubAgent(backend, role="level1", tools=("read",), parent=parent, depth=1)
    result = runner.spawn(sub, role="level2", task="x")
    assert result.ok is False
    assert "depth" in (result.error or "")


def test_spawn_uses_router_model_when_none_given() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    router = ModelRouter()
    runner = SubAgentRunner(backend, router=router)
    runner.spawn(parent, role="linter", task="lint the code", model=None)
    expected = router.route("lint the code").model.name
    assert backend.calls == [expected]


def test_spawn_keeps_explicit_model() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend, router=ModelRouter())
    runner.spawn(parent, role="linter", task="x", model="explicit-model")
    assert backend.calls == ["explicit-model"]


def test_run_many_batch() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)
    results = runner.run_many(
        parent,
        [
            SubAgentSpec(role="a", task="t1"),
            SubAgentSpec(role="b", task="t2", tools=("read",), context="ctx"),
        ],
    )
    assert len(results) == 2
    assert all(result.ok for result in results)


def test_call_convenience() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)
    result = runner(parent, SubAgentSpec(role="a", task="t1", model="m1"))
    assert result.ok is True
    assert backend.calls == ["m1"]


def test_spec_to_dict() -> None:
    spec = SubAgentSpec(role="a", task="t", tools=("read",), model="m", context="c")
    assert spec.to_dict() == {
        "role": "a",
        "task": "t",
        "tools": ["read"],
        "model": "m",
        "context": "c",
    }


def test_router_only_used_when_present() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)  # no router
    runner.spawn(parent, role="linter", task="x")
    assert backend.calls == [None]


def test_sub_agent_builds_messages_with_parent_context() -> None:
    backend = RecordingBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)
    result = runner.spawn(parent, role="writer", task="do it", context="Design: X")
    assert result.ok is True
    assert "Design: X" in result.output


def test_sub_agent_with_bare_tools_parent_class() -> None:
    # a parent without tools may only spawn tool-less children

    class BareParent(CoderAgent):
        tools = ()

    bare = BareParent(EchoBackend())
    runner = SubAgentRunner(EchoBackend())
    result = runner.spawn(bare, role="thinker", task="think")
    assert result.ok is True


def test_runner_does_not_need_router_for_tools_validation() -> None:
    backend = EchoBackend()
    parent = CoderAgent(backend)
    runner = SubAgentRunner(backend)
    with pytest.raises(ValueError):
        runner.spawn(parent, role="x", task="y", tools=("everything",))