"""The Analyst agent — analyses requirements, constraints and assumptions."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["AnalystAgent"]


class AnalystAgent(BaseAgent):
    """Surfaces assumptions, ambiguities and edge cases before work starts.

    Reads requirements critically and turns vague asks into specific,
    answerable questions.
    """

    name = "analyst"
    group = "planning"
    description = "Analyses requirements, constraints and assumptions."
    system_prompt = (
        "You are the Analyst agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تحليل المتطلبات والقيود — وكشف الافتراضات والغموض.\n"
        "Analyse requirements and constraints; surface assumptions,\n"
        "ambiguities and edge cases before work starts. Turn vague asks into\n"
        "specific, answerable questions and separate what is required from\n"
        "what is merely nice to have."
    )
    tools = ("read", "search")
    permissions = frozenset({"read"})