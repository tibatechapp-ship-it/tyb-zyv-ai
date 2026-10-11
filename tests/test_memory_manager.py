"""Tests for :class:`MemoryManager` — sessions, context, export/import, locking.

A ``FakeClock`` makes every timestamp deterministic; databases live in ``tmp_path``
so the on-disk path (and its parent-directory creation) is exercised too.
"""

from __future__ import annotations

import json
import threading

import pytest

from src.memory import MemoryManager

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
def manager(tmp_path):
    clock = FakeClock()
    mem = MemoryManager(tmp_path / "memory.db", clock=clock)
    try:
        yield mem, clock
    finally:
        mem.close()


# --------------------------------------------------------------------------- #
# construction & lifetime
# --------------------------------------------------------------------------- #
def test_manager_creates_database_file(tmp_path):
    db = tmp_path / "deep" / "nested" / "memory.db"
    with MemoryManager(db) as mem:
        mem.semantic.remember("k", "v")
    assert db.exists()


def test_manager_path_and_connection(manager):
    mem, _ = manager
    assert mem.path.endswith("memory.db")
    assert mem.connection is not None


def test_manager_default_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZYVO_MEMORY_DB", str(tmp_path / "env.db"))
    with MemoryManager() as mem:
        mem.semantic.remember("k", "v")
    assert (tmp_path / "env.db").exists()


def test_manager_in_memory_path():
    with MemoryManager(":memory:") as mem:
        mem.semantic.remember("k", "v")
        assert mem.semantic.recall("k") == "v"


def test_manager_close_is_idempotent(manager):
    mem, _ = manager
    mem.close()
    mem.close()


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def test_start_session_returns_id_and_becomes_current(manager):
    mem, _ = manager
    sid = mem.start_session("s1")
    assert sid == "s1"
    assert mem.current_session() == "s1"


def test_start_session_autogenerates_id(manager):
    mem, _ = manager
    sid = mem.start_session()
    assert sid and mem.current_session() == sid


def test_end_session_clears_current(manager):
    mem, _ = manager
    mem.start_session("s1")
    assert mem.end_session(summary="done") == "s1"
    assert mem.current_session() is None
    assert mem.session("s1")["summary"] == "done"


def test_end_session_without_active_returns_none(manager):
    mem, _ = manager
    assert mem.end_session() is None


def test_session_records_timestamps(manager):
    mem, clock = manager
    mem.start_session("s1")
    clock.advance(120)
    mem.end_session(summary="ok")
    record = mem.session("s1")
    assert record["ended_ts"] - record["started_ts"] == 120


def test_sessions_listed_newest_first(manager):
    mem, clock = manager
    mem.start_session("old")
    clock.advance(10)
    mem.start_session("new")
    assert [s["id"] for s in mem.sessions()] == ["new", "old"]


def test_missing_session_is_none(manager):
    mem, _ = manager
    assert mem.session("ghost") is None


# --------------------------------------------------------------------------- #
# recording
# --------------------------------------------------------------------------- #
def test_remember_task_tags_current_session(manager):
    mem, _ = manager
    mem.start_session("s1")
    mem.remember_task("Planner", "plan", "ok", duration_s=1.0)
    episode = mem.episodic.last()
    assert episode.data["session_id"] == "s1"
    assert episode.data["agent"] == "Planner"


def test_remember_task_without_session_is_untagged(manager):
    mem, _ = manager
    mem.remember_task("Planner", "plan", "ok")
    assert mem.episodic.last().data["session_id"] is None


# --------------------------------------------------------------------------- #
# context bundle
# --------------------------------------------------------------------------- #
def test_context_includes_active_preferences(manager):
    mem, _ = manager
    mem.emotional.set_preference("tone", "arabizi")
    bundle = mem.context()
    assert bundle["preferences"][0]["data"]["value"] == "arabizi"


def test_context_without_query_uses_recent_and_firsts(manager):
    mem, _ = manager
    mem.remember_task("A", "t", "r")
    mem.semantic.remember("k", "v")
    bundle = mem.context()
    assert bundle["recent"] and bundle["facts"]


def test_context_with_query_ranks_matches(manager):
    mem, _ = manager
    mem.semantic.remember("site", "github pages")
    mem.semantic.remember("editor", "vim")
    mem.procedural.learn("deploy", "ship to github", tags=["github"])
    bundle = mem.context("github deploy")
    assert bundle["facts"][0]["data"]["key"] == "site"
    assert bundle["skills"][0]["data"]["name"] == "deploy"


def test_context_reports_average_satisfaction(manager):
    mem, _ = manager
    mem.emotional.record_satisfaction(0.8)
    assert mem.context()["satisfaction"] == pytest.approx(0.8)


def test_context_text_renders_memories(manager):
    mem, _ = manager
    mem.start_session("s1")
    mem.remember_task("Planner", "plan the day", "done")
    mem.semantic.remember("tone", "arabizi")
    mem.procedural.learn("deploy", "ship it")
    mem.emotional.set_preference("lang", "arabizi")
    mem.emotional.record_satisfaction(0.8)
    text = mem.context_text()
    assert "session: s1" in text
    assert "[ok] plan the day" in text
    assert "tone: arabizi" in text
    assert "+ skill deploy" in text
    assert "lang = arabizi" in text
    assert "satisfaction: 0.80" in text


