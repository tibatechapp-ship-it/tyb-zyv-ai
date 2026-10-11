"""Tests for the experience collector (Phase 1 + 1.5).

A ``FakeClock`` keeps timestamps deterministic; one collector is built per test
so buffers never leak between tests. All roles/action types are the canonical
lowercase values from ``KNOWN_AGENT_ROLES`` / ``KNOWN_ACTION_TYPES``.
"""

from __future__ import annotations

import json
import logging
import threading

import pytest

from src.learning import DAY, Experience, ExperienceCollector, Outcome
from src.learning.experience_collector import KNOWN_ACTION_TYPES, KNOWN_AGENT_ROLES

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
    """Record n valid experiences: 3 successes, 1 failure, 1 partial."""
    for i in range(n):
        outcome = (
            Outcome.SUCCESS
            if i < 3
            else Outcome.FAILURE
            if i == 3
            else Outcome.PARTIAL
        )
        collector.record(
            f"task-{i}", f"step-{i}",
            "coder" if i % 2 == 0 else "planner",
            "write_file" if i % 3 == 0 else "read_file",
            outcome,
            action=f"act-{i}",
            result=f"result-{i}",
        )


# --------------------------------------------------------------------------- #
# known lists
# --------------------------------------------------------------------------- #
def test_known_lists_are_frozen_and_populated():
    assert isinstance(KNOWN_AGENT_ROLES, frozenset) and len(KNOWN_AGENT_ROLES) == 20
    assert isinstance(KNOWN_ACTION_TYPES, frozenset) and len(KNOWN_ACTION_TYPES) == 10


# --------------------------------------------------------------------------- #
# Phase 1 — core
# --------------------------------------------------------------------------- #
def test_record_appends_chronologically(collector):
    mem, _clock = collector
    fill(mem)
    assert len(mem) == 5
    assert [e.action for e in mem.all()] == ["act-0", "act-1", "act-2", "act-3", "act-4"]


def test_record_timestamps_from_injected_clock(collector):
    mem, clock = collector
    first = mem.record("t1", "s1", "coder", "write_file", Outcome.SUCCESS)
    clock.advance(1.5)
    second = mem.record("t2", "s2", "coder", "write_file", Outcome.SUCCESS)
    assert first.ts == T0
    assert second.ts == pytest.approx(T0 + 1.5)


def test_record_returns_the_experience(collector):
    mem, _clock = collector
    exp = mem.record("t1", "s1", "coder", "write_file", Outcome.SUCCESS, action="build")
    assert isinstance(exp, Experience)
    assert exp.task_id == "t1"
    assert exp.step_id == "s1"


def test_clear_drops_everything(collector):
    mem, _clock = collector
    fill(mem)
    mem.clear()
    assert len(mem) == 0
    assert mem.all() == []


# --------------------------------------------------------------------------- #
# Phase 1.5 — fail-safe record
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["task_id", "step_id"])
def test_record_rejects_blank_identifiers(collector, field):
    mem, _clock = collector
    kwargs = {"task_id": "t1", "step_id": "s1"}
    kwargs[field] = "   "
    with pytest.raises(ValueError, match=f"{field} must be a non-empty"):
        mem.record(kwargs["task_id"], kwargs["step_id"], "coder", "read_file", Outcome.SUCCESS)


def test_record_rejects_unknown_agent_role(collector):
    mem, _clock = collector
    with pytest.raises(ValueError, match="unknown agent_role 'robot'"):
        mem.record("t1", "s1", "robot", "read_file", Outcome.SUCCESS)
    assert len(mem) == 0


def test_record_rejects_unknown_action_type(collector):
    mem, _clock = collector
    with pytest.raises(ValueError, match="unknown action_type 'hack'"):
        mem.record("t1", "s1", "coder", "hack", Outcome.SUCCESS)
    assert len(mem) == 0


def test_record_rejects_uppercase_role(collector):
    mem, _clock = collector
    with pytest.raises(ValueError):
        mem.record("t1", "s1", "Coder", "read_file", Outcome.SUCCESS)


