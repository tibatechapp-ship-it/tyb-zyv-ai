"""Tests for Phase 3 — LessonStore, render_lessons and LessonInjector.

Deterministic: no real LLM, no network, no clock (lessons carry explicit
``created_at`` values). Agents are driven with the mock backends in
``tests/deep_helpers.py``.
"""

from __future__ import annotations

import json
import threading

import pytest

from src.agents.coder import CoderAgent
from src.learning import (
    Lesson,
    LessonInjector,
    LessonStore,
    Outcome,
    render_lessons,
)

from deep_helpers import RecordingBackend


def make_lesson(
    *,
    lid: str = "id-1",
    principle: str = "When deploying at night, always run the test suite first",
    context: str = "",
    confidence: float = 0.5,
    evidence: tuple[str, ...] = ("task-1:step-1",),
    source_outcome: Outcome = Outcome.FAILURE,
    created_at: float = 1.0,
    agent_role: str = "coder",
    tags: frozenset[str] = frozenset(),
) -> Lesson:
    return Lesson(
        id=lid,
        principle=principle,
        context=context,
        confidence=confidence,
        evidence=evidence,
        source_outcome=source_outcome,
        created_at=created_at,
        agent_role=agent_role,
        tags=tags,
    )


# ── mutation / reading ───────────────────────────────────────────────────
def test_add_len_get_clear():
    store = LessonStore()
    lesson = make_lesson()
    store.add(lesson)

    assert len(store) == 1
    assert store.get("id-1") is lesson
    assert store.get("missing") is None

    store.clear()
    assert len(store) == 0
    assert store.all() == []


def test_add_upserts_by_id_and_preserves_position():
    store = LessonStore()
    first = make_lesson(lid="a", principle="alpha", created_at=1.0)
    second = make_lesson(lid="b", principle="beta", created_at=2.0)
    store.add(first)
    store.add(second)

    replacement = make_lesson(lid="a", principle="alpha-v2", created_at=1.0)
    store.add(replacement)

    assert len(store) == 2
    assert store.get("a") is replacement
    # 'a' keeps slot 0 (newest-first ordering still puts 'b' first by time).
    assert [lesson.id for lesson in store.all()] == ["b", "a"]


def test_add_many_and_remove():
    store = LessonStore()
    added = store.add_many([make_lesson(lid="a"), make_lesson(lid="b")])
    assert added == 2

    assert store.remove("a") is True
    assert store.remove("a") is False
    assert len(store) == 1


def test_all_is_newest_first_and_honours_limit():
    store = LessonStore()
    store.add_many(
        [
            make_lesson(lid="old", created_at=1.0),
            make_lesson(lid="mid", created_at=2.0),
            make_lesson(lid="new", created_at=3.0),
        ]
    )
    assert [lesson.id for lesson in store.all()] == ["new", "mid", "old"]
    assert [lesson.id for lesson in store.all(limit=2)] == ["new", "mid"]


# ── queries ──────────────────────────────────────────────────────────────
def test_by_agent_tag_outcome_and_search():
    store = LessonStore()
    store.add(make_lesson(lid="c", agent_role="coder", tags=frozenset({"deploy"})))
    store.add(
        make_lesson(
            lid="r",
            agent_role="reviewer",
            tags=frozenset({"style"}),
            source_outcome=Outcome.SUCCESS,
            created_at=2.0,
        )
    )

    assert [lesson.id for lesson in store.by_agent("coder")] == ["c"]
    assert [lesson.id for lesson in store.by_tag("style")] == ["r"]
    assert [lesson.id for lesson in store.by_outcome(Outcome.SUCCESS)] == ["r"]
    assert [lesson.id for lesson in store.search(lambda item: item.id != "c")] == ["r"]
    assert store.by_agent("coder", limit=0) == []


def test_top_ranks_by_confidence_then_recency():
    store = LessonStore()
    store.add(make_lesson(lid="low", confidence=0.2, created_at=9.0))
    store.add(make_lesson(lid="high", confidence=0.9, created_at=1.0))
    store.add(make_lesson(lid="mid", confidence=0.5, created_at=5.0))

    assert [lesson.id for lesson in store.top(2)] == ["high", "mid"]
    assert [lesson.id for lesson in store.top()] == ["high", "mid", "low"]


