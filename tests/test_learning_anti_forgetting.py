"""Tests for the anti-forgetting layer — protection, conflict detection, sparse merge.

All offline: lessons are built directly, no LLM.
"""

from __future__ import annotations

from typing import Any

from src.learning import (
    AntiForgetting,
    Conflict,
    ConsolidationResult,
    ContinualLearningManager,
    ExperienceCollector,
    Lesson,
    LessonExtractor,
    LessonStore,
    Outcome,
    polarity,
)


def make_lesson(
    principle: str,
    *,
    lesson_id: str | None = None,
    confidence: float = 0.6,
    evidence: tuple[str, ...] = ("t1:s1",),
    outcome: Outcome = Outcome.FAILURE,
    created_at: float = 1.0,
    agent_role: str = "coder",
    tags: frozenset[str] = frozenset({"deploy"}),
) -> Lesson:
    return Lesson(
        id=lesson_id or f"id-{principle}",
        principle=principle,
        context="deploying",
        confidence=confidence,
        evidence=evidence,
        source_outcome=outcome,
        created_at=created_at,
        agent_role=agent_role,
        tags=tags,
    )


# -- polarity ---------------------------------------------------------------
def test_polarity_detects_sides() -> None:
    assert polarity("always run the tests") == 1
    assert polarity("never deploy on a friday") == -1
    assert polarity("the build is green") == 0


# -- protection -------------------------------------------------------------
def test_explicit_protection_roundtrip() -> None:
    layer = AntiForgetting(LessonStore())
    layer.protect("core-1")
    assert layer.protected_ids() == frozenset({"core-1"})
    assert layer.unprotect("core-1") is True
    assert layer.unprotect("core-1") is False
    assert layer.protected_ids() == frozenset()


def test_is_protected_by_value_rule() -> None:
    layer = AntiForgetting(LessonStore(), protection_threshold=0.8, min_evidence=3)
    strong = make_lesson("always test", confidence=0.9, evidence=("a", "b", "c"))
    weak = make_lesson("never test", confidence=0.5, evidence=("a",))
    assert layer.is_protected(strong) is True
    assert layer.is_protected(weak) is False


def test_is_protected_by_explicit_id_and_predicate() -> None:
    store = LessonStore()
    layer = AntiForgetting(store, protect=lambda lesson: "keep" in lesson.principle)
    pinned = make_lesson("x", lesson_id="pinned", confidence=0.1, evidence=())
    store.add(pinned)
    layer.protect("pinned")
    assert layer.is_protected(pinned) is True
    assert layer.is_protected(make_lesson("please keep this")) is True
    assert layer.is_protected(make_lesson("drop this")) is False


def test_protect_valuable_pins_store_core() -> None:
    store = LessonStore()
    layer = AntiForgetting(store, protection_threshold=0.8, min_evidence=2)
    store.add(make_lesson("strong", confidence=0.9, evidence=("a", "b")))
    store.add(make_lesson("weak", confidence=0.9, evidence=("a",)))
    pinned = layer.protect_valuable()
    assert pinned == 1
    assert layer.protected_ids() == frozenset({"id-strong"})
    assert layer.protect_valuable() == 0  # idempotent


# -- conflict detection -----------------------------------------------------
def test_detect_conflict_opposite_polarity_same_topic() -> None:
    store = LessonStore()
    store.add(make_lesson("always run the tests before deploy"))
    layer = AntiForgetting(store)
    conflicts = layer.detect_conflicts(make_lesson("never run the tests before deploy"))
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], Conflict)
    assert conflicts[0].existing.principle.startswith("always")


def test_detect_no_conflict_same_polarity() -> None:
    store = LessonStore()
    store.add(make_lesson("always run the tests before deploy"))
    layer = AntiForgetting(store)
    assert layer.detect_conflicts(make_lesson("always run unit tests before deploy")) == []


def test_detect_no_conflict_different_topic() -> None:
    store = LessonStore()
    store.add(make_lesson("always run the tests before deploy"))
    layer = AntiForgetting(store)
    assert layer.detect_conflicts(make_lesson("never delete the production database")) == []


def test_detect_no_conflict_when_polarity_neutral() -> None:
    store = LessonStore()
    store.add(make_lesson("the build should be fast"))
    layer = AntiForgetting(store)
    assert layer.detect_conflicts(make_lesson("never cache the build")) == []


