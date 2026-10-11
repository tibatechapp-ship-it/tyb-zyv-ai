"""The Documentation agent — writes user and developer documentation."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["DocumentationAgent"]


class DocumentationAgent(BaseAgent):
    """Keeps docs current, runnable and honest.

    Writes in the user's own language when the user speaks one, keeps
    examples runnable, and never documents behaviour the code does not
    actually have.
    """

    name = "documentation"
    group = "quality"
    description = "Writes user and developer documentation."
    system_prompt = (
        "You are the Documentation agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: كتابة وثائق المستخدم والمطور تطابق الكود الفعلي وتبقيه مُحدَّثاً.\n"
        "Write user and developer documentation that matches the real code.\n"
        "Keep READMEs current, make every example runnable, use the user's\n"
        "own language when they speak one, and never describe behaviour the\n"
        "code does not actually have."
    )
    tools = ("read", "write")
    permissions = frozenset({"read", "write"})