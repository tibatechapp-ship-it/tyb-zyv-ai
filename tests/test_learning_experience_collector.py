"""Tests for the experience collector (Phase 1 + 1.5).

A ``FakeClock`` keeps timestamps deterministic; one collector is built per test
so buffers never leak between tests.
"""

from __future__ import annotations

import threading

import pytest

from src.learning import Experience, ExperienceCollector, Outcome

T0 = 1_700_000_000.0


class FakeClock:
    """A clock you advance by hand."""

    def __init__(self, start: float = T0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def collector():
    clock = FakeClock()
    return ExperienceCollector(clock=clock), clock


def fill(collector: ExperienceCollector, n: int = 5) -> None:
    """Record n experiences: 3 successes, 1 failure, 1 partial, newest last."""
    for i in range(n):
        outcome = (
            Outcome.SUCCESS
            if i < 3
            else Outcome.FAILURE
            if i == 3
            else Outcome.PARTIAL
        )
        collector.record(
            "Coder" if i % 2 == 0 else "Planner",
            "deploy" if i % 3 == 0 else "review",
            outcome,
            action=f"step-{i}",
            result=f"result-{i}",
        )


# --------------------------------------------------------------------------- #
# Phase 1 — core
# --------------------------------------------------------------------------- #
def test_record_appends_chronologically(collector):
    mem, _clock = collector
    fill(mem)
    assert len(mem) == 5
    assert [e.action for e in mem.all()] == ["step-0", "step-1", "step-2", "step-3", "step-4"]


def test_record_timestamps_from_injected_clock(collector):
    mem, clock = collector
    first = mem.record("Coder", "deploy", Outcome.SUCCESS)
    clock.advance(1.5)
    second = mem.record("Coder", "deploy", Outcome.SUCCESS)
    assert first.ts == T0
    assert second.ts == pytest.approx(T0 + 1.5)


def test_record_returns_the_experience(collector):
    mem, _clock = collector
    exp = mem.record("Coder", "deploy", Outcome.SUCCESS, action="build")
    assert isinstance(exp, Experience)
    assert exp.agent_role == "Coder"


def test_clear_drops_everything(collector):
    mem, _clock = collector
    fill(mem)
    mem.clear()
    assert len(mem) == 0
    assert mem.all() == []


def test_stats_counts_outcomes(collector):
    mem, _clock = collector
    fill(mem)
    assert mem.stats() == {
        "total": 5,
        "successes": 3,
        "failures": 1,
        "partials": 1,
    }


def test_stats_empty_collector(collector):
    mem, _clock = collector
    assert mem.stats() == {"total": 0, "successes": 0, "failures": 0, "partials": 0}


def test_experience_to_dict_serialisable(collector):
    mem, _clock = collector
    exp = mem.record("Coder", "deploy", Outcome.SUCCESS, action="build", result="ok")
    data = exp.to_dict()
    assert data == {
        "agent_role": "Coder",
        "action_type": "deploy",
        "outcome": "success",
        "action": "build",
        "result": "ok",
        "ts": T0,
    }
    assert isinstance(data["outcome"], str)  # JSON-safe (StrEnum)


# --------------------------------------------------------------------------- #
# Phase 1.5 — advanced queries (newest first)
# --------------------------------------------------------------------------- #
def test_get_by_agent_filters_newest_first(collector):
    mem, _clock = collector
    fill(mem)
    coder = mem.get_by_agent("Coder")
    assert [e.agent_role for e in coder] == ["Coder", "Coder", "Coder"]
    assert [e.action for e in coder] == ["step-4", "step-2", "step-0"]  # newest first


def test_get_by_agent_unknown_role_returns_empty(collector):
    mem, _clock = collector
    fill(mem)
    assert mem.get_by_agent("Nobody") == []


def test_get_by_action_filters(collector):
    mem, _clock = collector
    fill(mem)
    deploys = mem.get_by_action("deploy")
    assert [e.action for e in deploys] == ["step-3", "step-0"]
    reviews = mem.get_by_action("review")
    assert [e.action for e in reviews] == ["step-4", "step-2", "step-1"]


def test_get_by_outcome_filters(collector):
    mem, _clock = collector
    fill(mem)
    failures = mem.get_by_outcome(Outcome.FAILURE)
    assert [e.action for e in failures] == ["step-3"]
    assert mem.get_by_outcome(Outcome.SUCCESS) is not None


def test_queries_respect_limit(collector):
    mem, _clock = collector
    fill(mem)
    assert len(mem.get_by_agent("Coder", limit=2)) == 2
    assert [e.action for e in mem.get_by_agent("Coder", limit=2)] == ["step-4", "step-2"]
    assert len(mem.get_by_action("review", limit=1)) == 1
    assert len(mem.get_by_outcome(Outcome.SUCCESS, limit=2)) == 2


def test_search_with_custom_predicate(collector):
    mem, _clock = collector
    fill(mem)
    slow = mem.search(lambda e: e.result == "result-4")
    assert [e.action for e in slow] == ["step-4"]
    failed = mem.search(lambda e: e.outcome is not Outcome.SUCCESS)
    assert [e.action for e in failed] == ["step-4", "step-3"]


def test_search_newest_first_and_limit(collector):
    mem, _clock = collector
    fill(mem)
    results = mem.search(lambda e: e.action_type == "review", limit=1)
    assert [e.action for e in results] == ["step-4"]  # newest review only


def test_search_empty_buffer(collector):
    mem, _clock = collector
    assert mem.search(lambda e: True) == []


def test_queries_do_not_mutate_buffer(collector):
    mem, _clock = collector
    fill(mem)
    mem.get_by_agent("Coder", limit=1)
    mem.search(lambda e: True)
    assert len(mem) == 5


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_records_all_persist(collector):
    mem, _clock = collector

    def worker(start: int) -> None:
        for i in range(50):
            mem.record("Coder", "deploy", Outcome.SUCCESS, action=f"{start}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)
    assert len(mem) == 500
    assert mem.stats()["successes"] == 500


def test_concurrent_reads_during_writes_do_not_crash(collector):
    mem, _clock = collector
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            mem.all()
            mem.get_by_agent("Coder")
            mem.search(lambda e: True)
            mem.stats()

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for i in range(200):
        mem.record("Coder", "deploy", Outcome.SUCCESS, action=f"t{i}")
    stop.set()
    for thread in threads:
        thread.join(timeout=30)
    assert len(mem) == 200