"""Delegation pattern — a lead agent plans, a worker executes."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.core.types import AgentResult

__all__ = ["Delegation"]


class Delegation:
    """Two-stage hand-off: the lead produces guidance, the worker acts on it."""

    def __init__(self, lead: BaseAgent, worker: BaseAgent) -> None:
        self.lead = lead
        self.worker = worker

    def run(self, task: str) -> AgentResult:
        """Ask ``lead`` to plan, then run ``worker`` with that plan as context.

        If the lead fails, its (failed) result is returned and the worker is not
        invoked.
        """
        plan = self.lead.run(task)
        if not plan.ok:
            return plan
        return self.worker.run(task, context=plan.output)
