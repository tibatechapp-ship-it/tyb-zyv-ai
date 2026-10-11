"""Sub-agents — focused, tool-restricted child agents.

A parent agent can hand one subtask to a *sub-agent*: a lightweight persona with
only the tools the parent already holds (never a wider set), an optional
router-chosen model, and a hard **depth limit** so spawning can never recurse
forever. :class:`SubAgentRunner` is the factory that enforces all of that and
returns a normal :class:`~src.core.types.AgentResult` the parent can consume.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agents.base import BaseAgent
from src.core.backend import LLMBackend
from src.core.types import AgentResult

if TYPE_CHECKING:
    from src.core.model_router import ModelRouter

__all__ = ["SubAgent", "SubAgentRunner", "SubAgentSpec"]


class SubAgent(BaseAgent):
    """A lightweight child agent, restricted to a subset of the parent's tools."""

    group = "sub"

    def __init__(
        self,
        backend: LLMBackend,
        *,
        role: str,
        tools: Sequence[str] = (),
        model: str | None = None,
        parent: BaseAgent | None = None,
        depth: int = 1,
    ) -> None:
        super().__init__(backend, model=model)
        self.name = role
        self.description = f"Sub-agent ({role})."
        self.tools = tuple(tools)
        self.permissions = frozenset(self.tools)
        self.parent = parent
        self.depth = depth
        self.system_prompt = (
            f"You are a focused sub-agent with role {role!r}, spawned to complete "
            "one subtask for a parent agent. Stay strictly on that subtask, be "
            "concise, and return a single self-contained result the parent can use "
            "directly — no preamble.\n"
            f"أنت وكيل فرعي مركّز بدور {role!r}: أنجز المهمة الفرعية وحدها وأرجع "
            "نتيجة واحدة جاهزة للاستخدام."
        )


@dataclass(frozen=True, slots=True)
class SubAgentSpec:
    """A declarative request to spawn one sub-agent."""

    role: str
    task: str
    tools: tuple[str, ...] | None = None
    model: str | None = None
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this spec."""
        return {
            "role": self.role,
            "task": self.task,
            "tools": None if self.tools is None else list(self.tools),
            "model": self.model,
            "context": self.context,
        }


class SubAgentRunner:
    """Spawns tool-restricted sub-agents under a parent, with a depth guard."""

    def __init__(
        self,
        backend: LLMBackend,
        *,
        router: ModelRouter | None = None,
        max_depth: int = 2,
    ) -> None:
        self._backend = backend
        self._router = router
        self._max_depth = max_depth

    def spawn(
        self,
        parent: BaseAgent,
        *,
        role: str,
        task: str,
        tools: Sequence[str] | None = None,
        model: str | None = None,
        context: str = "",
    ) -> AgentResult:
        """Run one sub-agent for ``task`` under ``parent``.

        The sub-agent may use only tools the parent holds; requesting anything
        else raises ``ValueError``. Spawning deeper than ``max_depth`` returns a
        failed :class:`AgentResult` instead of recursing. When no ``model`` is
        given and a router is configured, the router picks one for the task.
        """
        depth = (parent.depth if isinstance(parent, SubAgent) else 0) + 1
        if depth > self._max_depth:
            return AgentResult(
                agent=role,
                ok=False,
                error=f"sub-agent depth {depth} exceeds max_depth {self._max_depth}",
            )
        sub_tools = tuple(parent.tools) if tools is None else tuple(tools)
        disallowed = sorted(set(sub_tools) - set(parent.tools))
        if disallowed:
            raise ValueError(
                f"sub-agent {role!r} requested tools the parent does not hold: {disallowed}"
            )
        resolved_model = model
        if resolved_model is None and self._router is not None:
            resolved_model = self._router.route(task).model.name
        sub = SubAgent(
            self._backend,
            role=role,
            tools=sub_tools,
            model=resolved_model,
            parent=parent,
            depth=depth,
        )
        return sub.run(task, context=context)

    def run_many(self, parent: BaseAgent, specs: Sequence[SubAgentSpec]) -> list[AgentResult]:
        """Spawn one sub-agent per spec, sequentially, and collect their results."""
        return [self(parent, spec) for spec in specs]

    def __call__(self, parent: BaseAgent, spec: SubAgentSpec) -> AgentResult:
        return self.spawn(
            parent,
            role=spec.role,
            task=spec.task,
            tools=spec.tools,
            model=spec.model,
            context=spec.context,
        )

