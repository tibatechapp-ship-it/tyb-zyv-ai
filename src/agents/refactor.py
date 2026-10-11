"""The Refactor agent — restructures code without changing behaviour."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["RefactorAgent"]


class RefactorAgent(BaseAgent):
    """Improves clarity with minimal diffs and green tests.

    Moves in small, verified steps: restructure, test, and only then move
    on to the next piece.
    """

    name = "refactor"
    group = "quality"
    description = "Restructures code without changing behaviour."
    system_prompt = (
        "You are the Refactor agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: إعادة هيكلة الكود لتحسين الوضوح دون تغيير سلوكه.\n"
        "Restructure code to improve clarity and maintainability without\n"
        "changing behaviour. Keep diffs minimal and focused, work in small\n"
        "verified steps, and keep the test suite green after every change."
    )
    tools = ("read", "write", "bash")
    permissions = frozenset({"read", "write", "bash"})