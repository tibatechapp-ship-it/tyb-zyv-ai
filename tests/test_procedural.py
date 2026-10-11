"""Tests for Phase 4 procedural memory — skills registry, track record, auto-learning."""

from __future__ import annotations

import pytest

from src.memory.base import connect
from src.memory.embeddings import HashingEmbedder
from src.memory.procedural import ProceduralMemory, Skill, SkillsRegistry, _slug


@pytest.fixture
def procedural():
    conn = connect(":memory:")
    store = ProceduralMemory(conn)
    try:
        yield store
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# basic registry
# --------------------------------------------------------------------------- #
def test_learn_get_and_typed_view(procedural):
    procedural.learn(
        "deploy", "ship to github pages", steps=["build", "push"],
        examples=["make deploy"], tags=["web", "github"],
    )
    record = procedural.get("deploy")
    assert record is not None and record.data["steps"] == ["build", "push"]
    skill = procedural.skill("deploy")
    assert isinstance(skill, Skill)
    assert skill.tags == ("web", "github")
    assert skill.success_rate == 0.0  # never used yet


def test_get_missing_is_none(procedural):
    assert procedural.get("nope") is None
    assert procedural.skill("nope") is None


def test_learn_replaces_but_keeps_track_record(procedural):
    first = procedural.learn("k", "old", steps=["a"])
    procedural.record_use("k", ok=True)
    second = procedural.learn("k", "new", steps=["b"])
    assert first == second
    assert procedural.get("k").data["description"] == "new"
    assert procedural.get("k").data["uses"] == 1
    assert procedural.count() == 1


def test_forget(procedural):
    procedural.learn("k", "d")
    assert procedural.forget("k") is True
    assert procedural.forget("k") is False


def test_all_is_sorted_by_name(procedural):
    procedural.learn("beta", "b")
    procedural.learn("alpha", "a")
    assert [r.data["name"] for r in procedural.all()] == ["alpha", "beta"]
    assert [s.name for s in procedural.all_skills()] == ["alpha", "beta"]


# --------------------------------------------------------------------------- #
# success track record
# --------------------------------------------------------------------------- #
def test_record_use_tracks_rate(procedural):
    procedural.learn("deploy", "ship")
    assert procedural.record_use("deploy", ok=True) == 1.0
    assert procedural.record_use("deploy", ok=False) == pytest.approx(0.5)
    record = procedural.get("deploy")
    assert record.data["uses"] == 2 and record.data["successes"] == 1
    assert procedural.skill("deploy").success_rate == pytest.approx(0.5)


def test_record_use_missing_skill_returns_none(procedural):
    assert procedural.record_use("ghost") is None


def test_top_skills_orders_by_rate_then_uses(procedural):
    procedural.learn("gamma", "never used")
    procedural.learn("beta", "half")
    procedural.learn("alpha", "always")
    procedural.learn("delta", "always the most")
    for _ in range(2):
        procedural.record_use("beta", ok=True)
    procedural.record_use("beta", ok=False)         # 2/3
    for _ in range(3):
        procedural.record_use("alpha", ok=True)     # 3/3
    for _ in range(5):
        procedural.record_use("delta", ok=True)     # 5/5
    names = [r.data["name"] for r in procedural.top_skills()]
    assert names == ["delta", "alpha", "beta", "gamma"]


# --------------------------------------------------------------------------- #
# auto-learning
# --------------------------------------------------------------------------- #
def test_slug_helper():
    assert _slug("Deploy The App!") == "deploy_the_app"
    assert _slug("") == "task"


def test_learn_from_task_creates_and_records(procedural):
    result = procedural.learn_from_task(
        "deploy the app to github", steps=["build", "push"], tags=["ops"]
    )
    assert result is not None
    assert result["name"] == "deploy_the_app"
    assert result["uses"] == 1 and result["successes"] == 1
    assert procedural.skill("deploy_the_app").steps == ("build", "push")


def test_learn_from_task_ignores_failures(procedural):
    assert procedural.learn_from_task("broke everything", ok=False) is None
    assert procedural.count() == 0


def test_learn_from_task_accumulates_uses(procedural):
    procedural.learn_from_task("deploy the app", name="deploy")
    procedural.learn_from_task("deploy the app", name="deploy")
    assert procedural.skill("deploy").uses == 2
    assert procedural.count() == 1


# --------------------------------------------------------------------------- #
# retrieval
# --------------------------------------------------------------------------- #
def test_find_lexical(procedural):
    procedural.learn("deploy", "ship to github pages", tags=["web", "github"])
    procedural.learn("email", "send a newsletter", tags=["mail"])
    found = procedural.find("github deploy")
    assert found and found[0].data["name"] == "deploy"


def test_find_with_embedder(procedural_embedder):
    procedural_embedder.learn("deploy", "ship the release to github pages", tags=["ops"])
    procedural_embedder.learn("lunch", "banana smoothie recipe", tags=["food"])
    found = procedural_embedder.find("deploying to github")
    assert found and found[0].data["name"] == "deploy"


@pytest.fixture
def procedural_embedder():
    conn = connect(":memory:")
    store = ProceduralMemory(conn, embedder=HashingEmbedder())
    try:
        yield store
    finally:
        conn.close()


def test_skills_registry_is_procedural_memory():
    assert SkillsRegistry is ProceduralMemory


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #
def test_migration_adds_track_record_columns():
    conn = connect(":memory:")
    conn.execute(
        """
        CREATE TABLE skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, iso TEXT NOT NULL,
            name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
            steps TEXT NOT NULL DEFAULT '[]', examples TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.commit()
    store = ProceduralMemory(conn)
    columns = {str(row["name"]) for row in store._query("PRAGMA table_info(skills)")}
    assert {"uses", "successes", "embedding"} <= columns
    store.learn("k", "d")
    assert store.record_use("k", ok=True) == 1.0
    conn.close()
