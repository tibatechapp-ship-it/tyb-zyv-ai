"""Local tool execution — turning declared agent tools into real actions.

Until now an agent's ``tools``/``permissions`` metadata was purely declarative:
:meth:`~src.agents.base.BaseAgent.run` made exactly **one** backend call and
nothing ever executed a tool. This module closes that gap for the tools that are
safe to run *inside the framework* — read-only filesystem introspection
(``read`` / ``list`` / ``grep``). Anything that mutates the system or runs a
shell (``write`` / ``edit`` / ``bash``) is **deliberately not implemented here**:
in production those run inside the ZYVO server, behind its own permission gate.

The execution model is a small, deterministic ReAct loop over a plain-text
protocol: the model emits one or more ``<tool-call name="…" args='{…}'/>``
markers, the executor runs them, and the results are fed back as a
``<tool-result>`` turn until the model answers without a call (or a step budget
runs out). Everything is offline and testable — no network, no third-party deps.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.types import AgentResult, Message

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from src.agents.base import BaseAgent

__all__ = [
    "LocalToolExecutor",
    "SAFE_TOOL_NAMES",
    "ToolCall",
    "ToolOutcome",
    "ToolPolicy",
    "ToolRun",
    "parse_tool_calls",
]

#: The tools the framework itself is allowed to execute (read-only by design).
#: ``write``/``edit``/``bash`` are intentionally absent: they run server-side.
SAFE_TOOL_NAMES: frozenset[str] = frozenset({"read", "list", "grep"})

#: Directories never descended into by ``grep`` / ``list``.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules", ".venv"}
)

#: Matches ``<tool-call name="read" args='{"path": "x"}'/>`` (single or double quotes).
_TOOL_CALL_RE = re.compile(
    r"<tool-call\s+name=[\"'](?P<name>[A-Za-z_][\w.\-]*)[\"']\s+args=[\"'](?P<args>.*?)[\"']\s*/?>",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One parsed request from the model to run a named tool."""

    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view."""
        return {"name": self.name, "arguments": self.arguments}


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """The result of running one :class:`ToolCall` (never raises)."""

    name: str
    ok: bool
    output: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view."""
        return {"name": self.name, "ok": self.ok, "output": self.output, "error": self.error}

    def as_prompt(self) -> str:
        """Render this outcome as a ``<tool-result>`` block for the next turn."""
        body = self.output if self.ok else f"ERROR: {self.error}"
        return f'<tool-result name="{self.name}">\n{body}\n</tool-result>'


@dataclass(frozen=True, slots=True)
class ToolRun:
    """The outcome of a tool-using agent run: the answer plus the tool trace."""

    result: AgentResult
    outcomes: tuple[ToolOutcome, ...]
    steps: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view."""
        return {
            "result": self.result.as_dict(),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "steps": self.steps,
        }


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Configuration for running agents with real tool execution.

    Handed to a coordination pattern (or passed straight to
    :meth:`BaseAgent.run_with_tools`) to turn each stage from a single backend
    call into the bounded ReAct loop in :class:`LocalToolExecutor`. ``root``
    confines every read-only tool to one workspace directory.
    """

    root: str | Path | None = None
    max_steps: int = 5
    max_calls_per_step: int = 5


def parse_tool_calls(text: str) -> list[ToolCall]:
    """Extract every ``<tool-call …/>`` marker from ``text``, in order.

    Tolerant by design: a marker whose ``args`` are not valid JSON still yields a
    :class:`ToolCall` with empty arguments (the tool then reports the missing
    argument itself) so a malformed call can never crash the loop.
    """
    calls: list[ToolCall] = []
    for match in _TOOL_CALL_RE.finditer(text):
        raw_args = match.group("args")
        try:
            parsed = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        arguments = parsed if isinstance(parsed, dict) else {}
        calls.append(ToolCall(name=match.group("name"), arguments=arguments))
    return calls


