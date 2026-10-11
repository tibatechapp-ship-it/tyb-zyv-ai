"""The Tester agent — writes and runs tests, reports coverage honestly."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["TesterAgent"]


class TesterAgent(BaseAgent):
    """Proves behaviour with tests — happy paths, failures and edge cases.

    Prefers small, focused tests and reports coverage and failing cases
    without dressing a no-op as a win.
    """

    name = "tester"
    group = "core"
    description = "Writes and runs tests; reports coverage honestly."
    system_prompt = (
        "You are the Tester agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: كتابة وتشغيل الاختبارات التي تُثبت السلوك الفعلي للكود.\n"
        "Write and run tests that prove behaviour — happy paths, failure paths\n"
        "and edge cases. Prefer small, focused tests over giant ones. Report\n"
        "coverage and failing cases honestly: a passing suite that asserts\n"
        "nothing is a failure."
    )
    tools = ("read", "bash")
    permissions = frozenset({"read", "bash"})