def test_record_logs_warning_on_rejection(collector, caplog):
    mem, _clock = collector
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            mem.record("t1", "s1", "robot", "read_file", Outcome.SUCCESS)
    assert any("unknown agent_role" in record.message for record in caplog.records)


def test_record_after_failure_leaves_buffer_untouched(collector):
    mem, _clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.SUCCESS)
    with pytest.raises(ValueError):
        mem.record("t2", "s2", "coder", "nope", Outcome.SUCCESS)
    assert len(mem) == 1


# --------------------------------------------------------------------------- #
# Phase 1.5 — statistics
# --------------------------------------------------------------------------- #
def test_stats_shape_and_rates(collector):
    mem, _clock = collector
    clock = _clock
    for i in range(6):
        mem.record(f"t{i}", f"s{i}", "coder", "read_file", Outcome.SUCCESS)
        clock.advance(10)
    for i in range(4):
        mem.record(f"f{i}", f"s{i}", "planner", "write_file", Outcome.FAILURE)
        clock.advance(10)
    stats = mem.stats()
    assert stats["total"] == 10
    assert stats["success_rate"] == pytest.approx(0.6)
    assert stats["failure_rate"] == pytest.approx(0.4)


def test_stats_by_agent_sorted_descending(collector):
    mem, _clock = collector
    fill(mem)  # coder x3, planner x2
    by_agent = mem.stats()["by_agent"]
    assert by_agent == {"coder": 3, "planner": 2}
    assert list(by_agent.values()) == sorted(by_agent.values(), reverse=True)


def test_stats_by_action_and_outcome(collector):
    mem, _clock = collector
    fill(mem)  # write_file x2 (i=0,3), read_file x3
    stats = mem.stats()
    assert stats["by_action"] == {"read_file": 3, "write_file": 2}
    assert stats["by_outcome"] == {"success": 3, "failure": 1, "partial": 1}


def test_stats_time_range(collector):
    mem, clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.SUCCESS)  # ts = T0
    clock.advance(42.0)
    mem.record("t2", "s2", "coder", "write_file", Outcome.SUCCESS)  # ts = T0+42
    tr = mem.stats()["time_range"]
    assert tr["first"] == T0
    assert tr["last"] == pytest.approx(T0 + 42.0)
    assert tr["span_seconds"] == pytest.approx(42.0)


def test_stats_empty_collector(collector):
    mem, _clock = collector
    assert mem.stats() == {
        "total": 0,
        "success_rate": 0.0,
        "failure_rate": 0.0,
        "by_agent": {},
        "by_action": {},
        "by_outcome": {},
        "time_range": {"first": 0.0, "last": 0.0, "span_seconds": 0.0},
    }


# --------------------------------------------------------------------------- #
# Phase 1.5 — to_dict / from_dict
# --------------------------------------------------------------------------- #
def test_experience_to_dict_round_trip(collector):
    mem, _clock = collector
    exp = mem.record(
        "t1", "s1", "coder", "write_file", Outcome.SUCCESS, action="build", result="ok"
    )
    data = exp.to_dict()
    assert data["task_id"] == "t1"
    assert data["outcome"] == "success"  # JSON-safe StrEnum
    restored = Experience.from_dict(data)
    assert restored == exp


def test_from_dict_rejects_unknown_outcome():
    with pytest.raises(ValueError):
        Experience.from_dict(
            {"task_id": "t", "step_id": "s", "agent_role": "coder",
             "action_type": "read_file", "outcome": "exploded"}
        )


def test_from_dict_requires_identifiers():
    with pytest.raises(KeyError):
        Experience.from_dict(
            {"agent_role": "coder", "action_type": "read_file", "outcome": "success"}
        )


# --------------------------------------------------------------------------- #
# Phase 1.5 — export / import
# --------------------------------------------------------------------------- #
def test_export_import_round_trip(tmp_path, collector):
    mem, clock = collector
    fill(mem)
    dump = tmp_path / "exp" / "experiences.json"
    assert mem.export(dump) == 5

    fresh = ExperienceCollector(clock=clock)
    assert fresh.import_from(dump) == 5
    assert len(fresh) == 5
    assert [e.action for e in fresh.all()] == [e.action for e in mem.all()]


