"""The agent registry — the single source of truth for known agents."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Sequence

from src.agents.base import BaseAgent

__all__ = ["AgentRegistry", "DuplicateAgentError"]


class DuplicateAgentError(ValueError):
    """Raised when two agents share the same name."""


class AgentRegistry:
    """Holds agents by name and offers grouped views for the TUI.

    Registration is a check-then-act (``name in dict`` then ``dict[name] = ...``),
    so concurrent ``register`` calls could otherwise both pass the check and
    double-insert, or race with a reader mid-iteration. A single instance
    :class:`~threading.Lock` serialises every mutator and reader, making the
    registry safe under the same fan-out that drives the agents.
    """

    def __init__(self, agents: Iterable[BaseAgent] | None = None) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._lock = threading.Lock()
        for agent in agents or ():
            self.register(agent)

    def register(self, agent: BaseAgent, *, replace: bool = False) -> None:
        with self._lock:
            if agent.name in self._agents and not replace:
                raise DuplicateAgentError(f"agent {agent.name!r} is already registered")
            self._agents[agent.name] = agent

    def unregister(self, name: str) -> BaseAgent | None:
        with self._lock:
            return self._agents.pop(name, None)

    def get(self, name: str) -> BaseAgent:
        with self._lock:
            try:
                return self._agents[name]
            except KeyError as exc:
                raise KeyError(f"no agent named {name!r}") from exc

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._agents

    def all(self) -> list[BaseAgent]:
        with self._lock:
            return list(self._agents.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self._agents)

    def by_group(self, group: str) -> list[BaseAgent]:
        with self._lock:
            return [a for a in self._agents.values() if a.group == group]

    def groups(self) -> dict[str, list[BaseAgent]]:
        """All agents bucketed by group, preserving registration order."""
        with self._lock:
            result: dict[str, list[BaseAgent]] = {}
            for agent in self._agents.values():
                result.setdefault(agent.group, []).append(agent)
            return result

    def snapshot(self) -> list[dict[str, object]]:
        """A JSON-friendly description of every agent (metadata + live stats)."""
        with self._lock:
            return [agent.info() for agent in self._agents.values()]

    # -- dunders -----------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._agents

    def __iter__(self) -> Iterator[BaseAgent]:
        # Iterate a stable snapshot so a concurrent register/unregister cannot
        # raise "dictionary changed size during iteration".
        with self._lock:
            return iter(list(self._agents.values()))

    @classmethod
    def from_agents(cls, agents: Sequence[BaseAgent]) -> AgentRegistry:
        return cls(agents)