def test_context_text_marks_failures(manager):
    mem, _ = manager
    mem.remember_task("Coder", "build", "boom", ok=False)
    assert "[fail] build" in mem.context_text()


# --------------------------------------------------------------------------- #
# stats / snapshot / export / import
# --------------------------------------------------------------------------- #
def test_stats_counts_every_store(manager):
    mem, _ = manager
    mem.start_session("s1")
    mem.remember_task("A", "t", "r")
    mem.semantic.remember("k", "v")
    mem.procedural.learn("skill", "d")
    mem.emotional.set_preference("p", "1")
    assert mem.stats() == {
        "sessions": 1,
        "episodes": 1,
        "facts": 1,
        "skills": 1,
        "preferences": 1,
    }


def test_snapshot_has_all_tables(manager):
    mem, _ = manager
    mem.start_session("s1")
    mem.remember_task("A", "t", "r")
    mem.semantic.remember("k", "v")
    mem.procedural.learn("s", "d")
    mem.emotional.set_preference("p", "1")
    mem.emotional.record_signal("good")
    mem.emotional.record_satisfaction(0.9)
    snap = mem.snapshot()
    assert set(snap) == {
        "version",
        "sessions",
        "episodes",
        "facts",
        "skills",
        "preferences",
        "signals",
        "satisfaction",
    }
    assert snap["version"] == 1
    assert len(snap["episodes"]) == 1


def test_export_json_writes_valid_json(manager, tmp_path):
    mem, _ = manager
    mem.remember_task("A", "t", "r")
    out = tmp_path / "dump" / "memory.json"
    size = mem.export_json(out)
    assert size > 0
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == 1


def test_export_import_round_trip(tmp_path):
    source = MemoryManager(tmp_path / "a.db", clock=FakeClock())
    source.start_session("s1")
    source.remember_task("Planner", "plan", "done", ok=True, duration_s=2.5)
    source.semantic.remember("user_name", "tyba", subject="user")
    source.procedural.learn("deploy", "ship", steps=["build"], tags=["web"])
    source.emotional.set_preference("tone", "arabizi")
    source.emotional.record_signal("good work")
    source.emotional.record_satisfaction(0.75, note="nice")
    dump = tmp_path / "dump.json"
    source.export_json(dump)
    source.close()

    restored = MemoryManager(tmp_path / "b.db", clock=FakeClock())
    counts = restored.import_json(dump)
    assert counts == {
        "sessions": 1,
        "episodes": 1,
        "facts": 1,
        "skills": 1,
        "preferences": 1,
        "signals": 1,
        "satisfaction": 1,
    }
    assert restored.semantic.recall("user_name", subject="user") == "tyba"
    assert restored.procedural.get("deploy").data["steps"] == ["build"]
    assert restored.emotional.preference("tone") == "arabizi"
    assert restored.episodic.last().data["agent"] == "Planner"
    assert restored.stats() == {
        "sessions": 1,
        "episodes": 1,
        "facts": 1,
        "skills": 1,
        "preferences": 1,
    }
    restored.close()


def test_import_is_idempotent(tmp_path):
    source = MemoryManager(tmp_path / "a.db", clock=FakeClock())
    source.semantic.remember("k", "v")
    source.remember_task("A", "t", "r")
    dump = tmp_path / "dump.json"
    source.export_json(dump)
    source.close()

    restored = MemoryManager(tmp_path / "b.db", clock=FakeClock())
    restored.import_json(dump)
    restored.import_json(dump)
    assert restored.semantic.count() == 1
    assert restored.episodic.count() == 1
    restored.close()


def test_import_of_empty_snapshot_returns_zero_counts(tmp_path):
    dump = tmp_path / "empty.json"
    dump.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with MemoryManager(tmp_path / "b.db") as mem:
        counts = mem.import_json(dump)
    assert set(counts.values()) == {0}


# --------------------------------------------------------------------------- #
# embeddings (Phase 2)
# --------------------------------------------------------------------------- #
def test_manager_accepts_named_embedder(tmp_path):
    with MemoryManager(tmp_path / "e.db", embedder="hashing") as mem:
        mem.semantic.remember("deploy", "ship to github pages", subject="ops")
        mem.semantic.remember("lunch", "banana smoothie recipe", subject="food")
        found = mem.semantic.search("deploying to github")
        assert found and found[0].data["key"] == "deploy"


def test_manager_import_recomputes_embeddings(tmp_path):
    source = MemoryManager(tmp_path / "a.db", clock=FakeClock())
    source.semantic.remember("user_name", "tyba", subject="user")
    dump = tmp_path / "dump.json"
    source.export_json(dump)
    source.close()

    with MemoryManager(tmp_path / "b.db", embedder="hashing") as mem:
        mem.import_json(dump)
        row = mem.connection.execute(
            "SELECT embedding FROM facts WHERE key = 'user_name'"
        ).fetchone()
    assert row is not None and row["embedding"] is not None


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_episode_writes_all_persist(tmp_path):
    mem = MemoryManager(tmp_path / "race.db")
    try:
        def worker(start: int) -> None:
            for i in range(20):
                mem.episodic.record("A", f"t{start}-{i}", "r")

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads)
        assert mem.episodic.count() == 100
    finally:
        mem.close()
