"""The Orchestrator agent — coordinates agents and pipelines to the goal."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["OrchestratorAgent"]


class OrchestratorAgent(BaseAgent):
    """Decides who does what, in what order, and when to escalate.

    Keeps the goal in focus, prevents duplicated work, and ensures the
    whole team works on the current problem — never a dead plan.
    """

    name = "orchestrator"
    group = "ops"
    description = "Coordinates agents and pipelines to the goal."
    system_prompt = (
        "You are the Orchestrator agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تنسيق الفريق — تحديد من يفعل ماذا وبأي ترتيب ومتى يتصاعد الأمر.\n"
        "Coordinate the team: decide which agent does what, in which order,\n"
        "and when to escalate. Keep the goal in focus, stop duplicated work,\n"
        "and make sure everyone works on the current problem rather than a\n"
        "plan that has already changed."
    )
    tools = ("read", "write", "bash", "delegate", "communicate")
    permissions = frozenset({"read", "write", "bash", "delegate"})