"""The base class every agent inherits from.

An "agent" in this project is a specialised *persona* over the existing ZYVO
model: a system prompt plus a metadata envelope (group, description, tools,
permissions). It never owns a model key — it drives whatever backend it is
handed, which is always the local ZYVO HTTP API in production.
"""

from __future__ import annotations

import time
from abc import ABC
from collections.abc import Sequence

from src.agents.metrics import AgentMetrics
from src.agents.state import AgentState
from src.core.backend import LLMBackend
from src.core.types import AgentResult, Message

__all__ = ["BaseAgent"]


class BaseAgent(ABC):
    """Base implementation of a specialised agent.

    Subclasses normally only set the class-level metadata (``name``, ``group``,
    ``description``, ``system_prompt``, ``tools``, ``permissions``); the run
    loop itself lives here so every agent behaves identically and is testable.
    """

    #: Unique, human-readable identifier (e.g. ``"architect"``).
    name: str = "base"
    #: One of ``core`` / ``planning`` / ``quality`` / ``ops``.
    group: str = "core"
    #: One-line description shown in the TUI.
    description: str = "Base agent."
    #: The persona handed to the model as the system prompt.
    system_prompt: str = "You are a helpful assistant."
    #: Names of capabilities the agent may use.
    tools: tuple[str, ...] = ()
    #: Permission flags enforced by the (future) PolicyGate.
    permissions: frozenset[str] = frozenset()

    def __init__(self, backend: LLMBackend, *, model: str | None = None) -> None:
        self.backend = backend
        self.model = model
        self.state = AgentState()
        self.metrics = AgentMetrics()

    # -- prompt construction ----------------------------------------------
    def build_messages(self, task: str, context: str = "") -> list[Message]:
        """Build the conversation sent to the backend for ``task``."""
        user = task if not context else f"{task}\n\n--- context ---\n{context}"
        return [
            Message("system", self.system_prompt),
            Message("user", user),
        ]

    # -- execution ---------------------------------------------------------
    def run(self, task: str, *, context: str = "") -> AgentResult:
        """Execute ``task`` and return a structured result (never raises)."""
        self.state.mark_busy(task)
        started = time.perf_counter()
        try:
            messages = self.build_messages(task, context)
            output = self.backend.generate(messages, model=self.model)
            # A backend that violates the str contract (Byzantine failure) must
            # fail loudly here, not smuggle a non-str into AgentResult.output.
            if not isinstance(output, str):
                raise TypeError(
                    f"backend returned {type(output).__name__!r}, expected str"
                )
        except Exception as exc:  # noqa: BLE001 - agents must never crash callers
            elapsed = time.perf_counter() - started
            self.metrics.record(elapsed, ok=False)
            self.state.mark_error(str(exc))
            return AgentResult(
                agent=self.name,
                ok=False,
                error=str(exc),
                duration_s=elapsed,
            )
        elapsed = time.perf_counter() - started
        self.metrics.record(elapsed, ok=True)
        self.state.mark_idle()
        return AgentResult(agent=self.name, ok=True, output=output, duration_s=elapsed)

    def run_many(self, tasks: Sequence[str]) -> list[AgentResult]:
        """Run several tasks sequentially on this agent."""
        return [self.run(task) for task in tasks]

    def info(self) -> dict[str, object]:
        """Static metadata + live state/metrics, for the TUI."""
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "tools": list(self.tools),
            "permissions": sorted(self.permissions),
            "state": self.state.to_dict(),
            "metrics": self.metrics.to_dict(),
        }

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} group={self.group!r}>"
