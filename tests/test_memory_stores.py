"""Unit tests for the four memory stores (Phase 1).

Each store is exercised against an in-memory database with injected timestamps,
so ordering and "activity" buckets are deterministic and no real clock is used.
"""

from __future__ import annotations

import pytest

from src.memory.base import (
    MemoryKind,
    MemoryRecord,
    connect,
    default_memory_path,
    utc_iso,
)
from src.memory.embeddings import HashingEmbedder, cosine
from src.memory.emotional import EmotionalMemory, analyze_tone
from src.memory.episodic import EpisodicMemory
from src.memory.procedural import ProceduralMemory
from src.memory.semantic import SemanticMemory, _lexical_score

DAY = 86_400.0
T0 = 1_700_000_000.0


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def fake_embedder(text: str) -> list[float]:
    """Deterministic bag-of-words embedder over a tiny fixed vocabulary."""
    vocab = ["github", "deploy", "arabic", "name", "code", "tone"]
    lowered = text.lower()
    return [1.0 if word in lowered else 0.0 for word in vocab]


@pytest.fixture
def connection():
    conn = connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# base
# --------------------------------------------------------------------------- #
def test_memory_record_to_dict_flattens_kind():
    record = MemoryRecord(id=7, kind=MemoryKind.SEMANTIC, ts=1.5, iso="x", data={"a": 1})
    assert record.to_dict() == {
        "id": 7,
        "kind": "semantic",
        "ts": 1.5,
        "iso": "x",
        "data": {"a": 1},
    }


def test_utc_iso_round_trips():
    assert utc_iso(T0) == "2023-11-14T22:13:20+00:00"


