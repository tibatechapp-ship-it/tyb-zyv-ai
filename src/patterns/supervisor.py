"""Supervisor pattern — one coordinator fans work to a team and aggregates."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from src.agents.base import BaseAgent
from src.core.types import AgentResult
from src.patterns._stage import run_stage
from src.tools.executor import ToolPolicy, ToolRun

__all__ = ["Supervisor", "SupervisorReport"]


@dataclass
class SupervisorReport:
    """Aggregated outcome of a supervisory run."""

    task: str
    results: list[AgentResult] = field(default_factory=list)
    #: One entry per result that ran with tools (empty when no ToolPolicy is set).
    tool_runs: list[ToolRun] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every delegate succeeded."""
        return bool(self.results) and all(r.ok for r in self.results)

    @property
    def failures(self) -> list[AgentResult]:
        return [r for r in self.results if not r.ok]

    def summary(self) -> str:
        done = sum(1 for r in self.results if r.ok)
        return f"{done}/{len(self.results)} agents succeeded for task: {self.task}"


class Supervisor:
    """Runs every delegate over the same task and collects their results."""

    def __init__(
        self, agents: Iterable[BaseAgent], *, tools: ToolPolicy | None = None
    ) -> None:
        self._agents: list[BaseAgent] = list(agents)
        self._tools = tools

    @property
    def agents(self) -> Sequence[BaseAgent]:
        return tuple(self._agents)

    def run(self, task: str, *, context: str = "") -> SupervisorReport:
        report = SupervisorReport(task=task)
        for agent in self._agents:
            outcome = run_stage(agent, task, context=context, tools=self._tools)
            report.results.append(outcome.result)
            if outcome.tool_run is not None:
                report.tool_runs.append(outcome.tool_run)
        return report

