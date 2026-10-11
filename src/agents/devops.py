"""The DevOps agent — builds, deploys and provisions infrastructure."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["DevOpsAgent"]


class DevOpsAgent(BaseAgent):
    """Makes deployment reliable, repeatable and reversible.

    Automates repeated steps, pins versions, and treats rollback as a
    first-class capability.
    """

    name = "devops"
    group = "ops"
    description = "Builds, deploys and provisions infrastructure."
    system_prompt = (
        "You are the DevOps agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: البناء والنشر والتهيئة بشكل موثوق وقابل للتكرار.\n"
        "Build, deploy and provision reliably. Automate repeatable steps,\n"
        "pin dependency versions, and make rollbacks as easy as deployments.\n"
        "Never deploy unverified work, and keep every pipeline step visible\n"
        "and auditable."
    )
    tools = ("read", "bash", "deploy")
    permissions = frozenset({"read", "bash", "deploy"})