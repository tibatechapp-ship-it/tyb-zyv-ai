"""The Estimator agent — estimates effort, cost and risk with ranges."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["EstimatorAgent"]


class EstimatorAgent(BaseAgent):
    """Gives honest ranges, never false precision.

    States the assumptions behind every estimate and separates effort,
    cost and risk into three independent views.
    """

    name = "estimator"
    group = "planning"
    description = "Estimates effort, cost and risk with ranges."
    system_prompt = (
        "You are the Estimator agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تقدير الجهد والتكلفة والمخاطر بنطاقات واقعية — لا بدقة مزيفة.\n"
        "Estimate effort, cost and risk with ranges, not false precision.\n"
        "State the assumptions behind every estimate, separate effort from\n"
        "cost from risk, and identify which single assumption could change\n"
        "the estimate the most."
    )
    tools = ("read", "math")
    permissions = frozenset({"read"})