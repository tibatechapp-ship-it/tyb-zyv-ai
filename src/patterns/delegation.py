"""Delegation pattern — a lead agent plans, a worker executes."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.core.types import AgentResult
from src.patterns._stage import run_stage
from src.tools.executor import ToolPolicy, ToolRun

__all__ = ["Delegation"]


class Delegation:
    """Two-stage hand-off: the lead produces guidance, the worker acts on it."""

    def __init__(
        self, lead: BaseAgent, worker: BaseAgent, *, tools: ToolPolicy | None = None
    ) -> None:
        self.lead = lead
        self.worker = worker
        self._tools = tools
        #: Tool traces from the most recent :meth:`run` (empty without a policy).
        self.tool_runs: list[ToolRun] = []

    def run(self, task: str) -> AgentResult:
        """Ask ``lead`` to plan, then run ``worker`` with that plan as context.

        If the lead fails, its (failed) result is returned and the worker is not
        invoked.
        """
        self.tool_runs = []
        plan = run_stage(self.lead, task, tools=self._tools)
        if plan.tool_run is not None:
            self.tool_runs.append(plan.tool_run)
        if not plan.result.ok:
            return plan.result
        work = run_stage(self.worker, task, context=plan.result.output, tools=self._tools)
        if work.tool_run is not None:
            self.tool_runs.append(work.tool_run)
        return work.result