def test_detect_no_conflict_when_one_side_has_no_polarity() -> None:
    store = LessonStore()
    store.add(make_lesson("always run the tests before deploy"))
    layer = AntiForgetting(store)
    # "the build is green" has no polarity word → cannot contradict.
    assert layer.detect_conflicts(make_lesson("the build is green")) == []


def test_topic_similar_false_when_side_is_polarity_only() -> None:
    store = LessonStore()
    store.add(make_lesson("always"))
    layer = AntiForgetting(store)
    assert layer.detect_conflicts(make_lesson("never")) == []


def test_conflict_to_dict_shape() -> None:
    existing = make_lesson("always run the tests before deploy")
    incoming = make_lesson("never run the tests before deploy")
    data = Conflict(existing, incoming).to_dict()
    assert data["existing"]["principle"].startswith("always")
    assert data["incoming"]["principle"].startswith("never")


# -- consolidation ----------------------------------------------------------
def test_consolidate_adds_when_new() -> None:
    store = LessonStore()
    layer = AntiForgetting(store)
    result = layer.consolidate(make_lesson("always run tests before deploy"))
    assert isinstance(result, ConsolidationResult)
    assert result.action == "added"
    assert result.conflict is None
    assert len(store) == 1


def test_consolidate_merges_similar_same_polarity() -> None:
    store = LessonStore()
    layer = AntiForgetting(store)
    first = layer.consolidate(make_lesson("always run unit tests before deploy"))
    second = layer.consolidate(make_lesson("always run the tests before deploy"))
    assert second.action == "merged"
    assert len(store) == 1
    assert second.lesson.id != first.lesson.id  # fresh id
    assert set(second.lesson.evidence) == {"t1:s1"}


def test_consolidate_keeps_conflict_alongside() -> None:
    store = LessonStore()
    layer = AntiForgetting(store)
    layer.consolidate(make_lesson("always run the tests before deploy"))
    result = layer.consolidate(make_lesson("never run the tests before deploy"))
    assert result.action == "conflict"
    assert result.conflict is not None
    assert len(store) == 2  # both survive — no silent overwrite
    assert len(layer.conflicts) == 1


def test_consolidate_preserves_protected_status_through_merge() -> None:
    store = LessonStore()
    layer = AntiForgetting(store)
    first = layer.consolidate(make_lesson("always run unit tests before deploy"))
    layer.protect(first.lesson.id)
    second = layer.consolidate(make_lesson("always run the tests before deploy"))
    assert second.action == "merged"
    assert second.lesson.id in layer.protected_ids()
    assert first.lesson.id not in layer.protected_ids()


def test_consolidate_same_identity_is_not_self_merged() -> None:
    store = LessonStore()
    layer = AntiForgetting(store)
    lesson = make_lesson("always run tests", lesson_id="fixed")
    store.add(lesson)
    result = layer.consolidate(lesson)  # same id already stored
    assert result.action == "added"
    assert result.lesson.id == "fixed"


def test_consolidate_result_to_dict() -> None:
    layer = AntiForgetting(LessonStore())
    data = layer.consolidate(make_lesson("always run tests")).to_dict()
    assert data["action"] == "added"
    assert data["conflict"] is None


# -- reinforcement ----------------------------------------------------------
def test_reinforce_bumps_confidence() -> None:
    store = LessonStore()
    lesson = store.add(make_lesson("always run tests", confidence=0.5))
    layer = AntiForgetting(store)
    bumped = layer.reinforce(lesson.id, amount=0.2)
    assert bumped is not None
    assert bumped.id == lesson.id
    assert bumped.confidence == 0.7
    assert store.get(lesson.id).confidence == 0.7


def test_reinforce_missing_returns_none() -> None:
    layer = AntiForgetting(LessonStore())
    assert layer.reinforce("nope") is None


def test_reinforce_clamps_at_one() -> None:
    store = LessonStore()
    lesson = store.add(make_lesson("always run tests", confidence=0.95))
    layer = AntiForgetting(store)
    assert layer.reinforce(lesson.id, amount=0.5).confidence == 1.0


