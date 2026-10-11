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


def test_manager_import_recomputes_skill_embeddings(tmp_path):
    source = MemoryManager(tmp_path / "a.db", clock=FakeClock())
    source.procedural.learn("deploy", "ship to github pages", tags=["web"])
    dump = tmp_path / "dump.json"
    source.export_json(dump)
    source.close()

    with MemoryManager(tmp_path / "b.db", embedder="hashing") as mem:
        mem.import_json(dump)
        row = mem.connection.execute(
            "SELECT embedding FROM skills WHERE name = 'deploy'"
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


# --------------------------------------------------------------------------- #
# unified remember / recall / forget
# --------------------------------------------------------------------------- #
def test_remember_auto_infers_kind_from_arguments(manager):
    mem, _clock = manager
    assert mem.remember("plain fact about lunch")["kind"] == "semantic"
    assert mem.remember("deploy the app", task="deploy the app")["kind"] == "episodic"
    assert mem.remember("cut a release", steps=["tag", "push"])["kind"] == "procedural"
    assert mem.semantic.count() == 1
    assert mem.episodic.count() == 1
    assert mem.procedural.count() == 1


def test_remember_accepts_kind_synonyms(manager):
    mem, _clock = manager
    assert mem.remember("k1", kind="fact")["kind"] == "semantic"
    assert mem.remember("k2", kind="skill")["kind"] == "procedural"
    assert mem.remember("k3", kind="preference")["kind"] == "emotional"
    assert mem.remember("k4", kind="episode", task="do it")["kind"] == "episodic"


def test_remember_explicit_kind_overrides_inference(manager):
    mem, _clock = manager
    out = mem.remember("not a skill", kind="semantic", steps=["x"])
    assert out["kind"] == "semantic"
    assert mem.procedural.count() == 0


def test_recall_auto_picks_store_for_how_to(manager):
    mem, _clock = manager
    mem.remember("ship to github pages", steps=["build", "push"])
    assert mem.recall("how to ship to github pages")["kind"] == "procedural"


def test_recall_auto_picks_store_for_history(manager):
    mem, _clock = manager
    mem.remember("build the release", task="build the release")
    assert mem.recall("what did we do yesterday")["kind"] == "episodic"


def test_recall_auto_picks_store_for_preference(manager):
    mem, _clock = manager
    mem.emotional.set_preference("reply_style", "concise")
    assert mem.recall("do you know my preference")["kind"] == "emotional"


def test_recall_auto_defaults_to_semantic(manager):
    mem, _clock = manager
    mem.semantic.remember("deploy", "ship to github pages", subject="ops")
    answer = mem.recall("deploying to github")
    assert answer["kind"] == "semantic"
    assert answer["results"] and answer["results"][0]["data"]["key"] == "deploy"


def test_recall_explicit_kind(manager):
    mem, _clock = manager
    mem.remember("build the release", task="build the release")
    answer = mem.recall("build", kind="episodic")
    assert answer["kind"] == "episodic"
    assert answer["results"]


def test_recall_emotional_falls_back_to_all_preferences(manager):
    mem, _clock = manager
    mem.emotional.set_preference("reply_style", "concise")
    answer = mem.recall("what do you prefer totally unrelated", kind="emotional")
    assert answer["kind"] == "emotional"
    assert answer["results"]


def test_forget_removes_from_both_default_stores(manager):
    mem, _clock = manager
    mem.semantic.remember("deploy", "ship it", subject="ops")
    mem.procedural.learn("deploy", "ship the app")
    assert mem.forget("deploy") == 2
    assert mem.semantic.count() == 0
    assert mem.procedural.count() == 0


def test_forget_scoped_to_one_store(manager):
    mem, _clock = manager
    mem.semantic.remember("deploy", "ship it", subject="ops")
    mem.procedural.learn("deploy", "ship the app")
    assert mem.forget("deploy", kind="semantic", subject="ops") == 1
    assert mem.semantic.count() == 0
    assert mem.procedural.count() == 1


def test_forget_scoped_to_procedural_only(manager):
    mem, _clock = manager
    mem.semantic.remember("deploy", "ship it", subject="ops")
    mem.procedural.learn("deploy", "ship the app")
    assert mem.forget("deploy", kind="skill") == 1     # synonym accepted
    assert mem.semantic.count() == 1
    assert mem.procedural.count() == 0
    assert mem.forget("deploy", kind="procedural") == 0  # already gone


def test_forget_missing_target_returns_zero(manager):
    mem, _clock = manager
    assert mem.forget("nothing-here") == 0


# --------------------------------------------------------------------------- #
# retention & compression
# --------------------------------------------------------------------------- #
def test_retention_max_age_days(manager):
    mem, clock = manager
    mem.episodic.record("A", "old", "r")
    clock.advance(40 * 86_400)
    mem.episodic.record("A", "fresh", "r")
    removed = mem.retention(max_age_days=30)
    assert removed["episodes_removed"] == 1
    assert [rec.data["task"] for rec in mem.episodic.all()] == ["fresh"]


def test_retention_max_episodes_keeps_newest(manager):
    mem, clock = manager
    for i in range(5):
        mem.episodic.record("A", f"t{i}", "r")
        clock.advance(60)
    removed = mem.retention(max_episodes=2)
    assert removed["episodes_removed"] == 3
    assert [rec.data["task"] for rec in mem.episodic.all()] == ["t3", "t4"]


def test_compress_rolls_episodes_into_summaries(manager):
    mem, clock = manager
    mem.episodic.record("A", "old-1", "r", duration_s=0.5)
    mem.episodic.record("A", "old-2", "r", ok=False, duration_s=0.5)
    clock.advance(40 * 86_400)
    mem.episodic.record("A", "fresh", "r")
    out = mem.compress(older_than_days=30)
    assert out["episodes_removed"] == 2
    assert out["summaries"] == 1
    assert mem.episodic.count() == 1
    rows = mem.summaries()
    assert len(rows) == 1
    assert rows[0]["agent"] == "A"
    assert rows[0]["count"] == 2
    assert rows[0]["errors"] == 1
    assert rows[0]["duration_total"] == pytest.approx(1.0)


def test_compress_accumulates_across_runs(manager):
    mem, clock = manager
    # Two episodes on the SAME calendar day (T0 is mid-day) straddling the cutoff:
    # the first compress rolls only batch-1, the second folds batch-2 into the
    # same (day, agent) summary row -> counts must ADD, not overwrite.
    mem.episodic.record("A", "batch-1", "r")     # T0
    clock.advance(3_600)                          # +1h, still the same UTC day
    mem.episodic.record("A", "batch-2", "r")
    clock.advance(30 * 86_400)                    # cutoff lands between the two
    mem.compress(older_than_days=30)
    assert mem.summaries()[0]["count"] == 1
    clock.advance(3_600)                          # cutoff now past batch-2
    mem.compress(older_than_days=30)
    rows = mem.summaries()
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["agent"] == "A"


def test_export_load_aliases_round_trip(tmp_path, manager):
    mem, _clock = manager
    mem.remember("ship it", kind="semantic", subject="ops", key="deploy")
    mem.remember("do the release", task="do the release", kind="episodic")
    dump = tmp_path / "dump.json"
    assert mem.export(dump) > 0

    with MemoryManager(tmp_path / "fresh.db") as clone:
        counts = clone.load(dump)
        assert counts["facts"] == 1
        assert counts["episodes"] == 1
