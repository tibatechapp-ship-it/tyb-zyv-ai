"""Category 5 — Complex integration failures.

Distributed-systems failure modes applied to the agent team: cascading failure,
deadlock, livelock, Byzantine backends, network partition, clock skew, split
brain and the thundering herd. The framework must *contain* each one: stop early,
stay bounded, degrade cleanly, and never share state across replicas.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.agents import all_agents
from src.agents.architect import ArchitectAgent
from src.agents.coder import CoderAgent
from src.agents.performance import PerformanceAgent
from src.agents.registry import AgentRegistry
from src.agents.reviewer import ReviewerAgent
from src.core.backend import EchoBackend
from src.core.resilience import (
    BackendUnavailable,
    CommandQueue,
    RateLimiter,
    ResilientBackend,
    RetryPolicy,
    retry_call,
)
from src.core.types import Message
from src.core.watchdog import Watchdog
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline

from deep_helpers import DirectiveBackend, RecordingBackend

CATEGORY = "5-integration"


@pytest.mark.adversarial
def test_cascading_failure_is_contained_by_pipeline() -> None:
    """A failing stage must stop the chain, not let it cascade.

    Scenario: stage 2 (Coder) fails. The pipeline must return that failure
    immediately and never run stages 3 and 4 — the blast radius is one stage.
    """
    backend = DirectiveBackend(fail_for=["Coder"])
    pipeline = Pipeline(
        [
            ArchitectAgent(backend),
            CoderAgent(backend),
            ReviewerAgent(backend),
            PerformanceAgent(backend),
        ]
    )

    result = pipeline.run("design and build the feature")

    assert result.ok is False
    assert result.agent == "coder"
    assert backend.signatures == ["Architect", "Coder"]  # stages 3-4 never ran


@pytest.mark.adversarial
def test_deadlock_is_prevented_when_sharing_a_backend() -> None:
    """Many agents sharing one backend must not deadlock.

    Scenario: eight agents fan out onto a single backend with a small delay. The
    shared object has no lock, but nothing waits on itself, so all calls complete.
    """
    backend = RecordingBackend(reply="ok", delay=0.01)
    agents = [CoderAgent(backend) for _ in range(8)]

    results = Fanout(agents, max_workers=4).run("shared work")

    assert len(results) == 8
    assert all(result.ok for result in results)
    assert backend.attempt_count == 8


@pytest.mark.adversarial
def test_livelock_retries_are_bounded() -> None:
    """A backend that never progresses must not retry forever.

    Scenario: every attempt fails. Build-backoff is capped at ``max_attempts``, so
    the call gives up after exactly three tries instead of spinning.
    """
    backend = RecordingBackend(reply="", failures=10**9)
    policy = RetryPolicy(max_attempts=3, base_delay=0.0)

    with pytest.raises(RuntimeError):
        retry_call(
            lambda: backend.generate([Message("user", "x")]),
            policy,
            sleep=lambda _seconds: None,
        )

    assert backend.attempt_count == 3


@pytest.mark.adversarial
def test_byzantine_backend_is_handled() -> None:
    """A backend answering with the wrong type must not fake a success.

    Scenario: a Byzantine backend returns a ``dict`` instead of ``str``. The
    agent must reject it (``ok=False``) rather than smuggle a non-string into
    ``AgentResult.output`` and break every downstream consumer.
    """
    class DictBackend:
        def generate(self, messages: object, *, model: str | None = None) -> object:
            return {"not": "a string"}

    result = CoderAgent(DictBackend()).run("hello")  # type: ignore[arg-type]

    assert result.ok is False
    assert result.error is not None and "expected str" in result.error


@pytest.mark.adversarial
def test_network_partition_falls_back_then_queues(tmp_path: Path) -> None:
    """A total outage degrades to a queued request, not a hang or crash.

    Scenario: every model is unreachable. The resilient backend exhausts its
    fallback models and retries, then queues the request once and raises a clean,
    typed error so the caller can retry later.
    """
    backend = RecordingBackend(reply="", failures=10**9)
    queue = CommandQueue(tmp_path / "q.json")
    resilient = ResilientBackend(
        backend,
        models=["primary", "backup"],
        policy=RetryPolicy(max_attempts=2, base_delay=0.0),
        queue=queue,
    )

    with pytest.raises(BackendUnavailable):
        resilient.generate([Message("user", "x")])

    assert resilient.stats["queued"] == 1
    assert len(queue) == 1


@pytest.mark.adversarial
def test_clock_skew_does_not_break_the_limiter() -> None:
    """A backwards clock must fail closed, and never crash the watchdog.

    Scenario: the wall clock jumps backwards mid-window. The limiter must not
    over-admit (it fails closed) and, once time moves forward past the window,
    admit again; the watchdog must keep ticking through the skew.
    """
    clock = {"t": 1000.0}
    limiter = RateLimiter(max_calls=2, window=10.0, clock=lambda: clock["t"])
    assert limiter.try_acquire() and limiter.try_acquire()

    clock["t"] = 500.0  # backwards
    assert limiter.try_acquire() is False  # fail closed

    clock["t"] = 1011.0  # forwards past the window
    assert limiter.try_acquire() is True

    watchdog = Watchdog(
        lambda: True,
        lambda: None,
        interval=1.0,
        clock=lambda: clock["t"],
        sleep=lambda _seconds: None,
    )
    assert watchdog.run(max_cycles=3) == 3
    assert watchdog.checks == 3


@pytest.mark.adversarial
def test_split_brain_two_registries_stay_independent() -> None:
    """Two registries must not share mutable state (no split-brain drift).

    Scenario: mutate one replica. The other must be untouched — there is no
    module-global registry leaking entries between them.
    """
    first = AgentRegistry(all_agents(EchoBackend()))
    second = AgentRegistry(all_agents(EchoBackend()))
    assert len(first) == len(second) == 20

    first.unregister("coder")

    assert first.has("coder") is False
    assert second.has("coder") is True
    assert len(second) == 20


@pytest.mark.adversarial
def test_thundering_herd_is_bounded_by_max_workers() -> None:
    """A burst of agents must respect the concurrency cap.

    Scenario: twelve agents wake at once under a cap of three workers. Peak
    in-flight work must never exceed the cap — the herd is throttled.
    """
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class Counting:
        def generate(self, messages: object, *, model: str | None = None) -> str:
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.02)
            with lock:
                state["now"] -= 1
            return "ok"

    agents = [CoderAgent(Counting()) for _ in range(12)]  # type: ignore[arg-type]
    results = Fanout(agents, max_workers=3).run("storm")

    assert len(results) == 12
    assert all(result.ok for result in results)
    assert state["peak"] <= 3
