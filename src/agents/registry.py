"""The agent registry — the single source of truth for known agents."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from src.agents.base import BaseAgent

__all__ = ["AgentRegistry", "DuplicateAgentError"]


class DuplicateAgentError(ValueError):
    """Raised when two agents share the same name."""


class AgentRegistry:
    """Holds agents by name and offers grouped views for the TUI."""

    def __init__(self, agents: Iterable[BaseAgent] | None = None) -> None:
        self._agents: dict[str, BaseAgent] = {}
        for agent in agents or ():
            self.register(agent)

    def register(self, agent: BaseAgent, *, replace: bool = False) -> None:
        if agent.name in self._agents and not replace:
            raise DuplicateAgentError(f"agent {agent.name!r} is already registered")
        self._agents[agent.name] = agent

    def unregister(self, name: str) -> BaseAgent | None:
        return self._agents.pop(name, None)

    def get(self, name: str) -> BaseAgent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"no agent named {name!r}") from exc

    def has(self, name: str) -> bool:
        return name in self._agents

    def all(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def names(self) -> list[str]:
        return list(self._agents)

    def by_group(self, group: str) -> list[BaseAgent]:
        return [a for a in self._agents.values() if a.group == group]

    def groups(self) -> dict[str, list[BaseAgent]]:
        """All agents bucketed by group, preserving registration order."""
        result: dict[str, list[BaseAgent]] = {}
        for agent in self._agents.values():
            result.setdefault(agent.group, []).append(agent)
        return result

    def snapshot(self) -> list[dict[str, object]]:
        """A JSON-friendly description of every agent (metadata + live stats)."""
        return [agent.info() for agent in self._agents.values()]

    # -- dunders -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    def __iter__(self) -> Iterator[BaseAgent]:
        return iter(self._agents.values())

    @classmethod
    def from_agents(cls, agents: Sequence[BaseAgent]) -> AgentRegistry:
        return cls(agents)
