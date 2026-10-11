"""Lesson extractor — turn raw experiences into general principles.

A :class:`Lesson` is a reusable principle distilled from one or more
:class:`~src.learning.experience_collector.Experience` records (e.g. *"When
deploying at night, always run the test suite first because failures cost a
whole release"*). :class:`LessonExtractor` asks an :class:`LLMBackend` for one
principle per experience, then de-duplicates and merges similar lessons while
scoring their confidence.

Design notes:

* extraction is **tolerant** — a backend reply that is not valid JSON (or a
  backend error) yields ``None`` instead of raising, so one bad call never kills
  a batch;
* confidence starts from the model's own estimate and is adjusted
  deterministically by the support behind a lesson (repetition up, contradictions
  down), so equal inputs always score equally;
* evidence is recorded as ``"{task_id}:{step_id}"`` — the collector's
  experiences have no id of their own.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.backend import LLMBackend
from src.core.types import Message
from src.learning.experience_collector import Experience, ExperienceCollector, Outcome

__all__ = [
    "Lesson",
    "LessonExtractor",
    "blend_confidence",
    "merge_lessons",
    "principles_similar",
]

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a Lesson Extractor. Analyze the experience and extract ONE
general principle.

Format: "When [context], always/never [action] because [reason]".

Output JSON:
{
  "principle": "...",
  "context": "...",
  "confidence": 0.0-1.0,
  "tags": ["...", "..."]
}

Confidence based on:
- Number of supporting experiences.
- Pattern repetition.
- Clarity of cause.
- Absence of contradictions."""

_FAILURE_FOCUS = "This experience FAILED. Prefer a 'never' principle about what to avoid."
_SUCCESS_FOCUS = "This experience SUCCEEDED. Prefer an 'always' principle about what worked."

