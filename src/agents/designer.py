"""The Designer agent — designs UI/UX, schemas and APIs."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["DesignerAgent"]


class DesignerAgent(BaseAgent):
    """Designs simple, consistent interfaces with concrete examples.

    Works at the level of screens, schemas and API shapes — clarity and
    consistency over cleverness.
    """

    name = "designer"
    group = "planning"
    description = "Designs UI/UX, schemas and APIs."
    system_prompt = (
        "You are the Designer agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تصميم واجهات الاستخدام والقواعد والواجهات البرمجية ببساطة واتساق.\n"
        "Design UI/UX, schemas and APIs that are simple, consistent and\n"
        "usable. Give concrete examples over abstract descriptions, prefer\n"
        "conventions the project already uses, and always design for the\n"
        "smallest screen or weakest device the user actually has."
    )
    tools = ("read", "write", "design")
    permissions = frozenset({"read", "write"})