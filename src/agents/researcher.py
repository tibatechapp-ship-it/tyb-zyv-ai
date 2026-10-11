"""The Researcher agent — gathers current, source-backed information."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["ResearcherAgent"]


class ResearcherAgent(BaseAgent):
    """Finds and triangulates evidence from primary sources.

    Prefers official docs, code and issue trackers over blogspam, and
    explicitly marks anything that could not be verified.
    """

    name = "researcher"
    group = "planning"
    description = "Gathers current, source-backed information."
    system_prompt = (
        "You are the Researcher agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: جمع معلومات حديثة وموثوقة — مع ذكر المصادر.\n"
        "Gather current, source-backed information; prefer primary sources\n"
        "such as official documentation, code and issue trackers, and quote\n"
        "them. Triangulate with at least two independent sources before\n"
        "treating something as fact, and say explicitly what is unverified."
    )
    tools = ("read", "search", "web")
    permissions = frozenset({"read"})