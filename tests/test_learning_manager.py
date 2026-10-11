"""Tests for the ContinualLearningManager — the collect→extract→validate→merge loop.

The LLM is stubbed offline (``StubBackend``) so the tests pin the manager's own
behaviour: incremental collection, validation, cross-cycle merging, and the
schedule/run_due automation. ``LessonStore.merge`` is exercised directly too.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.types import Message
from src.learning import (
    ContinualLearningManager,
    Experience,
    ExperienceCollector,
    Lesson,
    LessonExtractor,
    LessonStore,
    LearningCycle,
    Outcome,
    SchedulePolicy,
)


class StubBackend:
    """Replays canned replies (the last one repeats when exhausted)."""

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)

    def generate(self, messages: list[Message], *, model: str | None = None) -> str:
        if len(self._replies) > 1:
            reply = self._replies.pop(0)
        elif self._replies:
            reply = self._replies[0]
        else:
            reply = "{}"
        return reply(messages) if callable(reply) else reply


def reply_json(principle: str, *, confidence: float = 0.6) -> str:
    return json.dumps(
        {
            "principle": principle,
            "context": "deploying",
            "confidence": confidence,
            "tags": ["deploy"],
        }
    )


def make_experience(step: str, outcome: Outcome = Outcome.FAILURE) -> Experience:
    return Experience(
        task_id="t1",
        step_id=step,
        agent_role="coder",
        action_type="write_file",
        outcome=outcome,
        action="deploy",
        result="exit 1",
        ts=1.0,
    )


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_lesson(principle: str, *, confidence: float = 0.8, created_at: float = 1.0) -> Lesson:
    return Lesson(
        id=f"id-{principle}",
        principle=principle,
        context="deploying",
        confidence=confidence,
        evidence=("t1:s1",),
        source_outcome=Outcome.FAILURE,
        created_at=created_at,
        agent_role="coder",
        tags=frozenset({"deploy"}),
    )


def _manager(
    backend: StubBackend,
    collector: ExperienceCollector,
    store: LessonStore,
    **kw: Any,
) -> ContinualLearningManager:
    return ContinualLearningManager(
        collector, LessonExtractor(backend, collector), store, **kw
    )


# -- LessonStore.merge ------------------------------------------------------
def test_store_merge_folds_similar_lesson() -> None:
    store = LessonStore()
    first = store.add(make_lesson("always run unit tests before deploy"))
    merged = store.merge(make_lesson("always run the tests before deploy"))

    assert merged.id != first.id  # a merge produces a fresh id
    assert len(store) == 1
    assert store.get(first.id) is None
    assert store.get(merged.id) is not None
    assert merged.evidence == ("t1:s1",)


def test_store_merge_adds_when_dissimilar() -> None:
    store = LessonStore()
    store.add(make_lesson("always run unit tests before deploy"))
    result = store.merge(make_lesson("never delete the production database"))

    assert result.id == "id-never delete the production database"
    assert len(store) == 2


def test_store_merge_into_empty_store_adds() -> None:
    store = LessonStore()
    lesson = make_lesson("always back up first")
    assert store.merge(lesson) is lesson
    assert len(store) == 1


def test_store_merge_unions_evidence_and_tags() -> None:
    store = LessonStore()
    store.add(make_lesson("always run unit tests before deploy"))
    other = Lesson(
        id="x",
        principle="always run the tests before deploy",
        context="deploying",
        confidence=0.7,
        evidence=("t2:s9",),
        source_outcome=Outcome.SUCCESS,
        created_at=2.0,
        agent_role="tester",
        tags=frozenset({"ci"}),
    )
    merged = store.merge(other)
    assert set(merged.evidence) == {"t1:s1", "t2:s9"}
    assert merged.tags == frozenset({"deploy", "ci"})


# -- learn() ----------------------------------------------------------------
def test_learn_incremental_adds_first_cycle() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    cycle = manager.learn()

    assert isinstance(cycle, LearningCycle)
    assert cycle.collected == 1
    assert cycle.extracted == 1
    assert cycle.validated == 1
    assert cycle.added == 1
    assert cycle.merged == 0
    assert len(store) == 1
    assert manager.history == (cycle,)
    assert manager.pending() == 0


def test_learn_second_cycle_merges_recurring_lesson() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )
    manager.learn()
    collector.record(
        task_id="t1", step_id="s2", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    cycle = manager.learn()

    assert cycle.collected == 1
    assert cycle.added == 0
    assert cycle.merged == 1
    assert len(store) == 1  # merged in place, not duplicated
    assert len(manager.history) == 2


def test_learn_with_no_new_experiences_is_empty() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    manager = _manager(StubBackend(reply_json("x")), collector, store)

    cycle = manager.learn()

    assert cycle.collected == 0
    assert cycle.extracted == 0
    assert cycle.lessons == ()
    assert len(store) == 0
    assert manager.history == (cycle,)


def test_learn_explicit_batch_does_not_consume_cursor() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    manager.learn(experiences=[make_experience("s9")])

    assert len(store) == 1
    assert manager.pending() == 1  # the recorded experience is still unconsumed


def test_learn_custom_validator_is_the_gate() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(
        backend, collector, store, validator=lambda lesson: False
    )
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    cycle = manager.learn()

    assert cycle.extracted == 1
    assert cycle.validated == 0
    assert len(store) == 0
    assert cycle.dropped >= 1


def test_learn_drops_low_confidence_lessons() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("weak idea", confidence=0.1))
    manager = _manager(backend, collector, store)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    cycle = manager.learn()

    assert cycle.extracted == 1
    assert cycle.validated == 0
    assert len(store) == 0


def test_learn_resyncs_after_buffer_prune() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store)
    for step in ("s1", "s2"):
        collector.record(
            task_id="t1", step_id=step, agent_role="coder", action_type="write_file",
            outcome=Outcome.FAILURE, action="deploy", result="exit 1",
        )
    manager.learn()  # cursor now 2
    collector.clear()  # buffer shrinks under the cursor
    collector.record(
        task_id="t1", step_id="s3", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )

    cycle = manager.learn()

    assert cycle.collected == 1  # resynced, the one surviving experience is fresh


# -- schedule / run_due -----------------------------------------------------
def test_schedule_returns_policy_and_unschedule_clears() -> None:
    manager = _manager(StubBackend(), ExperienceCollector(), LessonStore())
    policy = manager.schedule(every_experiences=5, every_seconds=30)
    assert policy == SchedulePolicy(every_experiences=5, every_seconds=30)
    assert manager.policy == policy
    manager.unschedule()
    assert manager.policy is None


def test_schedule_rejects_disabled_triggers() -> None:
    manager = _manager(StubBackend(), ExperienceCollector(), LessonStore())
    with pytest.raises(ValueError):
        manager.schedule(every_experiences=0, every_seconds=0)


def test_due_false_when_unscheduled_or_idle() -> None:
    collector = ExperienceCollector()
    manager = _manager(StubBackend(), collector, LessonStore())
    assert manager.due() is False  # unscheduled
    manager.schedule(every_experiences=1)
    assert manager.due() is False  # no pending work
    assert manager.run_due() is None


def test_due_and_run_due_by_experience_count() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store)
    manager.schedule(every_experiences=2)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )
    assert manager.due() is False  # only one pending
    collector.record(
        task_id="t1", step_id="s2", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )
    assert manager.due() is True

    cycle = manager.run_due()

    assert cycle is not None
    assert cycle.collected == 2
    assert manager.pending() == 0
    assert manager.due() is False


def test_due_by_elapsed_time() -> None:
    clock = FakeClock(100.0)
    collector = ExperienceCollector()
    store = LessonStore()
    backend = StubBackend(reply_json("always run unit tests before deploy"))
    manager = _manager(backend, collector, store, clock=clock)
    manager.schedule(every_experiences=100, every_seconds=10)
    collector.record(
        task_id="t1", step_id="s1", agent_role="coder", action_type="write_file",
        outcome=Outcome.FAILURE, action="deploy", result="exit 1",
    )
    assert manager.due() is False  # too few experiences, no time elapsed
    clock.now = 111.0
    assert manager.due() is True  # 11s >= 10s with pending work

    cycle = manager.run_due()

    assert cycle is not None and cycle.collected == 1


def test_manager_exposes_confidence_and_orchestrator() -> None:
    collector = ExperienceCollector()
    store = LessonStore()
    manager = _manager(StubBackend(), collector, store, min_confidence=0.7)
    assert manager.min_confidence == 0.7
    assert manager.orchestrator.min_confidence == 0.7


def test_schedule_after_a_run_keeps_last_run_reference() -> None:
    clock = FakeClock(50.0)
    manager = _manager(StubBackend(), ExperienceCollector(), LessonStore(), clock=clock)
    manager.learn()  # sets _last_run_at
    clock.now = 55.0
    policy = manager.schedule(every_experiences=1)
    assert manager.policy == policy


# -- to_dict ----------------------------------------------------------------
def test_schedule_policy_to_dict() -> None:
    assert SchedulePolicy(every_experiences=3, every_seconds=4.5).to_dict() == {
        "every_experiences": 3,
        "every_seconds": 4.5,
    }


def test_learning_cycle_to_dict() -> None:
    lesson = make_lesson("always run unit tests before deploy")
    cycle = LearningCycle(
        collected=2, extracted=1, validated=1, merged=0, added=1, dropped=1,
        lessons=(lesson,), duration=0.5,
    )
    data = cycle.to_dict()
    assert data["collected"] == 2
    assert data["lessons"][0]["principle"] == "always run unit tests before deploy"
    assert data["duration"] == 0.5
