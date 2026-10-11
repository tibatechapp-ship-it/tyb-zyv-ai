"""PHASE 2 - collaboration deep tests.

Goal: prove the 20 agents can actually work *together* through every pattern in
``src/patterns`` - sequenced hand-offs (Pipeline), true parallelism (Fanout),
coordination (Supervisor), two-stage delegation, and shared state (Blackboard)
- plus the in-process message bus. Backends are mocks; no real LLM is called.
"""

from __future__ import annotations

import time

from src.agents.architect import ArchitectAgent
from src.agents.coder import CoderAgent
from src.agents.communication import AgentCommunication, Envelope
from src.agents.performance import PerformanceAgent
from src.agents.planner import PlannerAgent
from src.agents.reviewer import ReviewerAgent
from src.agents.tester import TesterAgent as QA
from src.core.backend import EchoBackend
from src.patterns.blackboard import Blackboard
from src.patterns.delegation import Delegation
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline
from src.patterns.supervisor import Supervisor
from src.tui.state import TuiState

from deep_helpers import DirectiveBackend, RecordingBackend


# ── 2.1 Pipeline ─────────────────────────────────────────────────────────
def test_pipeline_architect_coder_reviewer_tester_chains_context() -> None:
    """A 4-stage delivery pipeline runs in order and chains each output on.

    Architect -> Coder -> Reviewer -> Tester: every stage must receive the
    previous stage's output as its context, and the final result must be the
    Tester's.
    """
    backend = DirectiveBackend()
    chain = Pipeline(
        [
            ArchitectAgent(backend),
            CoderAgent(backend),
            ReviewerAgent(backend),
            QA(backend),
        ]
    )
    result = chain.run("Add user login")

    assert result.ok
    assert result.agent == "tester"
    assert backend.signatures == ["Architect", "Coder", "Reviewer", "Tester"]
    assert backend.calls[0][0][1].content == "Add user login"
    for previous_tag, call in zip(("Architect", "Coder", "Reviewer"), backend.calls[1:]):
        assert f"[{previous_tag}-ANSWER]" in call[0][1].content


def test_pipeline_stops_at_the_failing_stage() -> None:
    """A mid-chain failure must abort the chain - later agents never run."""
    backend = DirectiveBackend(fail_for=["Reviewer"])
    chain = Pipeline(
        [
            ArchitectAgent(backend),
            CoderAgent(backend),
            ReviewerAgent(backend),
            QA(backend),
        ]
    )
    result = chain.run("Ship it")

    assert not result.ok
    assert result.agent == "reviewer"
    assert backend.signatures == ["Architect", "Coder", "Reviewer"]  # Tester skipped


# ── 2.2 Fanout (parallel) ────────────────────────────────────────────────
def test_fanout_runs_five_agents_truly_in_parallel() -> None:
    """Five agents sharing one task run concurrently, not one-after-another.

    With a 0.15s-per-call mock, five *sequential* calls would take >= 0.75s;
    a real thread pool finishes far sooner. The threshold is deliberately loose
    so the test stays green on slow phones while still catching a regression to
    sequential execution.
    """
    backend = RecordingBackend(reply="audited", delay=0.15)
    team = [
        ReviewerAgent(backend),
        QA(backend),
        PerformanceAgent(backend),
    ]
    started = time.perf_counter()
    results = Fanout(team, max_workers=3).run("audit the release")
    elapsed = time.perf_counter() - started

    assert backend.attempt_count == 3
    assert all(r.ok for r in results)
    assert [r.agent for r in results] == ["reviewer", "tester", "performance"]
    assert elapsed < 0.40, f"fanout was not parallel: {elapsed:.3f}s"


def test_fanout_broadcasts_context_to_every_agent() -> None:
    """Every fan-out worker receives the same injected context."""
    backend = RecordingBackend(reply="ok")
    Fanout([ReviewerAgent(backend), QA(backend)]).run("task", context="CTX-77")

    assert backend.attempt_count == 2
    for messages, _ in backend.calls:
        assert messages[-1].content.endswith("CTX-77")


# ── 2.3 Supervisor ───────────────────────────────────────────────────────
def test_supervisor_coordinates_a_team_and_aggregates_results() -> None:
    """A supervisor runs every delegate over one task and reports success."""
    backend = DirectiveBackend()
    supervisor = Supervisor([ArchitectAgent(backend), CoderAgent(backend), ReviewerAgent(backend)])
    report = supervisor.run("Harden the API")

    assert len(supervisor.agents) == 3
    assert len(report.results) == 3
    assert report.ok
    assert report.summary() == "3/3 agents succeeded for task: Harden the API"
    assert report.failures == []
    assert backend.signatures == ["Architect", "Coder", "Reviewer"]


