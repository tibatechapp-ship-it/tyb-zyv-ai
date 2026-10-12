"""Pipeline pattern — each agent's output becomes the next one's context."""

from __future__ import annotations

from collections.abc import Iterable

from src.agents.base import BaseAgent
from src.core.types import AgentResult
from src.patterns._stage import run_stage
from src.tools.executor import ToolPolicy, ToolRun

__all__ = ["Pipeline"]


class Pipeline:
    """Runs agents in order, chaining each output into the next as context."""

    def __init__(
        self, agents: Iterable[BaseAgent], *, tools: ToolPolicy | None = None
    ) -> None:
        self._agents = list(agents)
        if not self._agents:
            raise ValueError("Pipeline needs at least one agent")
        self._tools = tools
        #: Tool traces from the most recent :meth:`run` (empty without a policy).
        self.tool_runs: list[ToolRun] = []

    def run(self, task: str) -> AgentResult:
        """Run the chain and return the final agent's result.

        The chain stops early (returning the failed result) as soon as one
        stage fails.
        """
        self.tool_runs = []
        context = ""
        result: AgentResult | None = None
        for agent in self._agents:
            outcome = run_stage(agent, task, context=context, tools=self._tools)
            if outcome.tool_run is not None:
                self.tool_runs.append(outcome.tool_run)
            result = outcome.result
            if not result.ok:
                return result
            context = result.output
        assert result is not None  # guarded by the constructor
        return result