# ── relevance ────────────────────────────────────────────────────────────
def test_relevant_ranks_by_lexical_overlap():
    store = LessonStore()
    store.add(make_lesson(lid="deploy", principle="always run the test suite before deploy"))
    store.add(make_lesson(lid="style", principle="keep functions small and readable"))

    ranked = store.relevant("deploy the suite", limit=5)
    assert ranked and ranked[0].id == "deploy"
    # The style lesson shares no tokens -> not a candidate.
    assert all(lesson.id != "style" for lesson in ranked)


def test_relevant_agent_role_bonus_breaks_ties():
    store = LessonStore()
    store.add(make_lesson(lid="coder", agent_role="coder", principle="shared wording here"))
    store.add(make_lesson(lid="other", agent_role="reviewer", principle="shared wording here"))

    ranked = store.relevant("shared wording", agent_role="coder", limit=5)
    assert ranked[0].id == "coder"


def test_relevant_tag_and_min_confidence_filters():
    store = LessonStore()
    store.add(
        make_lesson(
            lid="deploy", principle="run the suite", tags=frozenset({"deploy"}), confidence=0.9
        )
    )
    store.add(make_lesson(lid="weak", principle="unrelated rule", confidence=0.1))

    tagged = store.relevant("", tags=["deploy"], limit=5)
    assert [lesson.id for lesson in tagged] == ["deploy"]

    confident = store.relevant("", min_confidence=0.5, limit=5)
    assert [lesson.id for lesson in confident] == ["deploy"]


def test_relevant_blank_query_falls_back_to_confidence_order():
    store = LessonStore()
    store.add(make_lesson(lid="low", confidence=0.2, created_at=5.0))
    store.add(make_lesson(lid="high", confidence=0.8, created_at=1.0))

    ranked = store.relevant("", limit=5)
    assert [lesson.id for lesson in ranked] == ["high", "low"]


def test_relevant_empty_store_is_empty():
    assert LessonStore().relevant("anything") == []


# ── rendering / build_context ────────────────────────────────────────────
def test_render_lessons_includes_confidence_and_context():
    lessons = [
        make_lesson(principle="always test", confidence=0.8, context="before deploy"),
        make_lesson(lid="b", principle="never force push", confidence=0.4),
    ]
    text = render_lessons(lessons)
    assert text.splitlines()[0] == "- (0.80) always test — before deploy"
    assert text.splitlines()[1] == "- (0.40) never force push"
    assert render_lessons([]) == ""


def test_build_context_returns_relevant_block_or_empty():
    store = LessonStore()
    store.add(make_lesson(lid="deploy", principle="run the suite before deploy", confidence=0.7))

    block = store.build_context("deploy the suite", agent_role="coder")
    assert block.startswith("- (0.70) run the suite before deploy")

    assert store.build_context("totally unrelated topic") == ""


# ── stats ────────────────────────────────────────────────────────────────
def test_stats_empty_and_populated():
    store = LessonStore()
    assert store.stats() == {
        "total": 0,
        "avg_confidence": 0.0,
        "by_agent": {},
        "by_outcome": {},
        "by_tag": {},
    }

    store.add(make_lesson(lid="a", confidence=0.4, agent_role="coder", tags=frozenset({"x"})))
    store.add(
        make_lesson(
            lid="b",
            confidence=0.8,
            agent_role="coder",
            tags=frozenset({"x", "y"}),
            source_outcome=Outcome.SUCCESS,
        )
    )
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["avg_confidence"] == pytest.approx(0.6)
    assert stats["by_agent"] == {"coder": 2}
    assert stats["by_outcome"] == {"failure": 1, "success": 1}
    assert stats["by_tag"] == {"x": 2, "y": 1}


# ── export / import ──────────────────────────────────────────────────────
def test_export_import_json_round_trip(tmp_path):
    store = LessonStore()
    store.add(make_lesson(lid="a", tags=frozenset({"deploy"})))
    path = tmp_path / "lessons.json"

    assert store.export(path) == 1

    restored = LessonStore()
    assert restored.import_from(path) == 1
    assert restored.get("a") == store.get("a")


