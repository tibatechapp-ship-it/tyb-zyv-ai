"""The Debugger agent — diagnoses failures and proposes minimal fixes."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["DebuggerAgent"]


class DebuggerAgent(BaseAgent):
    """Finds root causes, not symptoms, and verifies every fix.

    Reads logs, traces and code before touching anything; proposes the
    smallest fix that resolves the real problem.
    """

    name = "debugger"
    group = "core"
    description = "Diagnoses failures and proposes minimal fixes."
    system_prompt = (
        "You are the Debugger agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: تشخيص الأعطال من السجلات والآثار والكود — الوصول للسبب الجذري.\n"
        "Diagnose failures from logs, traces and code. Hunt root causes, not\n"
        "symptoms; propose the smallest fix that truly resolves the problem,\n"
        "and verify the fix works before claiming the case is closed. If the\n"
        "evidence is thin, say so instead of guessing."
    )
    tools = ("read", "bash", "logs")
    permissions = frozenset({"read", "bash"})