"""Tests for the Lesson Extractor (Phase 2).

The LLM is stubbed with ``StubBackend`` (offline, deterministic) so every test
proves the extractor's own logic: parsing, confidence, dedup and merging.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.backend import EchoBackend
from src.core.types import Message
from src.learning import (
    Experience,
    ExperienceCollector,
    Lesson,
    LessonExtractor,
    Outcome,
)

T0 = 1_700_000_000.0


class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class StubBackend:
    """A backend that replays canned replies (last one repeats when exhausted).

    A reply may also be a callable ``(messages) -> str`` to raise or compute
    dynamically.
    """

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, messages: list[Message], *, model: str | None = None) -> str:
        self.prompts.append(messages[-1].content)
        if len(self._replies) > 1:
            reply = self._replies.pop(0)
        elif self._replies:
            reply = self._replies[0]
        else:
            reply = "{}"
        if callable(reply):
            return reply(messages)
        return reply


def reply_json(
    principle: str,
    *,
    context: str = "deploying",
    confidence: float = 0.6,
    tags: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "principle": principle,
            "context": context,
            "confidence": confidence,
            "tags": tags if tags is not None else ["deploy", "ops"],
        }
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def collector(clock):
    return ExperienceCollector(clock=clock)


def make_experience(
    task_id: str = "t1",
    step_id: str = "s1",
    role: str = "coder",
    action_type: str = "write_file",
    outcome: Outcome = Outcome.FAILURE,
    action: str = "deploy at night",
    result: str = "exit 1",
) -> Experience:
    return Experience(
        task_id=task_id,
        step_id=step_id,
        agent_role=role,
        action_type=action_type,
        outcome=outcome,
        action=action,
        result=result,
        ts=T0,
    )


# --------------------------------------------------------------------------- #
# Lesson dataclass
# --------------------------------------------------------------------------- #
def test_lesson_to_dict_round_trip():
    lesson = Lesson(
        id="abc",
        principle="When deploying, always run tests because failures cost releases",
        context="deploying",
        confidence=0.7,
        evidence=("t1:s1", "t2:s2"),
        source_outcome=Outcome.FAILURE,
        created_at=T0,
        agent_role="coder",
        tags=frozenset({"deploy", "testing"}),
    )
    data = lesson.to_dict()
    assert data["tags"] == ["deploy", "testing"]  # sorted
    assert data["evidence"] == ["t1:s1", "t2:s2"]
    assert data["source_outcome"] == "failure"
    restored = Lesson.from_dict(data)
    assert restored == lesson


def test_lesson_from_dict_unknown_outcome_raises():
    with pytest.raises(ValueError):
        Lesson.from_dict(
            {"id": "i", "principle": "p", "context": "c", "confidence": 0.5,
             "source_outcome": "exploded", "created_at": T0, "agent_role": "coder"}
        )


def test_lesson_from_dict_requires_core_fields():
    with pytest.raises(KeyError):
        Lesson.from_dict({"principle": "p"})


def test_lesson_from_dict_clamps_confidence():
    lesson = Lesson.from_dict(
        {"id": "i", "principle": "p", "context": "c", "confidence": 5.0,
         "source_outcome": "success", "created_at": T0, "agent_role": "coder"}
    )
    assert lesson.confidence == 1.0
    assert lesson.evidence == ()
    assert lesson.tags == frozenset()


# --------------------------------------------------------------------------- #
# single-experience extraction
# --------------------------------------------------------------------------- #
def test_extract_from_failure_builds_lesson(collector, clock):
    backend = StubBackend(reply_json("When deploying, never skip tests because failures cost releases"))
    extractor = LessonExtractor(backend, collector, clock=clock)
    exp = make_experience()

    lesson = extractor.extract_from_failure(exp)

    assert lesson is not None
    assert lesson.source_outcome is Outcome.FAILURE
    assert lesson.agent_role == "coder"
    assert lesson.evidence == ("t1:s1",)
    assert lesson.tags == frozenset({"deploy", "ops"})
    assert lesson.confidence == pytest.approx(0.6)
    assert lesson.created_at == T0
    assert lesson.principle.startswith("When deploying")


def test_extract_from_success_builds_lesson(collector, clock):
    backend = StubBackend(reply_json("When reviewing, always read tests first", confidence=0.8))
    extractor = LessonExtractor(backend, collector, clock=clock)
    exp = make_experience(outcome=Outcome.SUCCESS, action_type="read_file")

    lesson = extractor.extract_from_success(exp)

    assert lesson is not None
    assert lesson.source_outcome is Outcome.SUCCESS
    assert lesson.confidence == pytest.approx(0.8)


def test_extract_prompt_carries_the_experience(collector, clock):
    backend = StubBackend(reply_json("When deploying, always run tests because it is cheap"))
    extractor = LessonExtractor(backend, collector, clock=clock)
    extractor.extract_from_failure(make_experience(action="night deploy", result="boom"))
    prompt = backend.prompts[0]
    assert "night deploy" in prompt and "boom" in prompt and "FAILED" in prompt


def test_extract_returns_none_on_non_json_reply(collector, clock):
    extractor = LessonExtractor(EchoBackend(), collector, clock=clock)
    assert extractor.extract_from_failure(make_experience()) is None


def test_extract_returns_none_on_broken_json(collector, clock):
    extractor = LessonExtractor(StubBackend("{not json at all"), collector, clock=clock)
    assert extractor.extract_from_success(make_experience(outcome=Outcome.SUCCESS)) is None


def test_extract_returns_none_when_principle_blank(collector, clock):
    extractor = LessonExtractor(StubBackend(reply_json("   ")), collector, clock=clock)
    assert extractor.extract_from_failure(make_experience()) is None


def test_extract_returns_none_on_malformed_json_with_braces(collector, clock):
    # Has { and } but is not valid JSON -> exercises the decode-error branch.
    extractor = LessonExtractor(StubBackend("{python: not, json}"), collector, clock=clock)
    assert extractor.extract_from_failure(make_experience()) is None


def test_extract_defaults_confidence_when_not_numeric(collector, clock):
    reply = '{"principle": "When X, always Y because Z", "confidence": "high"}'
    extractor = LessonExtractor(StubBackend(reply), collector, clock=clock)
    lesson = extractor.extract_from_failure(make_experience())
    assert lesson is not None and lesson.confidence == pytest.approx(0.5)


def test_extract_returns_none_on_backend_error(collector, clock):
    def boom(_messages: list[Message]) -> str:
        raise RuntimeError("server down")

    extractor = LessonExtractor(StubBackend(boom), collector, clock=clock)
    assert extractor.extract_from_failure(make_experience()) is None


def test_extract_strips_markdown_code_fence(collector, clock):
    fenced = "```json\n" + reply_json("When deploying, always run tests because cheap") + "\n```"
    extractor = LessonExtractor(StubBackend(fenced), collector, clock=clock)
    lesson = extractor.extract_from_failure(make_experience())
    assert lesson is not None and lesson.principle


def test_extract_accepts_missing_optional_fields(collector, clock):
    extractor = LessonExtractor(StubBackend('{"principle": "When X, never Y because Z"}'), collector, clock=clock)
    lesson = extractor.extract_from_failure(make_experience())
    assert lesson is not None
    assert lesson.context == ""
    assert lesson.tags == frozenset()
    assert lesson.confidence == pytest.approx(0.5)  # default


# --------------------------------------------------------------------------- #
# batch extraction + deduplication
# --------------------------------------------------------------------------- #
def test_extract_batch_merges_similar_principles(collector, clock):
    same = "When deploying, always run tests because failures cost releases"
    backend = StubBackend(reply_json(same))
    extractor = LessonExtractor(backend, collector, clock=clock)
    batch = [make_experience("t1", "s1"), make_experience("t2", "s2")]

    lessons = extractor.extract_batch(batch)

    assert len(lessons) == 1
    merged = lessons[0]
    assert merged.evidence == ("t1:s1", "t2:s2")
    # repetition boost: 0.6 + 0.05 = 0.65
    assert merged.confidence == pytest.approx(0.65)


def test_extract_batch_keeps_distinct_principles(collector, clock):
    backend = StubBackend(
        reply_json("When deploying, always run tests because cheap"),
        reply_json("When reviewing, never approve broken builds because quality"),
    )
    extractor = LessonExtractor(backend, collector, clock=clock)
    lessons = extractor.extract_batch([make_experience(), make_experience("t2", "s2")])
    assert len(lessons) == 2


def test_extract_batch_contradiction_lowers_confidence(collector, clock):
    same = "When deploying, always run tests because failures cost releases"
    backend = StubBackend(reply_json(same, confidence=0.6), reply_json(same, confidence=0.6))
    extractor = LessonExtractor(backend, collector, clock=clock)
    failure = make_experience("t1", "s1", outcome=Outcome.FAILURE)
    success = make_experience("t2", "s2", outcome=Outcome.SUCCESS)

    merged = extractor.extract_batch([failure, success])[0]

    # 0.6 + boost(0.05) - penalty(0.25) = 0.4
    assert merged.confidence == pytest.approx(0.4)


def test_extract_batch_ignores_unparseable_entries(collector, clock):
    backend = StubBackend(reply_json("When deploying, always run tests because cheap"), "garbage")
    extractor = LessonExtractor(backend, collector, clock=clock)
    lessons = extractor.extract_batch([make_experience(), make_experience("t2", "s2")])
    assert len(lessons) == 1
    assert lessons[0].evidence == ("t1:s1",)


def test_extract_batch_empty_returns_empty(collector, clock):
    extractor = LessonExtractor(StubBackend("{}"), collector, clock=clock)
    assert extractor.extract_batch([]) == []


def test_extract_from_task_filters_by_task_id(collector, clock):
    collector.record("task-a", "s1", "coder", "write_file", Outcome.FAILURE, result="boom")
    collector.record("task-a", "s2", "coder", "write_file", Outcome.SUCCESS, result="ok")
    collector.record("task-b", "s1", "planner", "read_file", Outcome.SUCCESS, result="ok")

    same = "When deploying, always run tests because failures cost releases"
    backend = StubBackend(reply_json(same))
    extractor = LessonExtractor(backend, collector, clock=clock)

    lessons = extractor.extract_from_task("task-a")

    assert len(lessons) == 1
    assert lessons[0].evidence == ("task-a:s1", "task-a:s2")
    assert len(backend.prompts) == 2  # only task-a's two experiences were sent


# --------------------------------------------------------------------------- #
# confidence maths
# --------------------------------------------------------------------------- #
def test_confidence_is_clamped_to_unit_interval():
    assert LessonExtractor._confidence(0.98, evidence_count=10, contradictory=False) == 1.0
    assert LessonExtractor._confidence(0.1, evidence_count=3, contradictory=True) == 0.0
    assert LessonExtractor._confidence(0.5, evidence_count=1, contradictory=False) == 0.5


def test_confidence_repetition_is_capped():
    # 10 supporting experiences still only boost by the cap (0.2).
    assert LessonExtractor._confidence(0.5, evidence_count=10, contradictory=False) == pytest.approx(0.7)


def test_similarity_helpers():
    assert LessonExtractor._similar("always run the tests", "always run tests")
    assert not LessonExtractor._similar("always run tests", "never deploy on friday")


def test_similarity_handles_tokenless_strings():
    # No alphanumeric tokens -> falls back to exact (case-insensitive) compare.
    assert LessonExtractor._similar("...", "...")
    assert not LessonExtractor._similar("...", "???")