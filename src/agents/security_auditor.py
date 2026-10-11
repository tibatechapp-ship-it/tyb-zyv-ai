"""The SecurityAuditor agent — audits code and config for vulnerabilities."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["SecurityAuditorAgent"]


class SecurityAuditorAgent(BaseAgent):
    """Hunts secrets, injection, weak auth and unsafe defaults.

    Ranks findings by severity and pairs every finding with a concrete,
    minimal fix.
    """

    name = "security_auditor"
    group = "quality"
    description = "Audits code and config for vulnerabilities."
    system_prompt = (
        "You are the SecurityAuditor agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: مراجعة الكود والتهيئة بحثاً عن الثغرات — أسرار، حقن، مصادقة ضعيفة.\n"
        "Audit code and config for vulnerabilities: hardcoded secrets, command\n"
        "or SQL injection, weak authentication, unsafe defaults and risky\n"
        "permissions. Rank findings by severity with a concrete fix for each,\n"
        "and never log or request real credentials while auditing."
    )
    tools = ("read", "search", "scan")
    permissions = frozenset({"read"})