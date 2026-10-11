"""Coordination patterns for multi-agent workflows."""

from __future__ import annotations

from src.patterns.blackboard import Blackboard
from src.patterns.delegation import Delegation
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline
from src.patterns.supervisor import Supervisor, SupervisorReport

__all__ = [
    "Blackboard",
    "Delegation",
    "Fanout",
    "Pipeline",
    "Supervisor",
    "SupervisorReport",
]