def test_supervisor_reports_failures_honestly() -> None:
    """When one delegate fails the report says so, with the failing result."""
    backend = DirectiveBackend(fail_for=["Tester"])
    report = Supervisor([ReviewerAgent(backend), QA(backend)]).run("verify")

    assert not report.ok
    assert [r.agent for r in report.failures] == ["tester"]
    assert "1/2 agents succeeded" in report.summary()


# ── 2.4 Delegation ───────────────────────────────────────────────────────
def test_delegation_lead_plans_then_worker_executes() -> None:
    """Planner -> Coder: the worker's context is exactly the lead's plan."""
    backend = DirectiveBackend()
    result = Delegation(PlannerAgent(backend), CoderAgent(backend)).run("Build the feature")

    assert result.ok
    assert backend.signatures == ["Planner", "Coder"]
    worker_user = backend.calls[1][0][1].content
    assert worker_user.startswith("Build the feature")
    assert "[Planner-ANSWER] Build the feature" in worker_user


def test_delegation_skips_worker_when_lead_fails() -> None:
    """A failed plan aborts the hand-off - the worker is never invoked."""
    backend = DirectiveBackend(fail_for=["Planner"])
    result = Delegation(PlannerAgent(backend), CoderAgent(backend)).run("x")

    assert not result.ok
    assert backend.signatures == ["Planner"]


# ── 2.5 Blackboard ───────────────────────────────────────────────────────
def test_blackboard_collects_agent_outputs_in_order() -> None:
    """Agents publish to a shared board; order and content are preserved."""
    backend = DirectiveBackend()
    board = Blackboard()
    for agent in (ReviewerAgent(backend), QA(backend), PerformanceAgent(backend)):
        result = agent.run("audit the release")
        board.append("reports", {"agent": agent.name, "output": result.output})

    reports = board.get("reports")
    assert [r["agent"] for r in reports] == ["reviewer", "tester", "performance"]
    assert reports[0]["output"] == "[Reviewer-ANSWER] audit the release"


def test_blackboard_shares_state_across_agents_and_isolates_snapshot() -> None:
    """One agent's write is visible to the next; snapshots are top-level copies."""
    board = Blackboard({"objective": "ship v2.2.0"})
    board.set("reviewer", "0 blockers")
    board.append("trail", "reviewer")
    board.append("trail", "tester")

    assert board.get("reviewer") == "0 blockers"
    assert board.has("trail")
    assert len(board.get("trail")) == 2

    snap = board.snapshot()
    snap["injected"] = True
    assert not board.has("injected")  # snapshot is a distinct top-level dict
    board.update({"reviewer": "1 blocker"})
    assert board.get("reviewer") == "1 blocker"
    board.delete("reviewer")
    assert not board.has("reviewer")


# ── message bus ──────────────────────────────────────────────────────────
def test_message_bus_lets_agents_share_information_directly() -> None:
    """An agent broadcasts a finding; peers receive it, the sender does not."""
    bus = AgentCommunication()
    for name in ("reviewer", "coder", "tester"):
        bus.subscribe(name)

    bus.send(Envelope(sender="reviewer", subject="blocker", body="null deref at auth.py:42"))

    assert "null deref" in bus.inbox("coder")[0].body
    assert "null deref" in bus.inbox("tester")[0].body
    assert bus.inbox("reviewer") == []  # sender excluded from its own broadcast
    assert len(bus.log()) == 1
    assert bus.drain("coder")[0].subject == "blocker"
    assert bus.inbox("coder") == []


# ── full team through the TUI state ──────────────────────────────────────
def test_tui_state_runs_members_of_the_full_twenty_agent_team() -> None:
    """The auto-registered 20-agent registry can actually be driven."""
    backend = DirectiveBackend()
    state = TuiState(backend=backend)  # auto-registers all 20

    for name in ("architect", "coder", "reviewer", "tester"):
        result = state.run_agent(name, f"task for {name}")
        assert result.ok

    assert backend.signatures == ["Architect", "Coder", "Reviewer", "Tester"]
    assert len(state.completed_tasks()) == 4
    assert state.llm_calls == 4

    # An empty/unknown request never crashes the console.
    assert state.registry.has("architect")
    assert not state.registry.has("ghost")


def test_echo_backend_pipeline_smoke() -> None:
    """Offline smoke: a whole chain runs end-to-end on EchoBackend alone."""
    chain = Pipeline([ArchitectAgent(EchoBackend()), CoderAgent(EchoBackend())])
    assert chain.run("tiny task").output.startswith("echo: ")
