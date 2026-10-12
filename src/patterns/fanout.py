"""Fan-out pattern — the same task broadcast to many agents in parallel."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from src.agents.base import BaseAgent
from src.core.types import AgentResult
from src.patterns._stage import StageOutcome, run_stage
from src.tools.executor import ToolPolicy, ToolRun

__all__ = ["Fanout"]


class Fanout:
    """Runs every agent on the same task concurrently and gathers the results."""

    def __init__(
        self,
        agents: Iterable[BaseAgent],
        *,
        max_workers: int | None = None,
        tools: ToolPolicy | None = None,
    ) -> None:
        self._agents = list(agents)
        self._max_workers = max_workers
        self._tools = tools
        #: Tool traces from the most recent :meth:`run` (empty without a policy).
        self.tool_runs: list[ToolRun] = []

    def run(self, task: str, *, context: str = "") -> list[AgentResult]:
        """Return one result per agent, in registration order."""
        self.tool_runs = []
        if not self._agents:
            return []
        workers = self._max_workers or min(len(self._agents), 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes: list[StageOutcome] = list(
                pool.map(
                    lambda a: run_stage(a, task, context=context, tools=self._tools),
                    self._agents,
                )
            )
        self.tool_runs = [o.tool_run for o in outcomes if o.tool_run is not None]
        return [o.result for o in outcomes]