#: Similarity threshold (token Jaccard) above which two principles are merged.
_SIMILARITY_THRESHOLD = 0.6
#: Confidence boost per extra supporting experience, and its cap.
_REPETITION_STEP = 0.05
_REPETITION_CAP = 0.2
_CONTADICTION_PENALTY = 0.25
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase word tokens of ``text`` (used for similarity)."""
    return frozenset(_TOKEN.findall(text.lower()))


def principles_similar(left: str, right: str) -> bool:
    """Whether two principle strings are similar enough to be merged.

    Similarity is token Jaccard at or above :data:`_SIMILARITY_THRESHOLD`; two
    blank (tokenless) strings are similar only when textually identical.
    """
    left_tokens, right_tokens = _tokenize(left), _tokenize(right)
    if not left_tokens or not right_tokens:
        return left.strip().lower() == right.strip().lower()
    if left_tokens == right_tokens:
        return True
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) >= _SIMILARITY_THRESHOLD


def blend_confidence(base: float, evidence_count: int, contradictory: bool) -> float:
    """Re-score confidence from ``base``, support count and contradiction flag."""
    confidence = base + min(_REPETITION_CAP, max(0, evidence_count - 1) * _REPETITION_STEP)
    if contradictory:
        confidence -= _CONTADICTION_PENALTY
    return round(min(1.0, max(0.0, confidence)), 3)


def merge_lessons(group: Sequence[Lesson]) -> Lesson:
    """Merge similar lessons into one: union evidence/tags, dominant labels.

    ``source_outcome``/``agent_role`` take the most common value (ties broken by
    first-seen); confidence is re-scored with a contradiction penalty when the
    group disagrees on outcome; the result is stamped with a fresh id.
    """
    evidence = tuple(dict.fromkeys(item for lesson in group for item in lesson.evidence))
    outcomes = [lesson.source_outcome for lesson in group]
    contradictory = len(set(outcomes)) > 1
    base = max(lesson.confidence for lesson in group)
    return Lesson(
        id=uuid.uuid4().hex,
        principle=group[0].principle,
        context=group[0].context,
        confidence=blend_confidence(base, len(evidence), contradictory),
        evidence=evidence,
        source_outcome=Counter(outcomes).most_common(1)[0][0],
        created_at=max(lesson.created_at for lesson in group),
        agent_role=Counter(item.agent_role for item in group).most_common(1)[0][0],
        tags=frozenset(tag for lesson in group for tag in lesson.tags),
    )


@dataclass(frozen=True, slots=True)
class Lesson:
    """A general principle distilled from one or more experiences."""

    id: str
    principle: str
    context: str
    confidence: float
    evidence: tuple[str, ...]
    source_outcome: Outcome
    created_at: float
    agent_role: str
    tags: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this lesson."""
        return {
            "id": self.id,
            "principle": self.principle,
            "context": self.context,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "source_outcome": self.source_outcome.value,
            "created_at": self.created_at,
            "agent_role": self.agent_role,
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lesson:
        """Rebuild a lesson from :meth:`to_dict` output.

        Raises ``KeyError`` for a missing required field and ``ValueError`` for
        an unknown ``source_outcome``.
        """
        confidence = float(data["confidence"])
        return cls(
            id=str(data["id"]),
            principle=str(data["principle"]),
            context=str(data["context"]),
            confidence=min(1.0, max(0.0, confidence)),
            evidence=tuple(str(item) for item in data.get("evidence", ())),
            source_outcome=Outcome(str(data["source_outcome"])),
            created_at=float(data["created_at"]),
            agent_role=str(data["agent_role"]),
            tags=frozenset(str(item) for item in data.get("tags", ())),
        )


class LessonExtractor:
    """Distil :class:`Experience` records into :class:`Lesson` principles."""

    def __init__(
        self,
        backend: LLMBackend,
        collector: ExperienceCollector,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._collector = collector
        self._clock = clock

    # -- single experience --------------------------------------------------
    def extract_from_failure(self, experience: Experience) -> Lesson | None:
        """Extract a lesson from a failure experience (or ``None``)."""
        return self._extract_one(experience, _FAILURE_FOCUS)

    def extract_from_success(self, experience: Experience) -> Lesson | None:
        """Extract a lesson from a success experience (or ``None``)."""
        return self._extract_one(experience, _SUCCESS_FOCUS)

    # -- batches ------------------------------------------------------------
    def extract_batch(self, experiences: Sequence[Experience]) -> list[Lesson]:
        """Extract lessons from a batch, merging/deduping similar ones."""
        lessons: list[Lesson] = []
        for experience in experiences:
            focus = _FAILURE_FOCUS if experience.outcome is Outcome.FAILURE else _SUCCESS_FOCUS
            lesson = self._extract_one(experience, focus)
            if lesson is not None:
                lessons.append(lesson)
        return self._deduplicate(lessons)

    def extract_from_task(self, task_id: str) -> list[Lesson]:
        """Extract all lessons from the experiences of one task.

        Experiences are replayed oldest-first so evidence reads chronologically.
        """
        newest_first = self._collector.search(lambda e: e.task_id == task_id, limit=1_000_000)
        return self.extract_batch(list(reversed(newest_first)))

    # -- extraction internals ----------------------------------------------
    @staticmethod
    def _experience_key(experience: Experience) -> str:
        return f"{experience.task_id}:{experience.step_id}"

    def _build_prompt(self, experience: Experience, focus: str) -> str:
        return (
            f"{focus}\n\n"
            f"Experience:\n"
            f"- agent_role: {experience.agent_role}\n"
            f"- action_type: {experience.action_type}\n"
            f"- outcome: {experience.outcome.value}\n"
            f"- action: {experience.action}\n"
            f"- result: {experience.result}\n"
            f"- task_id: {experience.task_id}\n"
            f"- step_id: {experience.step_id}\n"
        )

    def _extract_one(self, experience: Experience, focus: str) -> Lesson | None:
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=self._build_prompt(experience, focus)),
        ]
        try:
            reply = self._backend.generate(messages)
        except Exception as exc:
            # A single bad call must never kill a whole batch.
            logger.warning("lesson extraction: backend error: %s", exc)
            return None

        payload = self._parse_json(reply)
        if payload is None:
            logger.warning("lesson extraction: could not parse model reply")
            return None

        principle = str(payload.get("principle", "")).strip()
        if not principle:
            logger.warning("lesson extraction: reply had no principle")
            return None

        return Lesson(
            id=uuid.uuid4().hex,
            principle=principle,
            context=str(payload.get("context", "")).strip(),
            confidence=self._clamp(payload.get("confidence", 0.5)),
            evidence=(self._experience_key(experience),),
            source_outcome=experience.outcome,
            created_at=self._clock(),
            agent_role=experience.agent_role,
            tags=frozenset(
                str(tag).strip() for tag in (payload.get("tags") or []) if str(tag).strip()
            ),
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Pull the first JSON object out of a model reply (tolerantly)."""
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
            candidate = re.sub(r"\s*```$", "", candidate).strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end < start:
            return None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _clamp(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.5
        return round(min(1.0, max(0.0, number)), 3)

    # -- deduplication ------------------------------------------------------
    def _deduplicate(self, lessons: list[Lesson]) -> list[Lesson]:
        groups: list[list[Lesson]] = []
        for lesson in lessons:
            for group in groups:
                if self._similar(group[0].principle, lesson.principle):
                    group.append(lesson)
                    break
            else:
                groups.append([lesson])
        return [self._merge(group) for group in groups]

    @staticmethod
    def _similar(left: str, right: str) -> bool:
        return principles_similar(left, right)

    def _merge(self, group: list[Lesson]) -> Lesson:
        return merge_lessons(group)

    @staticmethod
    def _confidence(base: float, evidence_count: int, contradictory: bool) -> float:
        """Blend the model's confidence with the support behind the lesson."""
        return blend_confidence(base, evidence_count, contradictory)