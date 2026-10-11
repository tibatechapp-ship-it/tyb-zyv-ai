"""The Recovery agent — restores service after failures."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["RecoveryAgent"]


class RecoveryAgent(BaseAgent):
    """Brings systems back with the least disruption possible.

    Backs up state before touching anything and verifies the system is
    genuinely healthy again before calling the incident closed.
    """

    name = "recovery"
    group = "ops"
    description = "Restores service after failures."
    system_prompt = (
        "You are the Recovery agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: استعادة الخدمة بعد الأعطال بأقل ضرر ممكن.\n"
        "Restore service after failures with the least disruption. Back up\n"
        "state before touching anything, prefer the least invasive fix, and\n"
        "verify the system is genuinely healthy again before calling the\n"
        "incident closed — a process that starts is not proof it works."
    )
    tools = ("read", "bash", "restart")
    permissions = frozenset({"read", "bash"})