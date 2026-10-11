"""Concurrency hardening — the mutable singletons must be thread-safe.

``AgentMetrics`` and ``AgentRegistry`` are the two coordination points the whole
team shares, and a single agent/registry can be driven from many OS threads at
once (fan-out, ``asyncio.to_thread``). These tests hammer each with 100 real
threads and assert **nothing is lost and nothing double-registers** — the
guarantee a free-threaded build would otherwise break.

They also cover the two failure modes a naive lock introduces: **deadlock**
(threads must finish within a timeout) and **lock-ordering inversion**
(``registry.snapshot()`` acquires the registry lock then the metrics lock —
concurrent writers must not deadlock against it).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import pytest

from src.agents.base import BaseAgent
from src.agents.metrics import AgentMetrics
from src.agents.registry import AgentRegistry, DuplicateAgentError
from src.core.backend import EchoBackend
from src.core.backend import LLMBackend


class _NamedAgent(BaseAgent):
    """A minimal concrete agent whose name is set per-instance (registry races)."""

    group = "core"

    def __init__(self, name: str, backend: LLMBackend) -> None:
        super().__init__(backend)
        self.name = name


def _run_and_join(workers: list[Callable[[], None]], *, timeout: float = 30.0) -> bool:
    """Start every worker on a shared barrier; return True if all finish in time.

    The barrier releases all threads at once so the critical sections genuinely
    overlap (maximum contention). A ``False`` return means something is stuck —
    i.e. a deadlock — which callers turn into a failure.
    """
    barrier = threading.Barrier(len(workers))

    def wrapped(worker: Callable[[], None]) -> None:
        barrier.wait()
        worker()

    threads = [threading.Thread(target=wrapped, args=(worker,)) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    return all(not thread.is_alive() for thread in threads)


# ── AgentMetrics ─────────────────────────────────────────────────────────
def test_metrics_record_from_100_threads_loses_nothing() -> None:
    """100 threads × 50 records = exactly 5,000 — no lost updates."""
    metrics = AgentMetrics()
    per_thread = 50

    def worker() -> None:
        for _ in range(per_thread):
            metrics.record(0.001, ok=True)

    assert _run_and_join([worker for _ in range(100)]) is True

    assert metrics.tasks_run == 100 * per_thread
    assert metrics.tasks_ok == 100 * per_thread
    assert metrics.tasks_failed == 0
    assert len(metrics.durations) == 100 * per_thread
    assert metrics.total_seconds == pytest.approx(0.001 * 100 * per_thread)


def test_metrics_counts_successes_and_failures_under_contention() -> None:
    """Half the threads report failure; the split must be exact, never torn."""
    metrics = AgentMetrics()
    per_thread = 40

    def ok_worker() -> None:
        for _ in range(per_thread):
            metrics.record(0.0, ok=True)

    def fail_worker() -> None:
        for _ in range(per_thread):
            metrics.record(0.0, ok=False)

    workers = [ok_worker] * 50 + [fail_worker] * 50
    assert _run_and_join(workers) is True

    assert metrics.tasks_run == 100 * per_thread
    assert metrics.tasks_ok == 50 * per_thread
    assert metrics.tasks_failed == 50 * per_thread


def test_metrics_snapshot_is_never_torn_under_writes() -> None:
    """Readers never observe an inconsistent snapshot while writers record."""
    metrics = AgentMetrics()
    stop = threading.Event()
    errors: list[BaseException] = []
    guard = threading.Lock()

    def writer() -> None:
        for _ in range(2_000):
            metrics.record(0.001, ok=True)

    def reader() -> None:
        while not stop.is_set():
            try:
                snapshot = metrics.to_dict()
                # tasks_run always equals tasks_ok + tasks_failed in a coherent read.
                assert snapshot["tasks_run"] == snapshot["tasks_ok"] + snapshot["tasks_failed"]
            except BaseException as exc:  # noqa: BLE001 - record, then fail loudly
                with guard:
                    errors.append(exc)
                return

    writers = [threading.Thread(target=writer) for _ in range(8)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers + writers:
        thread.start()
    for thread in writers:
        thread.join(30.0)
    stop.set()
    for thread in readers:
        thread.join(30.0)

    assert not errors, f"reader saw a torn snapshot: {errors[:1]}"
    assert metrics.tasks_run == 8 * 2_000


# ── AgentRegistry ────────────────────────────────────────────────────────
def test_registry_register_from_100_threads_registers_all() -> None:
    """100 threads each register one distinct agent — all 100 survive."""
    registry = AgentRegistry()

    def make_worker(index: int) -> Callable[[], None]:
        def worker() -> None:
            registry.register(_NamedAgent(f"agent-{index}", EchoBackend()))

        return worker

    assert _run_and_join([make_worker(i) for i in range(100)]) is True

    assert len(registry) == 100
    assert set(registry.names()) == {f"agent-{i}" for i in range(100)}


def test_registry_duplicate_race_has_exactly_one_winner() -> None:
    """100 threads race to register the same name — one wins, 99 collide."""
    registry = AgentRegistry()
    wins = 0
    collisions = 0
    guard = threading.Lock()

    def worker() -> None:
        nonlocal wins, collisions
        try:
            registry.register(_NamedAgent("solo", EchoBackend()))
            with guard:
                wins += 1
        except DuplicateAgentError:
            with guard:
                collisions += 1

    assert _run_and_join([worker for _ in range(100)]) is True

    assert len(registry) == 1
    assert wins == 1
    assert collisions == 99


def test_registry_concurrent_reads_during_writes_are_safe() -> None:
    """Readers iterating while writers register never crash; 200 agents land."""
    registry = AgentRegistry()
    errors: list[BaseException] = []
    guard = threading.Lock()

    def make_writer(start: int) -> Callable[[], None]:
        def writer() -> None:
            for index in range(start, start + 50):
                registry.register(_NamedAgent(f"w{index}", EchoBackend()))

        return writer

    def reader() -> None:
        for _ in range(300):
            try:
                for _agent in registry:  # __iter__ over a live dict is the risk
                    pass
                registry.all()
                registry.groups()
                registry.snapshot()
                len(registry)
            except BaseException as exc:  # noqa: BLE001 - a crash here is the bug
                with guard:
                    errors.append(exc)
                return

    workers: list[Callable[[], None]] = [make_writer(i * 50) for i in range(4)]
    workers += [reader for _ in range(4)]
    assert _run_and_join(workers) is True

    assert not errors, f"concurrent read raced a write: {errors[:1]}"
    assert len(registry) == 200


# ── asyncio (pytest-asyncio, asyncio_mode=auto) ──────────────────────────
async def test_metrics_record_is_safe_under_asyncio_to_thread() -> None:
    """100 concurrent asyncio.to_thread workers lose no metric updates."""
    metrics = AgentMetrics()
    per_thread = 40

    def worker() -> None:
        for _ in range(per_thread):
            metrics.record(0.001, ok=True)

    await asyncio.gather(*(asyncio.to_thread(worker) for _ in range(100)))

    assert metrics.tasks_run == 100 * per_thread
    assert len(metrics.durations) == 100 * per_thread


async def test_registry_register_is_safe_under_asyncio_to_thread() -> None:
    """100 concurrent asyncio.to_thread registrations all land."""
    registry = AgentRegistry()

    def worker(index: int) -> None:
        registry.register(_NamedAgent(f"async-{index}", EchoBackend()))

    await asyncio.gather(*(asyncio.to_thread(worker, i) for i in range(100)))

    assert len(registry) == 100


# ── Bonus: deadlock, lock ordering, overhead ─────────────────────────────
def test_no_deadlock_across_all_registry_methods() -> None:
    """Heavy mixed use of every locked method must never deadlock.

    Scenario: 60 threads call a random-ish mix of register / unregister / get /
    all / groups / snapshot / iter / len under maximum contention. If any lock
    could be held twice or acquired in a cycle, the join would time out.
    """
    registry = AgentRegistry()
    methods = [
        lambda i: registry.register(_NamedAgent(f"m{i}", EchoBackend())),
        lambda i: registry.unregister(f"m{i}"),
        lambda i: registry.has(f"m{i}"),
        lambda i: registry.all(),
        lambda i: registry.groups(),
        lambda i: registry.snapshot(),
        lambda i: list(registry),
        lambda i: len(registry),
    ]

    def make_worker(index: int) -> Callable[[], None]:
        def worker() -> None:
            for step in range(500):
                methods[(index + step) % len(methods)](index)

        return worker

    assert _run_and_join([make_worker(i) for i in range(60)]) is True


def test_lock_ordering_registry_then_metrics_is_consistent() -> None:
    """``snapshot`` takes registry→metrics; concurrent writers must not invert it.

    Scenario: threads call ``registry.snapshot()`` (registry lock → each agent's
    ``metrics.to_dict()``) while others hammer ``agent.metrics.record()``. A
    consistent global order means no cycle ⇒ no deadlock; we assert completion.
    """
    backend = EchoBackend()
    agent = _NamedAgent("locked", backend)
    registry = AgentRegistry([agent])
    agent_workers = 30
    snapshot_workers = 30

    def recorder() -> None:
        for _ in range(1_000):
            agent.metrics.record(0.0, ok=True)

    def snapshooter() -> None:
        for _ in range(1_000):
            registry.snapshot()

    workers: list[Callable[[], None]] = [recorder] * agent_workers
    workers += [snapshooter] * snapshot_workers
    assert _run_and_join(workers) is True

    assert agent.metrics.tasks_run == agent_workers * 1_000


def test_benchmark_metrics_record_lock_overhead(benchmark) -> None:
    """Micro-benchmark the locked ``record`` path (shows the lock's cost)."""
    metrics = AgentMetrics()
    benchmark(metrics.record, 0.001, ok=True)
