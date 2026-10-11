"""The Reviewer agent — reviews code for correctness, style and clarity."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["ReviewerAgent"]


class ReviewerAgent(BaseAgent):
    """Audits other agents' work with structured, actionable feedback.

    Reports findings as a numbered list with file/line references and
    separates blockers from suggestions and nitpicks.
    """

    name = "reviewer"
    group = "core"
    description = "Reviews code for correctness, style and clarity."
    system_prompt = (
        "You are the Reviewer agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: مراجعة الكود بدقة — الصحة، الأسلوب، الوضوح، وقابلية الصيانة.\n"
        "Review code for correctness, style and clarity. Report findings as a\n"
        "numbered list with file:line references, and separate blockers from\n"
        "suggestions and nitpicks. Be honest and specific — never approve work\n"
        "you have not actually checked."
    )
    tools = ("read", "search")
    permissions = frozenset({"read"})