def test_import_is_idempotent_append(tmp_path, collector):
    mem, _clock = collector
    fill(mem)
    dump = tmp_path / "dump.json"
    mem.export(dump)
    assert mem.import_from(dump) == 5
    assert len(mem) == 10


def test_import_skips_invalid_items(tmp_path, collector, caplog):
    mem, _clock = collector
    dump = tmp_path / "dump.json"
    dump.write_text(
        json.dumps(
            [
                {"task_id": "t1", "step_id": "s1", "agent_role": "coder",
                 "action_type": "read_file", "outcome": "success"},
                {"task_id": "t2"},  # incomplete
                {"task_id": "t3", "step_id": "s3", "agent_role": "coder",
                 "action_type": "read_file", "outcome": "exploded"},
                "garbage",
            ]
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert mem.import_from(dump) == 1
    assert len(mem) == 1
    assert mem.all()[0].task_id == "t1"


def test_export_creates_parent_directories(tmp_path, collector):
    mem, _clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.SUCCESS)
    dump = tmp_path / "a" / "b" / "c.json"
    assert mem.export(dump) == 1
    assert dump.exists()


def test_jsonl_round_trip(tmp_path, collector):
    mem, clock = collector
    fill(mem)
    dump = tmp_path / "experiences.jsonl"
    assert mem.export_to_jsonl(dump) == 5

    fresh = ExperienceCollector(clock=clock)
    assert fresh.import_from_jsonl(dump) == 5
    assert [e.to_dict() for e in fresh.all()] == [e.to_dict() for e in mem.all()]


def test_jsonl_skips_blank_and_broken_lines(tmp_path, collector, caplog):
    mem, _clock = collector
    dump = tmp_path / "dump.jsonl"
    dump.write_text(
        "{\"task_id\": \"t1\", \"step_id\": \"s1\", \"agent_role\": \"coder\","
        " \"action_type\": \"read_file\", \"outcome\": \"success\"}\n"
        "\n"
        "{not-json}\n"
        "{\"task_id\": \"t2\"}\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert mem.import_from_jsonl(dump) == 1
    assert len(mem) == 1
    assert mem.all()[0].task_id == "t1"


def test_import_missing_file_raises(tmp_path, collector):
    mem, _clock = collector
    with pytest.raises(FileNotFoundError):
        mem.import_from(tmp_path / "missing.json")


# --------------------------------------------------------------------------- #
# Phase 1.5 — retention
# --------------------------------------------------------------------------- #
def test_prune_by_age(collector):
    mem, clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.SUCCESS)
    clock.advance(40 * DAY)
    mem.record("t2", "s2", "coder", "write_file", Outcome.SUCCESS)
    removed = mem.prune(max_age_days=30)
    assert removed == 1
    assert [e.task_id for e in mem.all()] == ["t2"]


def test_prune_no_removal_within_window(collector):
    mem, clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.SUCCESS)
    clock.advance(10 * DAY)
    assert mem.prune(max_age_days=30) == 0
    assert len(mem) == 1


def test_prune_by_max_count_keeps_newest(collector):
    mem, clock = collector
    for i in range(5):
        mem.record(f"t{i}", f"s{i}", "coder", "read_file", Outcome.SUCCESS)
        clock.advance(1)
    removed = mem.prune(max_count=2)
    assert removed == 3
    assert [e.task_id for e in mem.all()] == ["t3", "t4"]


def test_prune_combines_age_and_count(collector):
    mem, clock = collector
    mem.record("old", "s", "coder", "read_file", Outcome.SUCCESS)
    clock.advance(40 * DAY)
    for i in range(3):
        mem.record(f"t{i}", f"s{i}", "coder", "read_file", Outcome.SUCCESS)
        clock.advance(1)
    removed = mem.prune(max_age_days=30, max_count=2)
    assert removed == 2  # 1 old + 1 newest-trimmed
    assert [e.task_id for e in mem.all()] == ["t1", "t2"]


