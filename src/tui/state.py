"""In-memory state shared by the TUI screens (tasks + structured logs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.agents import all_agents
from src.agents.registry import AgentRegistry
from src.core.backend import LLMBackend
from src.core.types import AgentResult
from src.learning import LessonStore
from src.security import SecurityOrchestrator

__all__ = ["TaskRecord", "TuiState"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


@dataclass
class TaskRecord:
    """One unit of work shown in the Tasks view."""

    id: int
    agent: str
    task: str
    status: str = "queued"  # queued | running | done | failed
    output: str = ""
    error: str | None = None
    duration_s: float = 0.0
    created_at: str = field(default_factory=_now)


class TuiState:
    """Holds everything the views render: registry, tasks, logs, counters."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        backend: LLMBackend | None = None,
        *,
        lesson_store: LessonStore | None = None,
        security_orchestrator: SecurityOrchestrator | None = None,
    ) -> None:
        # No registry given but a backend is: auto-register the 20 built-ins.
        if registry is None:
            registry = AgentRegistry(all_agents(backend)) if backend is not None else AgentRegistry()
        self.registry = registry
        self.backend = backend
        # The learned principles shown in the Lessons view. Empty by default;
        # callers can hand in a pre-populated store (e.g. loaded from SQLite).
        self.lesson_store = lesson_store if lesson_store is not None else LessonStore()
        # The security scanner driving the Security view (offline static scan).
        self.security_orchestrator = (
            security_orchestrator if security_orchestrator is not None else SecurityOrchestrator()
        )
        self.tasks: list[TaskRecord] = []
        self.logs: list[dict[str, str]] = []
        self.llm_calls = 0
        self._next_id = 1

    # -- logs --------------------------------------------------------------
    def add_log(self, level: str, message: str) -> None:
        self.logs.append({"time": _now(), "level": level.upper(), "message": message})

    # -- tasks -------------------------------------------------------------
    def new_task(self, agent: str, task: str) -> TaskRecord:
        record = TaskRecord(id=self._next_id, agent=agent, task=task)
        self._next_id += 1
        self.tasks.append(record)
        return record

    def run_agent(self, name: str, task: str) -> AgentResult:
        """Run a registered agent, recording the task + logs + LLM counter."""
        agent = self.registry.get(name)
        record = self.new_task(name, task)
        record.status = "running"
        self.logs.append({"time": _now(), "level": "INFO", "message": f"{name}: started"})
        result = agent.run(task)
        self.llm_calls += 1
        record.duration_s = result.duration_s
        if result.ok:
            record.status = "done"
            record.output = result.output
            self.add_log("INFO", f"{name}: done in {result.duration_s:.2f}s")
        else:
            record.status = "failed"
            record.error = result.error
            self.add_log("ERROR", f"{name}: {result.error}")
        return result

    # -- views -------------------------------------------------------------
    def active_tasks(self) -> list[TaskRecord]:
        return [t for t in self.tasks if t.status in ("queued", "running")]

    def completed_tasks(self) -> list[TaskRecord]:
        return [t for t in self.tasks if t.status == "done"]

    def failed_tasks(self) -> list[TaskRecord]:
        return [t for t in self.tasks if t.status == "failed"]

    def filtered_logs(self, level: str | None = None, query: str = "") -> list[dict[str, str]]:
        rows = self.logs
        if level:
            rows = [r for r in rows if r["level"] == level.upper()]
        if query:
            needle = query.lower()
            rows = [r for r in rows if needle in r["message"].lower()]
        return rows
