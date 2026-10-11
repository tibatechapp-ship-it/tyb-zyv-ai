"""Pure command parsing for the TUI input line (fully unit-testable).

Grammar::

    :command [args]     → a UI command, e.g. :agents, :quit
    @agent task text    → run a specific agent
    anything else       → run the default agent
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ParsedCommand", "parse_command", "KNOWN_COMMANDS"]

KNOWN_COMMANDS = frozenset({"agents", "tasks", "monitor", "logs", "lessons", "help", "quit"})


@dataclass(slots=True)
class ParsedCommand:
    """Result of parsing one input line."""

    kind: str  # "empty" | "command" | "agent" | "task"
    name: str = ""
    text: str = ""

    @property
    def is_empty(self) -> bool:
        return self.kind == "empty"


def parse_command(raw: str) -> ParsedCommand:
    """Turn a raw input line into a :class:`ParsedCommand` (never raises)."""
    line = raw.strip()
    if not line:
        return ParsedCommand("empty")

    if line.startswith(":"):
        head, _, rest = line[1:].partition(" ")
        return ParsedCommand("command", name=head.strip().lower(), text=rest.strip())

    if line.startswith("@"):
        head, _, rest = line[1:].partition(" ")
        return ParsedCommand("agent", name=head.strip(), text=rest.strip())

    return ParsedCommand("task", text=line)
