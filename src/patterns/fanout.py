"""Fan-out pattern — the same task broadcast to many agents in parallel."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from src.agents.base import BaseAgent
from src.core.types import AgentResult

__all__ = ["Fanout"]


class Fanout:
    """Runs every agent on the same task concurrently and gathers the results."""

    def __init__(self, agents: Iterable[BaseAgent], *, max_workers: int | None = None) -> None:
        self._agents = list(agents)
        self._max_workers = max_workers

    def run(self, task: str, *, context: str = "") -> list[AgentResult]:
        """Return one result per agent, in registration order."""
        if not self._agents:
            return []
        workers = self._max_workers or min(len(self._agents), 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda a: a.run(task, context=context), self._agents))
