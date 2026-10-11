"""Tests for the 20 built-in agents — metadata, prompts, behaviour and wiring."""

from __future__ import annotations

import importlib

import pytest

from src.agents import AGENT_CLASSES, AGENT_GROUPS, all_agents
from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.core.backend import EchoBackend

#: module name -> (class name, group) — the exact contract of the 20 agents.
EXPECTED: dict[str, tuple[str, str]] = {
    "architect": ("ArchitectAgent", "core"),
    "coder": ("CoderAgent", "core"),
    "reviewer": ("ReviewerAgent", "core"),
    "tester": ("TesterAgent", "core"),
    "debugger": ("DebuggerAgent", "core"),
    "planner": ("PlannerAgent", "planning"),
    "analyst": ("AnalystAgent", "planning"),
    "researcher": ("ResearcherAgent", "planning"),
    "designer": ("DesignerAgent", "planning"),
    "estimator": ("EstimatorAgent", "planning"),
    "security_auditor": ("SecurityAuditorAgent", "quality"),
    "performance": ("PerformanceAgent", "quality"),
    "documentation": ("DocumentationAgent", "quality"),
    "refactor": ("RefactorAgent", "quality"),
    "compliance": ("ComplianceAgent", "quality"),
    "orchestrator": ("OrchestratorAgent", "ops"),
    "devops": ("DevOpsAgent", "ops"),
    "monitor": ("MonitorAgent", "ops"),
    "recovery": ("RecoveryAgent", "ops"),
    "reporter": ("ReporterAgent", "ops"),
}


def _has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


# ── package exports ─────────────────────────────────────────────────────
def test_twenty_modules_exported_with_correct_all() -> None:
    assert len(EXPECTED) == 20
    package = importlib.import_module("src.agents")
    for module_name, (class_name, _) in EXPECTED.items():
        assert hasattr(package, class_name), f"{class_name} missing from src.agents"
        module = importlib.import_module(f"src.agents.{module_name}")
        assert module.__all__ == [class_name]


# ── all_agents() wiring ──────────────────────────────────────────────────
def test_all_agents_order_matches_expected() -> None:
    agents = all_agents(EchoBackend())
    assert len(agents) == 20
    assert [a.name for a in agents] == list(EXPECTED)


def test_all_agents_names_unique() -> None:
    names = [a.name for a in all_agents(EchoBackend())]
    assert len(names) == len(set(names))


def test_all_agents_groups_balanced() -> None:
    agents = all_agents(EchoBackend())
    assert {a.group for a in agents} == set(AGENT_GROUPS)
    for group in AGENT_GROUPS:
        assert sum(1 for a in agents if a.group == group) == 5


# ── per-agent metadata ───────────────────────────────────────────────────
@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.__name__)
def test_agent_metadata(cls: type[BaseAgent]) -> None:
    module_name = cls.__module__.split(".")[-1]
    class_name, group = EXPECTED[module_name]
    assert cls.__name__ == class_name
    assert cls.__doc__ and cls.__doc__.strip()

    agent = cls(EchoBackend())
    assert isinstance(agent, BaseAgent)
    assert agent.name == module_name
    assert agent.group == group
    assert agent.description
    assert agent.system_prompt
    assert _has_arabic(agent.system_prompt), f"{module_name}: prompt lacks Arabic"
    assert any(ch.isascii() and ch.isalpha() for ch in agent.system_prompt), (
        f"{module_name}: prompt lacks English"
    )
    assert isinstance(agent.tools, tuple)
    assert isinstance(agent.permissions, frozenset)


# ── per-agent behaviour ──────────────────────────────────────────────────
@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.__name__)
def test_agent_runs_and_info(cls: type[BaseAgent]) -> None:
    agent = cls(EchoBackend())
    result = agent.run("hello")
    assert result.ok
    assert result.agent == agent.name
    assert result.output == "echo: hello"

    info = agent.info()
    assert info["name"] == agent.name
    assert info["group"] == agent.group
    assert info["description"] == agent.description
    assert info["tools"] == list(agent.tools)
    assert info["permissions"] == sorted(agent.permissions)


# ── registry integration ─────────────────────────────────────────────────
def test_registry_builds_all_twenty() -> None:
    registry = AgentRegistry(all_agents(EchoBackend()))
    assert len(registry) == 20
    for module_name in EXPECTED:
        assert registry.has(module_name)
    for group in AGENT_GROUPS:
        assert len(registry.by_group(group)) == 5


# ── TUI auto-registration ────────────────────────────────────────────────
def test_tuistate_auto_registers_all_agents() -> None:
    from src.tui.state import TuiState

    state = TuiState(backend=EchoBackend())
    assert len(state.registry) == 20
    assert state.registry.has("architect") and state.registry.has("reporter")


def test_tuistate_without_backend_is_empty() -> None:
    from src.tui.state import TuiState

    assert len(TuiState().registry) == 0