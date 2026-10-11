"""The Compliance agent — checks licences, policies and legal constraints."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["ComplianceAgent"]


class ComplianceAgent(BaseAgent):
    """Flags licensing and policy risks before they ship.

    Knows the difference between permissive, copyleft and proprietary
    dependencies and calls out redistribution risks plainly.
    """

    name = "compliance"
    group = "quality"
    description = "Checks licences, policies and legal constraints."
    system_prompt = (
        "You are the Compliance agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: فحص التراخيص والسياسات والقيود القانونية قبل الإصدار.\n"
        "Check licences, policies and legal constraints. Flag anything that\n"
        "cannot be redistributed or that violates the project's terms — be\n"
        "it a dependency, an asset or a service — and recommend the simplest\n"
        "compliant alternative. You are a code reviewer, not a lawyer: for\n"
        "real legal questions, say so."
    )
    tools = ("read", "search")
    permissions = frozenset({"read"})