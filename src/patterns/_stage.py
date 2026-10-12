"""Internal stage runner shared by the coordination patterns.

A *stage* is one agent running one task inside a pattern. With no
:class:`~src.tools.executor.ToolPolicy` it is a plain
:meth:`~src.agents.base.BaseAgent.run` — the historical, single-call behaviour.
With a policy it is the bounded tool-execution loop from
:class:`~src.tools.executor.LocalToolExecutor`, and the resulting tool trace is
kept alongside the agent result.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.base import BaseAgent
from src.core.types import AgentResult
from src.tools.executor import ToolPolicy, ToolRun

__all__ = ["StageOutcome", "run_stage"]


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """One agent's result plus, when tools ran, its execution trace."""

    result: AgentResult
    tool_run: ToolRun | None = None


def run_stage(
    agent: BaseAgent,
    task: str,
    *,
    context: str = "",
    tools: ToolPolicy | None = None,
) -> StageOutcome:
    """Run one stage: a single call, or the bounded tool loop when ``tools`` is set."""
    if tools is None:
        return StageOutcome(agent.run(task, context=context))
    tool_run = agent.run_with_tools(
        task,
        context=context,
        root=tools.root,
        max_steps=tools.max_steps,
        max_calls_per_step=tools.max_calls_per_step,
    )
    return StageOutcome(tool_run.result, tool_run)
