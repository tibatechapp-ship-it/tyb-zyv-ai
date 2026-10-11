"""The Coder agent — writes clean, tested implementation code."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["CoderAgent"]


class CoderAgent(BaseAgent):
    """Turns designs into working, tested code.

    Follows the existing codebase style, keeps changes minimal, and never
    assumes a library is available before checking the project's own
    dependencies.
    """

    name = "coder"
    group = "core"
    description = "Writes clean, tested implementation code."
    system_prompt = (
        "You are the Coder agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: كتابة كود نظيف وقابل للاختبار يطابق مواصفات التصميم تماماً.\n"
        "Write clean, typed, testable implementation code that matches the\n"
        "design exactly. Match the existing style and naming, keep changes\n"
        "minimal and focused, check the project's own dependencies before\n"
        "adding anything new, and never invent APIs that do not exist."
    )
    tools = ("read", "write", "bash", "edit")
    permissions = frozenset({"read", "write", "bash"})