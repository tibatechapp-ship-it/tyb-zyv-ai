"""PHASE 5 - performance deep tests.

Goal: put a floor under the framework's cost so a regression is caught long
before it reaches the user's phone. Four angles:

* **Load**    - 100 tasks, sequential and concurrently (asyncio), all succeed.
* **Memory**  - 100 runs must not leak unbounded memory (``tracemalloc``).
* **Latency** - a single mocked agent run stays in the low-milliseconds range.
* **Throughput** - the team sustains a sane tasks/second rate.

Plus ``pytest-benchmark`` micro-benchmarks for the hot paths. All thresholds
are deliberately loose: they catch order-of-magnitude regressions, not jitter on
a busy CI runner or a slow phone.
"""

from __future__ import annotations

import asyncio
import tracemalloc
from time import perf_counter

from src.agents import all_agents
from src.agents.architect import ArchitectAgent
from src.agents.coder import CoderAgent
from src.agents.performance import PerformanceAgent
from src.agents.reviewer import ReviewerAgent
from src.core.backend import EchoBackend
from src.patterns.pipeline import Pipeline
from src.tui.state import TuiState

from deep_helpers import RecordingBackend

N_TASKS = 100


# ── 5.1 Load ─────────────────────────────────────────────────────────────
def test_load_100_sequential_tasks_on_the_full_team() -> None:
    """100 runs spread across all 20 agents all complete, and reasonably fast."""
    state = TuiState(backend=RecordingBackend(reply="ok"))
    names = state.registry.names()
    assert len(names) == 20

    started = perf_counter()
    for i in range(N_TASKS):
        result = state.run_agent(names[i % len(names)], f"task {i}")
        assert result.ok
    elapsed = perf_counter() - started

    assert state.llm_calls == N_TASKS
    assert len(state.completed_tasks()) == N_TASKS
    assert elapsed < 30.0, f"100 sequential tasks took {elapsed:.2f}s"


async def test_load_100_concurrent_tasks_via_asyncio() -> None:
    """100 runs fired concurrently (thread pool) all succeed and finish fast.

    This is the phone-reality load shape: many tasks in flight at once. The
    mocked backend has no delay, so the run should complete in a fraction of a
    second; the bound only guards against a pathological regression.
    """
    agent = CoderAgent(RecordingBackend(reply="ok"))
    started = perf_counter()
    results = await asyncio.gather(*(asyncio.to_thread(agent.run, f"task {i}") for i in range(N_TASKS)))
    elapsed = perf_counter() - started

    assert len(results) == N_TASKS
    assert all(r.ok for r in results)
    assert elapsed < 20.0, f"100 concurrent tasks took {elapsed:.2f}s"


# ── 5.2 Memory ───────────────────────────────────────────────────────────
def test_100_runs_do_not_leak_memory() -> None:
    """Running the agent 100 times must not grow memory without bound.

    Uses ``tracemalloc`` after a warm-up so the check reflects steady-state
    allocation, not one-off import/cache costs.
    """
    agent = CoderAgent(EchoBackend())
    agent.run("warm-up")

    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    for i in range(N_TASKS):
        agent.run(f"task {i}")
    after = tracemalloc.take_snapshot()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    growth = sum(stat.size_diff for stat in after.compare_to(baseline, "filename"))
    assert growth < 4 * 1024 * 1024, f"net growth {growth} bytes"
    assert peak < 16 * 1024 * 1024, f"peak {peak} bytes"


# ── 5.3 Latency ──────────────────────────────────────────────────────────
def test_single_run_latency_is_low() -> None:
    """A single mocked agent run stays well under a tenth of a second on average."""
    agent = CoderAgent(EchoBackend())
    agent.run("warm-up")

    samples: list[float] = []
    for _ in range(20):
        started = perf_counter()
        agent.run("measure me")
        samples.append(perf_counter() - started)

    average = sum(samples) / len(samples)
    assert average < 0.10, f"average latency {average * 1000:.2f} ms"
    assert max(samples) < 1.0, f"worst latency {max(samples) * 1000:.2f} ms"


# ── 5.4 Throughput ───────────────────────────────────────────────────────
def test_throughput_meets_a_minimum_rate() -> None:
    """The agent sustains a healthy tasks/second rate on the offline backend."""
    agent = CoderAgent(EchoBackend())
    count = 200
    started = perf_counter()
    for i in range(count):
        agent.run(f"t{i}")
    elapsed = perf_counter() - started

    rate = count / elapsed
    assert rate > 20.0, f"throughput {rate:.1f} tasks/s"


# ── pytest-benchmark micro-benchmarks ────────────────────────────────────
def test_benchmark_single_agent_run(benchmark) -> None:
    """Micro-benchmark: one mocked agent run (the innermost hot path)."""
    agent = CoderAgent(EchoBackend())
    benchmark(agent.run, "benchmark task")


def test_benchmark_team_construction(benchmark) -> None:
    """Micro-benchmark: building the whole 20-agent team."""
    benchmark(all_agents, EchoBackend())


def test_benchmark_pipeline_four_stages(benchmark) -> None:
    """Micro-benchmark: a full Architect -> Coder -> Reviewer -> Perf chain."""
    pipeline = Pipeline(
        [
            ArchitectAgent(EchoBackend()),
            CoderAgent(EchoBackend()),
            ReviewerAgent(EchoBackend()),
            PerformanceAgent(EchoBackend()),
        ]
    )
    benchmark(pipeline.run, "benchmark chain")
