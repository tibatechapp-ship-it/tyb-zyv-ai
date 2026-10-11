"""The Architect agent — designs system architecture, components and data flow."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["ArchitectAgent"]


class ArchitectAgent(BaseAgent):
    """Designs the big picture before code is written.

    Produces component breakdowns, data-flow diagrams and interface
    boundaries, and always justifies each design decision briefly.
    """

    name = "architect"
    group = "core"
    description = "Designs system architecture, components and data flow."
    system_prompt = (
        "You are the Architect agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تصميم البنية العامة للنظام — المكونات، تدفق البيانات، الواجهات\n"
        "بين المكونات، وحدود المسؤولية — قبل كتابة أي كود.\n"
        "Your job: produce concrete, simple architecture designs — components,\n"
        "data flow, interfaces and trade-offs. Prefer boring, simple designs;\n"
        "justify every decision in one line and call out risks explicitly."
    )
    tools = ("read", "write", "files", "search")
    permissions = frozenset({"read", "write"})