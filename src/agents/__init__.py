"""The agent layer: base class, registry, state, metrics, messaging and the 20 built-in agents."""

from __future__ import annotations

from src.agents.analyst import AnalystAgent
from src.agents.architect import ArchitectAgent
from src.agents.base import BaseAgent
from src.agents.coder import CoderAgent
from src.agents.communication import AgentCommunication, Envelope
from src.agents.compliance import ComplianceAgent
from src.agents.debugger import DebuggerAgent
from src.agents.designer import DesignerAgent
from src.agents.devops import DevOpsAgent
from src.agents.documentation import DocumentationAgent
from src.agents.estimator import EstimatorAgent
from src.agents.metrics import AgentMetrics
from src.agents.monitor import MonitorAgent
from src.agents.orchestrator import OrchestratorAgent
from src.agents.performance import PerformanceAgent
from src.agents.planner import PlannerAgent
from src.agents.recovery import RecoveryAgent
from src.agents.refactor import RefactorAgent
from src.agents.registry import AgentRegistry, DuplicateAgentError
from src.agents.reporter import ReporterAgent
from src.agents.researcher import ResearcherAgent
from src.agents.reviewer import ReviewerAgent
from src.agents.security_auditor import SecurityAuditorAgent
from src.agents.state import AgentState, AgentStatus
from src.agents.sub_agent import SubAgent, SubAgentRunner, SubAgentSpec
from src.agents.tester import TesterAgent
from src.core.backend import LLMBackend

__all__ = [
    "BaseAgent",
    "AgentRegistry",
    "DuplicateAgentError",
    "AgentState",
    "AgentStatus",
    "AgentMetrics",
    "AgentCommunication",
    "Envelope",
    "SubAgent",
    "SubAgentRunner",
    "SubAgentSpec",
    "AGENT_GROUPS",
    "AGENT_CLASSES",
    "all_agents",
    # -- the 20 built-in agents -------------------------------------------
    "ArchitectAgent",
    "CoderAgent",
    "ReviewerAgent",
    "TesterAgent",
    "DebuggerAgent",
    "PlannerAgent",
    "AnalystAgent",
    "ResearcherAgent",
    "DesignerAgent",
    "EstimatorAgent",
    "SecurityAuditorAgent",
    "PerformanceAgent",
    "DocumentationAgent",
    "RefactorAgent",
    "ComplianceAgent",
    "OrchestratorAgent",
    "DevOpsAgent",
    "MonitorAgent",
    "RecoveryAgent",
    "ReporterAgent",
]

#: The four group buckets every built-in agent belongs to.
AGENT_GROUPS: tuple[str, ...] = ("core", "planning", "quality", "ops")

#: All built-in agent classes, in fixed display order (group by group).
AGENT_CLASSES: tuple[type[BaseAgent], ...] = (
    # core
    ArchitectAgent,
    CoderAgent,
    ReviewerAgent,
    TesterAgent,
    DebuggerAgent,
    # planning
    PlannerAgent,
    AnalystAgent,
    ResearcherAgent,
    DesignerAgent,
    EstimatorAgent,
    # quality
    SecurityAuditorAgent,
    PerformanceAgent,
    DocumentationAgent,
    RefactorAgent,
    ComplianceAgent,
    # ops
    OrchestratorAgent,
    DevOpsAgent,
    MonitorAgent,
    RecoveryAgent,
    ReporterAgent,
)


def all_agents(backend: LLMBackend, *, model: str | None = None) -> list[BaseAgent]:
    """Instantiate every built-in agent with ``backend``.

    Returns one instance per agent class, in stable group order — the
    recommended way to build the full 20-agent registry (the TUI, the
    registry and the tests all rely on it).
    """
    return [cls(backend, model=model) for cls in AGENT_CLASSES]