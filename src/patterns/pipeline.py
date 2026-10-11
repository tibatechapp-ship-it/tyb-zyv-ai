"""Pipeline pattern — each agent's output becomes the next one's context."""

from __future__ import annotations

from collections.abc import Iterable

from src.agents.base import BaseAgent
from src.core.types import AgentResult

__all__ = ["Pipeline"]


class Pipeline:
    """Runs agents in order, chaining each output into the next as context."""

    def __init__(self, agents: Iterable[BaseAgent]) -> None:
        self._agents = list(agents)
        if not self._agents:
            raise ValueError("Pipeline needs at least one agent")

    def run(self, task: str) -> AgentResult:
        """Run the chain and return the final agent's result.

        The chain stops early (returning the failed result) as soon as one
        stage fails.
        """
        context = ""
        result: AgentResult | None = None
        for agent in self._agents:
            result = agent.run(task, context=context)
            if not result.ok:
                return result
            context = result.output
        assert result is not None  # guarded by the constructor
        return result
