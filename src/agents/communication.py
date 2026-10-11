"""A tiny in-process message bus so agents can hand work to each other."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["Envelope", "AgentCommunication"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Envelope:
    """One message on the bus. ``recipient=None`` means a broadcast."""

    sender: str
    subject: str
    body: str
    recipient: str | None = None
    created_at: str = field(default_factory=_now)


class AgentCommunication:
    """Routes :class:`Envelope` instances between agents (and keeps a log)."""

    def __init__(self) -> None:
        self._inboxes: dict[str, list[Envelope]] = {}
        self._log: list[Envelope] = []

    def send(self, envelope: Envelope) -> None:
        """Deliver to a specific recipient (or broadcast when recipient is None)."""
        if envelope.recipient is None:
            self.broadcast(envelope)
            return
        self._inboxes.setdefault(envelope.recipient, []).append(envelope)
        self._log.append(envelope)

    def broadcast(self, envelope: Envelope) -> None:
        """Deliver one copy to every known recipient. Sender is excluded."""
        for name, inbox in self._inboxes.items():
            if name != envelope.sender:
                inbox.append(envelope)
        self._log.append(envelope)

    def inbox(self, agent_name: str) -> list[Envelope]:
        """Peek at an agent's pending messages (does not drain)."""
        return list(self._inboxes.get(agent_name, []))

    def drain(self, agent_name: str) -> list[Envelope]:
        """Return and clear an agent's pending messages."""
        return self._inboxes.pop(agent_name, [])

    def subscribe(self, agent_name: str) -> None:
        """Ensure an inbox exists (so broadcasts reach the agent)."""
        self._inboxes.setdefault(agent_name, [])

    def log(self) -> list[Envelope]:
        """Every envelope ever sent, in order."""
        return list(self._log)