class LocalToolExecutor:
    """Runs the safe, read-only tools against a confined workspace root.

    Every path argument is resolved and must stay **inside** the root, so a model
    can never read ``/etc/passwd`` or escape with ``../../`` — the same
    confinement the security scanner enforces, but for tool calls.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        max_output_chars: int = 8_000,
    ) -> None:
        self.root = Path(root or Path.cwd()).resolve()
        self.max_output_chars = max_output_chars

    # -- capability surface ------------------------------------------------
    @property
    def available(self) -> frozenset[str]:
        """The tools this executor can actually run."""
        return SAFE_TOOL_NAMES

    def available_for(self, tools: Iterable[str]) -> frozenset[str]:
        """The subset of ``tools`` that are both declared and executable here."""
        return frozenset(tools) & SAFE_TOOL_NAMES

    # -- dispatch ----------------------------------------------------------
    def execute(
        self, call: ToolCall, *, allowed_tools: frozenset[str] | None = None
    ) -> ToolOutcome:
        """Run ``call`` and return an outcome (never raises).

        ``allowed_tools`` (normally the agent's own ``tools`` tuple) narrows what
        may run; a call outside it is refused with a clear error.
        """
        if call.name not in SAFE_TOOL_NAMES:
            return ToolOutcome(
                name=call.name,
                ok=False,
                error=(
                    f"tool {call.name!r} is not executable in-framework; it runs "
                    "inside the ZYVO server behind its permission gate"
                ),
            )
        if allowed_tools is not None and call.name not in allowed_tools:
            return ToolOutcome(
                name=call.name,
                ok=False,
                error=f"agent is not permitted to use tool {call.name!r}",
            )
        handler = {
            "read": self._read,
            "list": self._list,
            "grep": self._grep,
        }[call.name]
        try:
            return ToolOutcome(name=call.name, ok=True, output=handler(call.arguments))
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            return ToolOutcome(name=call.name, ok=False, error=str(exc))
        except PermissionError as exc:
            return ToolOutcome(name=call.name, ok=False, error=str(exc))
        except re.error as exc:  # bad regex from the model
            return ToolOutcome(name=call.name, ok=False, error=f"invalid pattern: {exc}")
        except (ValueError, TypeError, OSError) as exc:
            return ToolOutcome(name=call.name, ok=False, error=f"{type(exc).__name__}: {exc}")

    # -- individual tools --------------------------------------------------
    def _resolve(self, raw: Any) -> Path:
        """Resolve ``raw`` and confine it to the workspace root."""
        if not isinstance(raw, str) or not raw:
            raise ValueError("a non-empty string 'path' argument is required")
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise PermissionError(f"path {raw!r} escapes the workspace root {self.root}")
        return resolved

    def _clamp(self, text: str) -> str:
        """Truncate ``text`` to :attr:`max_output_chars` with a visible marker."""
        if len(text) <= self.max_output_chars:
            return text
        return text[: self.max_output_chars] + f"\n… [truncated at {self.max_output_chars} chars]"

    def _read(self, arguments: dict[str, Any]) -> str:
        """Return a text file's contents (UTF-8, decode-safe)."""
        path = self._resolve(arguments.get("path"))
        if path.is_dir():
            raise IsADirectoryError(f"{path} is a directory — use the 'list' tool")
        data = path.read_bytes()
        return self._clamp(data.decode("utf-8", "replace"))

    def _list(self, arguments: dict[str, Any]) -> str:
        """Return a sorted listing of a directory (directories marked with ``/``)."""
        path = self._resolve(arguments.get("path", "."))
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")
        lines: list[str] = []
        for entry in sorted(path.iterdir(), key=lambda item: item.name):
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                try:
                    lines.append(f"{entry.name}  ({entry.stat().st_size} bytes)")
                except OSError:  # pragma: no cover - unreadable entry
                    lines.append(entry.name)
        return self._clamp("\n".join(lines)) if lines else "(empty directory)"

    def _grep(self, arguments: dict[str, Any]) -> str:
        """Return ``path:line: text`` for files under a path matching a regex."""
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("a non-empty string 'pattern' argument is required")
        regex = re.compile(pattern)
        max_matches = _positive_int(arguments.get("max_matches"), default=200)
        start = self._resolve(arguments.get("path", "."))
        matches: list[str] = []
        for file in _walk_text_files(start):
            try:
                content = file.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{file}:{lineno}: {line.strip()}")
                    if len(matches) >= max_matches:
                        return self._clamp("\n".join(matches))
        return self._clamp("\n".join(matches)) if matches else "(no matches)"

    # -- the ReAct loop ----------------------------------------------------
    def run(
        self,
        agent: BaseAgent,
        task: str,
        *,
        context: str = "",
        lessons: str = "",
        max_steps: int = 5,
        max_calls_per_step: int = 5,
    ) -> ToolRun:
        """Run ``agent`` on ``task`` with real tool execution, step-bounded.

        The agent answers; if its answer contains tool calls they run and their
        results are fed back as a new turn; the loop repeats until the model
        answers without a call or ``max_steps`` is reached. Backend failures are
        captured (never raised) exactly like :meth:`BaseAgent.run`.
        """
        agent.state.mark_busy(task)
        started = time.perf_counter()
        messages = agent.build_messages(task, context)
        if lessons:
            messages = agent._with_lessons(messages, lessons)  # same-package contract
        outcomes: list[ToolOutcome] = []
        allowed = frozenset(agent.tools)
        steps = 0
        output = ""
        try:
            while steps < max_steps:
                steps += 1
                output = agent.backend.generate(messages, model=agent.model)
                if not isinstance(output, str):
                    raise TypeError(f"backend returned {type(output).__name__!r}, expected str")
                calls = parse_tool_calls(output)
                if not calls:
                    break
                messages.append(Message("assistant", output))
                for call in calls[:max_calls_per_step]:
                    outcome = self.execute(call, allowed_tools=allowed)
                    outcomes.append(outcome)
                    messages.append(Message("user", outcome.as_prompt()))
        except Exception as exc:  # noqa: BLE001 - a tool run must never crash callers
            elapsed = time.perf_counter() - started
            agent.metrics.record(elapsed, ok=False)
            agent.state.mark_error(str(exc))
            return ToolRun(
                result=AgentResult(agent=agent.name, ok=False, error=str(exc), duration_s=elapsed),
                outcomes=tuple(outcomes),
                steps=steps,
            )
        elapsed = time.perf_counter() - started
        agent.metrics.record(elapsed, ok=True)
        agent.state.mark_idle()
        return ToolRun(
            result=AgentResult(agent=agent.name, ok=True, output=output, duration_s=elapsed),
            outcomes=tuple(outcomes),
            steps=steps,
        )


def _positive_int(raw: Any, *, default: int) -> int:
    """Coerce ``raw`` to a positive int, or ``default`` when it is not usable."""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int) and raw > 0:
        return raw
    return default


def _walk_text_files(start: Path) -> Iterator[Path]:
    """Yield plausible text files under ``start`` (skipping known junk dirs)."""
    if start.is_file():
        yield start
        return
    for candidate in sorted(start.rglob("*")):
        if not candidate.is_file():
            continue
        if _SKIP_DIRS.intersection(candidate.parts):
            continue
        try:
            if candidate.stat().st_size > 1_000_000:
                continue
        except OSError:  # pragma: no cover - unreadable entry
            continue
        yield candidate