def test_default_memory_path_uses_env(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("ZYVO_MEMORY_DB", str(target))
    assert default_memory_path() == target


def test_default_memory_path_fallback(monkeypatch):
    monkeypatch.delenv("ZYVO_MEMORY_DB", raising=False)
    assert default_memory_path().name == "memory.db"


def test_connect_creates_parent_directory(tmp_path):
    db = tmp_path / "nested" / "dir" / "memory.db"
    conn = connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.close()
    assert db.exists()


# --------------------------------------------------------------------------- #
# episodic
# --------------------------------------------------------------------------- #
@pytest.fixture
def episodic(connection):
    return EpisodicMemory(connection)


def test_episodic_record_returns_increasing_ids(episodic):
    first = episodic.record("A", "t1", "r1", timestamp=T0)
    second = episodic.record("A", "t2", "r2", timestamp=T0 + 1)
    assert second > first


def test_episodic_recent_is_newest_first(episodic):
    episodic.record("A", "old", "r", timestamp=T0)
    episodic.record("A", "new", "r", timestamp=T0 + 5)
    assert [r.data["task"] for r in episodic.recent()] == ["new", "old"]


def test_episodic_last_empty_then_filled(episodic):
    assert episodic.last() is None
    episodic.record("A", "only", "r", timestamp=T0)
    last = episodic.last()
    assert last is not None and last.data["task"] == "only"


def test_episodic_between_is_inclusive(episodic):
    episodic.record("A", "early", "r", timestamp=T0)
    episodic.record("A", "mid", "r", timestamp=T0 + 10)
    episodic.record("A", "late", "r", timestamp=T0 + 20)
    found = episodic.between(T0, T0 + 10)
    assert [r.data["task"] for r in found] == ["early", "mid"]


def test_episodic_between_honours_limit(episodic):
    for i in range(5):
        episodic.record("A", f"t{i}", "r", timestamp=T0 + i)
    assert len(episodic.between(T0, T0 + 10, limit=2)) == 2


def test_episodic_for_agent_filters(episodic):
    episodic.record("A", "a-task", "r", timestamp=T0)
    episodic.record("B", "b-task", "r", timestamp=T0 + 1)
    assert [r.data["task"] for r in episodic.for_agent("A")] == ["a-task"]


def test_episodic_for_session_orders_oldest_first(episodic):
    episodic.record("A", "first", "r", session_id="s1", timestamp=T0)
    episodic.record("A", "second", "r", session_id="s1", timestamp=T0 + 1)
    episodic.record("A", "other", "r", session_id="s2", timestamp=T0 + 2)
    assert [r.data["task"] for r in episodic.for_session("s1")] == ["first", "second"]


def test_episodic_for_session_honours_limit(episodic):
    for i in range(5):
        episodic.record("A", f"t{i}", "r", session_id="s1", timestamp=T0 + i)
    assert len(episodic.for_session("s1", limit=2)) == 2


def test_store_exposes_shared_connection(episodic, connection):
    assert episodic.connection is connection


def test_episodic_search_matches_result_text(episodic):
    episodic.record("A", "task one", "deploy succeeded", timestamp=T0)
    episodic.record("A", "other", "nothing here", timestamp=T0 + 1)
    assert [r.data["task"] for r in episodic.search("deploy")] == ["task one"]


def test_episodic_activity_buckets_by_day(episodic):
    episodic.record("A", "d1a", "r", timestamp=T0)
    episodic.record("A", "d1b", "r", timestamp=T0 + 60)
    episodic.record("A", "d2", "r", timestamp=T0 + DAY)
    assert episodic.activity() == {"2023-11-14": 2, "2023-11-15": 1}


def test_episodic_count_clear_and_len(episodic):
    episodic.record("A", "t", "r", timestamp=T0)
    assert episodic.count() == 1
    assert len(episodic) == 1
    episodic.clear()
    assert episodic.count() == 0


def test_episodic_ok_flag_round_trips(episodic):
    episodic.record("A", "t", "r", ok=False, timestamp=T0)
    assert episodic.last().data["ok"] is False


# --------------------------------------------------------------------------- #
# semantic
# --------------------------------------------------------------------------- #
@pytest.fixture
def semantic(connection):
    return SemanticMemory(connection)


def test_semantic_remember_and_recall(semantic):
    semantic.remember("user_name", "tyba", subject="user")
    assert semantic.recall("user_name", subject="user") == "tyba"


def test_semantic_recall_missing_is_none(semantic):
    assert semantic.recall("nope") is None


def test_semantic_remember_updates_existing(semantic):
    first = semantic.remember("k", "v1", subject="s")
    second = semantic.remember("k", "v2", subject="s")
    assert first == second
    assert semantic.recall("k", subject="s") == "v2"
    assert semantic.count() == 1


def test_semantic_subject_scopes_same_key(semantic):
    semantic.remember("version", "1.0", subject="alpha")
    semantic.remember("version", "2.0", subject="beta")
    assert semantic.recall("version", subject="alpha") == "1.0"
    assert semantic.recall("version", subject="beta") == "2.0"


def test_semantic_forget(semantic):
    semantic.remember("k", "v")
    assert semantic.forget("k") is True
    assert semantic.forget("k") is False


def test_semantic_all_is_sorted(semantic):
    semantic.remember("b", "v", subject="s")
    semantic.remember("a", "v", subject="s")
    assert [r.data["key"] for r in semantic.all()] == ["a", "b"]


def test_semantic_lexical_search_ranks_by_overlap(semantic):
    semantic.remember("lang", "arabic", subject="user")
    semantic.remember("editor", "vim", subject="tools")
    found = semantic.search("arabic language")
    assert found and found[0].data["key"] == "lang"


def test_semantic_search_empty_query_returns_nothing(semantic):
    semantic.remember("k", "v")
    assert semantic.search("") == []


def test_semantic_vector_search_prefers_embedding_match(connection):
    store = SemanticMemory(connection, embedder=fake_embedder)
    store.remember("site", "github pages", subject="deploy")
    store.remember("editor", "vim", subject="tools")
    found = store.search("github deploy")
    assert found and found[0].data["key"] == "site"


def test_semantic_vector_search_falls_back_for_unembedded_rows(connection):
    store = SemanticMemory(connection, embedder=fake_embedder)
    store.remember("site", "github pages", subject="deploy")
    connection.execute("UPDATE facts SET embedding = NULL")
    connection.commit()
    found = store.search("github deploy")
    assert found and found[0].data["key"] == "site"


def test_semantic_remember_without_embedder_leaves_embedding_null(semantic):
    semantic.remember("k", "v")
    row = semantic._query_one("SELECT embedding FROM facts WHERE key = ?", ("k",))
    assert row is not None and row["embedding"] is None


def test_semantic_top_k_is_alias_for_search(semantic):
    semantic.remember("site", "github pages", subject="ops")
    semantic.remember("editor", "vim", subject="tools")
    assert [r.data["key"] for r in semantic.top_k("github", k=1)] == ["site"]


def test_semantic_hashing_embedder_ranks_fuzzy_match(connection):
    store = SemanticMemory(connection, embedder=HashingEmbedder())
    store.remember("deploy", "ship the release to github pages", subject="ops")
    store.remember("lunch", "banana smoothie recipe", subject="food")
    found = store.search("deploying the release to github")
    assert found and found[0].data["key"] == "deploy"


# --------------------------------------------------------------------------- #
# lexical / cosine helpers
# --------------------------------------------------------------------------- #
def test_lexical_score_full_and_partial():
    assert _lexical_score("deploy github", "deploy to github pages") == 1.0
    assert _lexical_score("deploy zzz", "deploy to github") == 0.5


def test_lexical_score_empty_query():
    assert _lexical_score("", "anything") == 0.0


def test_lexical_score_ignores_single_character_tokens():
    assert _lexical_score("a b", "a b") == 0.0


def test_cosine_matches_and_mismatches():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0], [1.0, 2.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --------------------------------------------------------------------------- #
# procedural
# --------------------------------------------------------------------------- #
@pytest.fixture
def procedural(connection):
    return ProceduralMemory(connection)


def test_procedural_learn_and_get(procedural):
    procedural.learn(
        "deploy", "ship it", steps=["build", "push"], examples=["ex"], tags=["web"]
    )
    got = procedural.get("deploy")
    assert got is not None
    assert got.data["steps"] == ["build", "push"]
    assert got.data["tags"] == ["web"]


def test_procedural_get_missing_is_none(procedural):
    assert procedural.get("nope") is None


def test_procedural_learn_replaces_existing(procedural):
    first = procedural.learn("k", "old", steps=["a"])
    second = procedural.learn("k", "new", steps=["b"])
    assert first == second
    assert procedural.get("k").data["description"] == "new"
    assert procedural.count() == 1


def test_procedural_forget(procedural):
    procedural.learn("k", "d")
    assert procedural.forget("k") is True
    assert procedural.forget("k") is False


def test_procedural_all_sorted_by_name(procedural):
    procedural.learn("beta", "b")
    procedural.learn("alpha", "a")
    assert [r.data["name"] for r in procedural.all()] == ["alpha", "beta"]


def test_procedural_find_ranks_by_context(procedural):
    procedural.learn("deploy", "ship to github pages", tags=["web", "github"])
    procedural.learn("email", "send a newsletter", tags=["mail"])
    found = procedural.find("github deploy")
    assert found and found[0].data["name"] == "deploy"


# --------------------------------------------------------------------------- #
# emotional
# --------------------------------------------------------------------------- #
@pytest.fixture
def emotional(connection):
    return EmotionalMemory(connection)


@pytest.mark.parametrize(
    ("text", "label", "score"),
    [
        ("this is great, shukran", "positive", 1.0),
        ("bad and broken", "negative", -1.0),
        ("just a plain sentence", "neutral", 0.0),
        ("", "neutral", 0.0),
        ("good but also bad", "neutral", 0.0),
    ],
)
def test_analyze_tone(text, label, score):
    assert analyze_tone(text) == (label, score)


def test_emotional_set_and_get_preference(emotional):
    emotional.set_preference("tone", "arabizi")
    assert emotional.preference("tone") == "arabizi"
    assert emotional.preference("missing") is None


def test_emotional_set_preference_updates(emotional):
    emotional.set_preference("tone", "formal")
    emotional.set_preference("tone", "arabizi", confidence=0.5)
    assert emotional.preference("tone") == "arabizi"
    assert emotional.count() == 1


def test_emotional_preferences_sorted(emotional):
    emotional.set_preference("b", "1")
    emotional.set_preference("a", "2")
    assert [r.data["key"] for r in emotional.preferences()] == ["a", "b"]


def test_emotional_record_signal_autoclassifies(emotional):
    emotional.record_signal("awesome work", session_id="s1")
    signal = emotional.signals()[0]
    assert signal.data["sentiment"] == "positive"
    assert signal.data["session_id"] == "s1"


def test_emotional_record_signal_accepts_explicit_label(emotional):
    emotional.record_signal("??", sentiment="negative", score=-0.5)
    assert emotional.signals()[0].data["sentiment"] == "negative"


def test_emotional_observe_logs_and_returns(emotional):
    assert emotional.observe("thanks, perfect") == ("positive", 1.0)
    assert emotional.signals()[0].data["score"] == 1.0


def test_emotional_satisfaction_is_clamped(emotional):
    emotional.record_satisfaction(5.0)
    emotional.record_satisfaction(-3.0)
    scores = sorted(r.data["score"] for r in emotional.satisfaction())
    assert scores == [0.0, 1.0]


def test_emotional_average_satisfaction_empty_and_filled(emotional):
    assert emotional.average_satisfaction() is None
    emotional.record_satisfaction(0.5)
    emotional.record_satisfaction(1.0)
    assert emotional.average_satisfaction() == pytest.approx(0.75)


def test_emotional_all_signals_and_satisfaction_oldest_first(emotional):
    emotional.record_signal("bad", timestamp=T0)
    emotional.record_signal("good", timestamp=T0 + 1)
    emotional.record_satisfaction(0.1, timestamp=T0)
    emotional.record_satisfaction(0.9, timestamp=T0 + 1)
    assert [r.data["sentiment"] for r in emotional.all_signals()] == ["negative", "positive"]
    assert [r.data["score"] for r in emotional.all_satisfaction()] == [0.1, 0.9]
