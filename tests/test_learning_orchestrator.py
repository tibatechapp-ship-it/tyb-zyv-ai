"""Tests for the LearningOrchestrator — the collector→extractor→store pipeline.

The LLM is stubbed offline (``StubBackend``) so the tests pin the orchestrator's
own behaviour: source resolution, the confidence filter, and the report.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.types import Message
from src.learning import (
    DEFAULT_MIN_CONFIDENCE,
    Experience,
    ExperienceCollector,
    ExtractionReport,
    LearningOrchestrator,
    LessonExtractor,
    LessonStore,
    Outcome,
)


class StubBackend:
    """Replays canned replies (the last one repeats when exhausted)."""

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
        return reply(messages) if callable(reply) else reply


def reply_json(principle: str, *, confidence: float = 0.6, tags: list[str] | None = None) -> str:
    return json.dumps(
        {
            "principle": principle,
            "context": "deploying",
            "confidence": confidence,
            "tags": tags if tags is not None else ["deploy"],
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


def _orchestrator(backend: StubBackend, collector: ExperienceCollector, store: LessonStore, **kw: Any) -> LearningOrchestrator:
    return LearningOrchestrator(collector, LessonExtractor(backend, collector), store, **kw)


def test_default_min_confidence_constant():
    assert DEFAULT_MIN_CONFIDENCE == 0.5


def test_auto_extract_keeps_strong_and_drops_weak():
    collector = ExperienceCollector()
    collector.record("t1", "s1", "coder", "write_file", Outcome.FAILURE, action="deploy", result="exit 1")
    collector.record("t1", "s2", "coder", "write_file", Outcome.SUCCESS, action="retry", result="ok")
    backend = StubBackend(
        reply_json("always run the tests before deploy", confidence=0.9),
        reply_json("never push to main on friday", confidence=0.2),
    )
    store = LessonStore()
    report = _orchestrator(backend, collector, store).auto_extract(task_id="t1")

    assert report.extracted == 2
    assert report.kept == 1
    assert report.dropped == 1
    assert len(store) == 1
    assert report.lessons[0].confidence == pytest.approx(0.9)
    assert store.all()[0].principle == "always run the tests before deploy"


def test_auto_extract_iterates_every_experience_without_task_id():
    collector = ExperienceCollector()
    collector.record("a", "s1", "coder", "write_file", Outcome.FAILURE)
    collector.record("b", "s2", "tester", "run_command", Outcome.SUCCESS)
    backend = StubBackend(
        reply_json("always verify the result", confidence=0.8),
        reply_json("never skip the changelog", confidence=0.6),
    )
    store = LessonStore()

    report = _orchestrator(backend, collector, store).auto_extract()

    assert report.extracted == 2
    assert report.kept == 2
    assert len(store) == 2
    assert len(backend.prompts) == 2


def test_auto_extract_accepts_explicit_experiences():
    collector = ExperienceCollector()  # deliberately empty
    backend = StubBackend(reply_json("prefer small commits", confidence=0.7))
    store = LessonStore()

    report = _orchestrator(backend, collector, store).auto_extract(
        experiences=[make_experience("s1")]
    )

    assert report.kept == 1
    assert len(store) == 1


def test_auto_extract_min_confidence_override():
    collector = ExperienceCollector()
    collector.record("t1", "s1", "coder", "write_file", Outcome.FAILURE)
    backend = StubBackend(reply_json("some middling lesson", confidence=0.4))
    store = LessonStore()
    orchestrator = _orchestrator(backend, collector, store)

    assert orchestrator.auto_extract(task_id="t1").kept == 0  # default floor 0.5

    report = orchestrator.auto_extract(task_id="t1", min_confidence=0.3)
    assert report.kept == 1
    assert len(store) == 1


def test_auto_extract_empty_collector_is_a_noop():
    store = LessonStore()
    report = _orchestrator(StubBackend(), ExperienceCollector(), store).auto_extract()
    assert report == ExtractionReport(extracted=0, kept=0, dropped=0, lessons=())
    assert len(store) == 0
    assert store.stats()["total"] == 0


def test_auto_extract_report_to_dict():
    collector = ExperienceCollector()
    collector.record("t1", "s1", "coder", "write_file", Outcome.FAILURE)
    backend = StubBackend(reply_json("always test first", confidence=0.9))
    report = _orchestrator(backend, collector, LessonStore()).auto_extract(task_id="t1")

    data = report.to_dict()
    assert data["extracted"] == 1 and data["kept"] == 1 and data["dropped"] == 0
    assert data["lessons"][0]["principle"] == "always test first"


def test_min_confidence_property_reflects_constructor():
    orchestrator = _orchestrator(StubBackend(), ExperienceCollector(), LessonStore(), min_confidence=0.75)
    assert orchestrator.min_confidence == pytest.approx(0.75)


def test_explicit_experiences_take_priority_over_task_id():
    collector = ExperienceCollector()
    collector.record("t1", "collected", "coder", "write_file", Outcome.FAILURE)
    backend = StubBackend(reply_json("explicit lesson", confidence=0.9))
    store = LessonStore()

    _orchestrator(backend, collector, store).auto_extract(
        task_id="t1", experiences=[make_experience("explicit")]
    )

    # Exactly one extraction happened, and it used the explicit experience.
    assert len(backend.prompts) == 1
    assert "explicit" in backend.prompts[0]
    assert len(store) == 1
