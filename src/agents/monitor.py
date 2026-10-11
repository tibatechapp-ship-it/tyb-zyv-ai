"""The Monitor agent — watches health signals and alerts on anomalies."""

from __future__ import annotations

from src.agents.base import BaseAgent

__all__ = ["MonitorAgent"]


class MonitorAgent(BaseAgent):
    """Reports facts with timestamps, not hunches.

    Watches live health signals — processes, resources, error rates — and
    raises the alarm before the user notices the problem.
    """

    name = "monitor"
    group = "ops"
    description = "Watches health signals and alerts on anomalies."
    system_prompt = (
        "You are the Monitor agent in the tyb-zyv-ai multi-agent team.\n"
        "دورك: مراقبة الإشارات الحية والتنبيه عند الشذوذ قبل أن يلاحظه المستخدم.\n"
        "Watch live health signals — processes, resources, error rates — and\n"
        "alert on anomalies before the user notices. Report facts with\n"
        "timestamps and values, never hunches, and distinguish a real\n"
        "outage from a harmless blip."
    )
    tools = ("read", "observe", "metrics", "alerts")
    permissions = frozenset({"read"})