def test_import_json_skips_invalid_items(tmp_path):
    path = tmp_path / "lessons.json"
    path.write_text(
        json.dumps(
            [
                make_lesson(lid="ok").to_dict(),
                {"id": "broken"},  # missing fields -> KeyError
                "not-an-object",
                {"id": "bad-outcome", "principle": "p", "context": "", "confidence": 0.5,
                 "source_outcome": "EXPLODED", "created_at": 1.0, "agent_role": "coder"},
            ]
        ),
        encoding="utf-8",
    )
    store = LessonStore()
    assert store.import_from(path) == 1
    assert len(store) == 1


def test_import_json_rejects_non_array(tmp_path):
    path = tmp_path / "lessons.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        LessonStore().import_from(path)


def test_import_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LessonStore().import_from(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        LessonStore().import_from_jsonl(tmp_path / "nope.jsonl")


def test_jsonl_round_trip_skips_blank_and_broken_lines(tmp_path):
    store = LessonStore()
    store.add(make_lesson(lid="a"))
    store.add(make_lesson(lid="b", created_at=2.0))
    path = tmp_path / "lessons.jsonl"
    assert store.export_to_jsonl(path) == 2

    path.write_text(path.read_text(encoding="utf-8") + "\n{broken\n\n", encoding="utf-8")
    restored = LessonStore()
    assert restored.import_from_jsonl(path) == 2
    assert set(restored.get(lid).id for lid in ("a", "b")) == {"a", "b"}


# ── injection into an agent ──────────────────────────────────────────────
def test_build_messages_appends_lessons_to_user_turn_only():
    agent = CoderAgent(RecordingBackend())
    messages = agent.build_messages("do the task", lessons="- (0.80) always test")

    system, user = messages
    assert system.content == CoderAgent.system_prompt
    assert "always test" not in system.content
    assert user.content == "do the task\n\n--- lessons ---\n- (0.80) always test"


def test_build_messages_orders_task_context_then_lessons():
    agent = CoderAgent(RecordingBackend())
    user = agent.build_messages("task", "ctx", lessons="lesson")[1].content
    assert user == "task\n\n--- context ---\nctx\n\n--- lessons ---\nlesson"


def test_build_messages_without_lessons_is_unchanged():
    agent = CoderAgent(RecordingBackend())
    assert agent.build_messages("task")[1].content == "task"
    assert agent.build_messages("task", "ctx")[1].content == "task\n\n--- context ---\nctx"


def test_agent_run_injects_lessons_into_backend_call():
    backend = RecordingBackend(reply="done")
    result = CoderAgent(backend).run("deploy", lessons="- (0.9) run tests first")

    assert result.ok
    assert "- (0.9) run tests first" in backend.last_user_message()
    assert CoderAgent.system_prompt == backend.calls[0][0][0].content


def test_injector_context_for_uses_agent_role_and_limits():
    store = LessonStore()
    store.add(make_lesson(lid="c", agent_role="coder", principle="always log the deploy"))
    injector = LessonInjector(store, limit=5)
    assert injector.context_for("deploy log", agent_role="coder").startswith("- (")


def test_injector_run_wires_relevant_lessons_into_the_agent():
    store = LessonStore()
    store.add(make_lesson(lid="d", principle="run the deploy test suite", confidence=0.7))
    injector = LessonInjector(store)
    backend = RecordingBackend(reply="ok")

    result = injector.run(CoderAgent(backend), "deploy the test suite")

    assert result.ok
    sent = backend.last_user_message()
    assert "--- lessons ---" in sent
    assert "run the deploy test suite" in sent


def test_injector_run_with_no_matching_lessons_adds_nothing():
    injector = LessonInjector(LessonStore())
    backend = RecordingBackend(reply="ok")

    injector.run(CoderAgent(backend), "unrelated task")

    assert backend.last_user_message() == "unrelated task"


# ── thread safety ────────────────────────────────────────────────────────
def test_concurrent_adds_are_safe():
    store = LessonStore()

    def worker(start: int) -> None:
        for offset in range(50):
            store.add(make_lesson(lid=f"{start}-{offset}", created_at=float(offset)))

    threads = [threading.Thread(target=worker, args=(group,)) for group in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(store) == 400