# -- conflict resolution ----------------------------------------------------
def test_resolve_drops_less_confident() -> None:
    store = LessonStore()
    existing = store.add(make_lesson("always run the tests before deploy", confidence=0.3))
    incoming = make_lesson("never run the tests before deploy", confidence=0.9)
    layer = AntiForgetting(store, protection_threshold=1.0)
    conflict = Conflict(existing, incoming)

    winner = layer.resolve(conflict, keep="confident")

    assert winner is incoming
    assert store.get(existing.id) is None
    assert store.get(incoming.id) is not None


def test_resolve_keep_existing_explicit() -> None:
    store = LessonStore()
    existing = make_lesson("always run the tests before deploy", confidence=0.3)
    incoming = make_lesson("never run the tests before deploy", confidence=0.9)
    store.add(existing)
    store.add(incoming)
    layer = AntiForgetting(store, protection_threshold=1.0)
    winner = layer.resolve(Conflict(existing, incoming), keep="existing")
    assert winner is existing
    assert store.get(incoming.id) is None


def test_resolve_never_drops_protected() -> None:
    store = LessonStore()
    existing = store.add(make_lesson("always run the tests before deploy", confidence=0.9, evidence=("a", "b", "c")))
    incoming = make_lesson("never run the tests before deploy", confidence=0.95)
    layer = AntiForgetting(store, protection_threshold=0.8, min_evidence=3)
    assert layer.resolve(Conflict(existing, incoming), keep="confident") is None
    assert store.get(existing.id) is not None  # protected survivor kept


def test_resolve_keep_incoming_explicit() -> None:
    store = LessonStore()
    existing = make_lesson("always run the tests before deploy", confidence=0.9)
    incoming = make_lesson("never run the tests before deploy", confidence=0.3)
    store.add(existing)
    store.add(incoming)
    layer = AntiForgetting(store, protection_threshold=1.0)
    winner = layer.resolve(Conflict(existing, incoming), keep="incoming")
    assert winner is incoming
    assert store.get(existing.id) is None


def test_resolve_confident_prefers_existing_on_low_incoming() -> None:
    store = LessonStore()
    existing = make_lesson("always run the tests before deploy", confidence=0.9)
    incoming = make_lesson("never run the tests before deploy", confidence=0.2)
    store.add(existing)
    store.add(incoming)
    layer = AntiForgetting(store, protection_threshold=1.0)
    winner = layer.resolve(Conflict(existing, incoming), keep="confident")
    assert winner is existing
    assert store.get(incoming.id) is None


# -- manager integration ----------------------------------------------------
class StubBackend:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)

    def generate(self, messages: list[Any], *, model: str | None = None) -> str:
        if len(self._replies) > 1:
            return self._replies.pop(0)
        return self._replies[0] if self._replies else "{}"


def _reply(principle: str, confidence: float = 0.6) -> str:
    import json

    return json.dumps(
        {"principle": principle, "context": "deploying", "confidence": confidence}
    )


def _record(collector: ExperienceCollector, step: str) -> None:
    collector.record(
        task_id="t1", step_id=step, agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )


def test_manager_reports_conflicts_when_guarded() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(
        _reply("always run the tests before deploy"),
        _reply("never run the tests before deploy"),
    )
    layer = AntiForgetting(store)
    manager = ContinualLearningManager(
        collector, LessonExtractor(backend, collector), store, anti_forgetting=layer
    )
    assert manager.anti_forgetting is layer
    _record(collector, "s1")
    first = manager.learn()
    assert first.added == 1
    _record(collector, "s2")
    second = manager.learn()
    assert second.conflicts == 1
    assert second.to_dict()["conflicts"] == 1
    assert len(store) == 2


def test_manager_merges_through_anti_forgetting_layer() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(_reply("always run unit tests before deploy"))
    layer = AntiForgetting(store)
    manager = ContinualLearningManager(
        collector, LessonExtractor(backend, collector), store, anti_forgetting=layer
    )
    _record(collector, "s1")
    first = manager.learn()
    _record(collector, "s2")
    second = manager.learn()
    assert first.added == 1
    assert second.merged == 1  # the layer's merged action path
    assert second.added == 0
    assert len(store) == 1


def test_manager_without_layer_has_no_conflicts_field_change() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(_reply("always run tests before deploy"))
    manager = ContinualLearningManager(collector, LessonExtractor(backend, collector), store)
    assert manager.anti_forgetting is None
    _record(collector, "s1")
    assert manager.learn().conflicts == 0
