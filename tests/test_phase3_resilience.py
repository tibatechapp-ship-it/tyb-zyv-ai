"""PHASE 3 - resilience deep tests.

Goal: prove the framework survives the hostile things a phone does to a
long-running process - transient backend failures, a call that hangs forever,
the network dropping mid-request, a model going away, and loops that must never
spin unbounded.

The real reliability layer lives in ``src/core/resilience.py`` and
``src/core/watchdog.py``; these tests drive it end-to-end through real agents
and a real on-disk queue. No real timers or network are used.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.agents.coder import CoderAgent
from src.agents.state import AgentStatus
from src.core.resilience import (
    BackendUnavailable,
    CommandQueue,
    ResilientBackend,
    RetryPolicy,
)
from src.core.types import Message
from src.core.watchdog import Heartbeat, Watchdog

from deep_helpers import BlockingBackend, ModelBackend, RecordingBackend


def _no_sleep(_seconds: float) -> None:
    """Injected sleeper so retry/loop tests never actually wait."""


# ── 3.1 Retry ────────────────────────────────────────────────────────────
def test_retry_recovers_after_transient_failures() -> None:
    """A backend that fails twice then succeeds still returns an answer.

    The retry layer must swallow the transient failures, keep the agent's
    outward result successful, and *not* count the recovery as a failure.
    """
    inner = RecordingBackend(reply="recovered", failures=2)
    backend = ResilientBackend(inner, policy=RetryPolicy(max_attempts=3, base_delay=0.0))

    assert backend.generate([Message("user", "hi")]) == "recovered"
    assert inner.attempt_count == 3
    assert backend.stats["failures"] == 0
    assert backend.stats["calls"] == 1


def test_retry_never_loops_forever_even_when_always_failing() -> None:
    """An always-failing backend is retried exactly ``max_attempts`` times."""
    inner = RecordingBackend(failures=10_000)
    backend = ResilientBackend(inner, policy=RetryPolicy(max_attempts=3, base_delay=0.0))

    with pytest.raises(RuntimeError):
        backend.generate([Message("user", "boom")])
    assert inner.attempt_count == 3
    assert backend.stats["failures"] == 1


def test_agent_recovers_through_resilient_backend() -> None:
    """The same resilience holds one layer up, inside a real agent."""
    inner = RecordingBackend(reply="answered", failures=1)
    backend = ResilientBackend(inner, policy=RetryPolicy(max_attempts=2, base_delay=0.0))
    agent = CoderAgent(backend)

    result = agent.run("please answer")
    assert result.ok
    assert result.output == "answered"
    assert inner.attempt_count == 2


# ── 3.2 Timeout / cancellation ───────────────────────────────────────────
def test_hung_call_is_interruptible_and_leaves_the_agent_usable() -> None:
    """A call that blocks can be abandoned; the agent must not be corrupted.

    While hung the agent is visibly BUSY; once released the same agent instance
    accepts the next task normally. This is the phone-reality case of a stuck
    request that must never wedge the whole console.
    """
    backend = BlockingBackend()
    agent = CoderAgent(backend)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(agent.run, "hang please")
        assert backend.entered.wait(timeout=5.0)
        assert agent.state.status is AgentStatus.BUSY

        with pytest.raises(TimeoutError):
            future.result(timeout=0.2)  # the caller gives up waiting

        backend.release.set()
        result = future.result(timeout=5.0)  # the stuck call finishes cleanly
        assert result.ok and result.output == "after unblock"
    finally:
        backend.release.set()
        pool.shutdown(wait=True)

    assert agent.state.status is AgentStatus.IDLE
    assert agent.metrics.tasks_ok == 1


# ── 3.3 Queue (network drop then resume) ─────────────────────────────────
def test_queue_captures_requests_while_down_then_replays_them(tmp_path: Path) -> None:
    """A request made while the network is down survives and runs on recovery.

    The request must be persisted to disk (not lost), raise a typed
    ``BackendUnavailable`` to the caller, and be replayable - message included -
    by a *fresh* backend instance once the server is reachable again.
    """
    queue_path = tmp_path / "offline-queue.json"

    down = RecordingBackend(failures=10_000)
    offline = ResilientBackend(
        down,
        policy=RetryPolicy(max_attempts=1, base_delay=0.0),
        queue=CommandQueue(queue_path),
    )
    with pytest.raises(BackendUnavailable):
        offline.generate([Message("user", "important when back")])
    assert offline.stats["queued"] == 1
    assert len(CommandQueue(queue_path)) == 1  # survived to disk

    healed = RecordingBackend(reply="online")
    online = ResilientBackend(
        healed,
        policy=RetryPolicy(max_attempts=1, base_delay=0.0),
        queue=CommandQueue(queue_path),
    )
    assert online.replay() == 1
    assert CommandQueue(queue_path).pending() == []
    assert healed.calls[0][0][-1].content == "important when back"


def test_queue_drain_stops_on_failure_and_loses_nothing(tmp_path: Path) -> None:
    """Resume is safe: a failing item stays at the head, order preserved."""
    queue = CommandQueue(tmp_path / "q.json")
    for i in range(3):
        queue.enqueue({"n": i})

    def handler(item: dict[str, object]) -> None:
        if item["n"] == 1:
            raise RuntimeError("still down")

    assert queue.drain(handler) == 1
    assert [item["n"] for item in queue.pending()] == [1, 2]


# ── 3.4 Fallback (model goes away) ───────────────────────────────────────
def test_fallback_moves_to_the_next_model_when_the_primary_fails() -> None:
    """If the primary model is unavailable, the alternate answers instead."""
    inner = ModelBackend(ok_models=["backup"])
    backend = ResilientBackend(
        inner,
        models=["primary", "backup"],
        policy=RetryPolicy(max_attempts=1, base_delay=0.0),
    )

    assert backend.generate([Message("user", "q")]) == "reply-from-backup"
    assert inner.tried == ["primary", "backup"]
    assert backend.stats["fallbacks"] == 1


def test_fallback_gives_up_only_after_every_model_failed() -> None:
    """With no working model the last error surfaces - no infinite fallback."""
    inner = ModelBackend(ok_models=[])
    backend = ResilientBackend(
        inner,
        models=["a", "b"],
        policy=RetryPolicy(max_attempts=1, base_delay=0.0),
    )

    with pytest.raises(RuntimeError):
        backend.generate([Message("user", "q")])
    assert inner.tried == ["a", "b"]
    assert backend.stats["failures"] == 1


# ── 3.5 Loop detection ───────────────────────────────────────────────────
def test_watchdog_loop_is_bounded_by_its_cycle_cap() -> None:
    """An unhealthy service is restarted per cycle, then the loop stops.

    This is the guard against an infinite restart loop: a permanently-down
    service is retried a bounded number of times, never spun forever.
    """
    restarts: list[float] = []
    watchdog = Watchdog(
        lambda: False,
        lambda: restarts.append(1.0),
        interval=1.0,
        sleep=_no_sleep,
    )
    cycles = watchdog.run(max_cycles=4)

    assert cycles == 4
    assert watchdog.restarts == 4
    assert watchdog.checks == 4


def test_watchdog_loop_stops_via_stop_predicate() -> None:
    """An external stop predicate breaks the loop mid-run."""
    restarts: list[float] = []
    watchdog = Watchdog(
        lambda: False,
        lambda: restarts.append(1.0),
        interval=1.0,
        sleep=_no_sleep,
    )

    def stop() -> bool:
        return watchdog.checks >= 3

    assert watchdog.run(should_stop=stop) == 3
    assert watchdog.restarts == 3


def test_heartbeat_loop_is_bounded_and_skips_when_not_due() -> None:
    """The heartbeat loop terminates and never beats before its interval."""
    sent: list[str] = []
    clock = {"t": 0.0}
    beat = Heartbeat(
        lambda msg: sent.append(msg),
        interval=10.0,
        clock=lambda: clock["t"],
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 10.0),
    )
    cycles = beat.run(max_cycles=3, should_stop=lambda: clock["t"] >= 20.0)

    # Two loop cycles ran; only the second was actually due (the first was
    # within the interval), proving the loop both terminates and paces itself.
    assert cycles == 2
    assert len(sent) == 1