def test_prune_by_outcome_keeps_newest_of_that_outcome(collector):
    mem, clock = collector
    for i in range(5):
        mem.record(f"t{i}", f"s{i}", "coder", "read_file", Outcome.FAILURE)
        clock.advance(1)
    mem.record("t5", "s5", "coder", "write_file", Outcome.SUCCESS)
    removed = mem.prune_by_outcome(Outcome.FAILURE, max_count=2)
    assert removed == 3
    assert [e.task_id for e in mem.all()] == ["t3", "t4", "t5"]
    assert mem.stats()["total"] == 3


def test_prune_by_outcome_untouched_when_under_limit(collector):
    mem, _clock = collector
    mem.record("t1", "s1", "coder", "read_file", Outcome.FAILURE)
    assert mem.prune_by_outcome(Outcome.FAILURE, max_count=1000) == 0
    assert len(mem) == 1


# --------------------------------------------------------------------------- #
# Phase 1.5 — advanced queries (newest first)
# --------------------------------------------------------------------------- #
def test_get_by_agent_filters_newest_first(collector):
    mem, _clock = collector
    fill(mem)
    coder = mem.get_by_agent("coder")
    assert [e.agent_role for e in coder] == ["coder", "coder", "coder"]
    assert [e.action for e in coder] == ["act-4", "act-2", "act-0"]  # newest first


def test_get_by_agent_unknown_role_returns_empty(collector):
    mem, _clock = collector
    fill(mem)
    assert mem.get_by_agent("reporter") == []


def test_get_by_action_filters(collector):
    mem, _clock = collector
    fill(mem)
    assert [e.action for e in mem.get_by_action("write_file")] == ["act-3", "act-0"]
    assert [e.action for e in mem.get_by_action("read_file")] == ["act-4", "act-2", "act-1"]


def test_get_by_outcome_filters(collector):
    mem, _clock = collector
    fill(mem)
    assert [e.action for e in mem.get_by_outcome(Outcome.FAILURE)] == ["act-3"]
    assert len(mem.get_by_outcome(Outcome.SUCCESS)) == 3


def test_queries_respect_limit(collector):
    mem, _clock = collector
    fill(mem)
    assert [e.action for e in mem.get_by_agent("coder", limit=2)] == ["act-4", "act-2"]
    assert len(mem.get_by_outcome(Outcome.SUCCESS, limit=2)) == 2


def test_search_with_custom_predicate(collector):
    mem, _clock = collector
    fill(mem)
    assert [e.action for e in mem.search(lambda e: e.result == "result-4")] == ["act-4"]
    failed = mem.search(lambda e: e.outcome is not Outcome.SUCCESS)
    assert [e.action for e in failed] == ["act-4", "act-3"]


def test_search_empty_buffer(collector):
    mem, _clock = collector
    assert mem.search(lambda e: True) == []


def test_queries_do_not_mutate_buffer(collector):
    mem, _clock = collector
    fill(mem)
    mem.get_by_agent("coder", limit=1)
    mem.search(lambda e: True)
    assert len(mem) == 5


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_records_all_persist(collector):
    mem, _clock = collector

    def worker(start: int) -> None:
        for i in range(50):
            mem.record(f"t{start}-{i}", f"s{start}-{i}", "coder", "read_file", Outcome.SUCCESS)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)
    assert len(mem) == 500
    assert mem.stats()["total"] == 500
    assert mem.stats()["success_rate"] == 1.0


def test_concurrent_reads_during_writes_do_not_crash(collector):
    mem, _clock = collector
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            mem.all()
            mem.get_by_agent("coder")
            mem.search(lambda e: True)
            mem.stats()

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for i in range(200):
        mem.record(f"t{i}", f"s{i}", "coder", "read_file", Outcome.SUCCESS)
    stop.set()
    for thread in threads:
        thread.join(timeout=30)
    assert len(mem) == 200