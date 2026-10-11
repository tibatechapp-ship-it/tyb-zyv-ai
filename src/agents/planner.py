"""The Planner agent — breaks goals into ordered, testable tasks."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["PlannerAgent"]


class PlannerAgent(BaseAgent):
    """Turns goals into a concrete, ordered plan.

    Each task is small enough to finish in one session and carries clear
    acceptance criteria, dependencies and risks.
    """

    name = "planner"
    group = "planning"
    description = "Breaks goals into ordered, testable tasks."
    system_prompt = (
        "You are the Planner agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تقسيم الأهداف الكبيرة إلى مهام مرتبة وقابلة للاختبار بوضوح.\n"
        "Break goals into ordered, testable tasks, each with acceptance\n"
        "criteria and small enough to finish in one session. Call out\n"
        "dependencies between tasks, risks, and what can be skipped first if\n"
        "time runs short."
    )
    tools = ("read", "brainstorm")
    permissions = frozenset({"read"})