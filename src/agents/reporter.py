"""The Reporter agent — summarises outcomes for the human."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["ReporterAgent"]


class ReporterAgent(BaseAgent):
    """Turns team output into one clear human message.

    Summarises what happened, what it means, and the single next step —
    in the user's own language, short and honest.
    """

    name = "reporter"
    group = "ops"
    description = "Summarises outcomes for the human."
    system_prompt = (
        "You are the Reporter agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تلخيص نتائج العمل للمستخدم — ماذا حدث، ماذا يعني، والخطوة التالية.\n"
        "Summarise outcomes for humans: what happened, what it means for\n"
        "them, and the single next step — in the user's own language and\n"
        "tone. Be short, concrete and honest: if something failed or is\n"
        "unverified, say so plainly."
    )
    tools = ("read", "write")
    permissions = frozenset({"write"})