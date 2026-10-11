"""The agent layer: base class, registry, state, metrics and messaging."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.agents.communication import AgentCommunication, Envelope
from src.agents.metrics import AgentMetrics
from src.agents.registry import AgentRegistry, DuplicateAgentError
from src.agents.state import AgentState, AgentStatus

__all__ = [
    "BaseAgent",
    "AgentRegistry",
    "DuplicateAgentError",
    "AgentState",
    "AgentStatus",
    "AgentMetrics",
    "AgentCommunication",
    "Envelope",
]
