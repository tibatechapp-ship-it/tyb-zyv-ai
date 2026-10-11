"""Anti-forgetting layer — guard the lesson store against catastrophic forgetting
(Phase 6).

When a continual learner keeps folding new experiences into one store, two
failure modes appear:

* **silent overwrite** — a new (possibly noisy) lesson merges into an important
  old one and washes it out;
* **contradiction collapse** — a lesson that directly *contradicts* an existing
  one ("never X" vs "always X") gets merged as if it agreed, destroying meaning.

:class:`AntiForgetting` sits between the learner and the store and adds three
protections:

* **protection** — *core* lessons (manually pinned, matched by a predicate, or
  simply high-confidence + well-supported) keep their identity through every
  merge; their confidence is never silently traded away.
* **conflict detection** — a new lesson whose principle is topically the same as
  an existing one but with the **opposite polarity** ("always" ⇄ "never") is
  reported as a :class:`Conflict` and stored *alongside* instead of overwritten.
* **sparse updates** — consolidation touches **only** the entry that the incoming
  lesson actually relates to (or adds a new one); every unrelated lesson is left
  byte-for-byte untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any

from src.learning.lesson import (
    Lesson,
    _tokenize,
    merge_lessons,
    principles_similar,
)
from src.learning.lesson_store import LessonStore

__all__ = ["AntiForgetting", "Conflict", "ConsolidationResult"]

#: Words that flip a principle's polarity ("always X" vs "never X").
_NEGATIVE = frozenset({"never", "avoid", "dont", "don't", "not", "no", "without"})
_POSITIVE = frozenset({"always", "should", "must", "prefer", "recommended"})

#: Topic similarity (over non-polarity tokens) for a possible conflict.
_DEFAULT_TOPIC_THRESHOLD = 0.5


def polarity(principle: str) -> int:
    """Return +1 / -1 / 0 for a principle's polarity (positive / negative / none)."""
    tokens = _tokenize(principle)
    if tokens & _NEGATIVE:
        return -1
    if tokens & _POSITIVE:
        return 1
    return 0


def _topic_tokens(principle: str) -> frozenset[str]:
    return _tokenize(principle) - _NEGATIVE - _POSITIVE


def _topic_similar(left: str, right: str, threshold: float) -> bool:
    left_tokens, right_tokens = _topic_tokens(left), _topic_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) >= threshold


@dataclass(frozen=True, slots=True)
class Conflict:
    """An incoming lesson that contradicts an existing one."""

    existing: Lesson
    incoming: Lesson

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this conflict."""
        return {
            "existing": self.existing.to_dict(),
            "incoming": self.incoming.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    """What :meth:`AntiForgetting.consolidate` did with one lesson."""

    lesson: Lesson
    action: str  # "added" | "merged" | "conflict"
    conflict: Conflict | None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this result."""
        return {
            "lesson": self.lesson.to_dict(),
            "action": self.action,
            "conflict": None if self.conflict is None else self.conflict.to_dict(),
        }


class AntiForgetting:
    """Protection- and conflict-aware consolidation over a :class:`LessonStore`."""

    def __init__(
        self,
        store: LessonStore,
        *,
        protection_threshold: float = 0.8,
        min_evidence: int = 3,
        protect: Callable[[Lesson], bool] | None = None,
        topic_threshold: float = _DEFAULT_TOPIC_THRESHOLD,
    ) -> None:
        self._store = store
        self._protection_threshold = protection_threshold
        self._min_evidence = min_evidence
        self._protect = protect
        self._topic_threshold = topic_threshold
        self._protected: set[str] = set()
        self._conflicts: list[Conflict] = []
        self._lock = RLock()

    # -- protection ---------------------------------------------------------
    def protect(self, lesson_id: str) -> None:
        """Pin a lesson id so it survives every future consolidation."""
        with self._lock:
            self._protected.add(lesson_id)

    def unprotect(self, lesson_id: str) -> bool:
        """Un-pin a lesson id; returns whether it was pinned."""
        with self._lock:
            if lesson_id in self._protected:
                self._protected.discard(lesson_id)
                return True
            return False

    def is_protected(self, lesson: Lesson) -> bool:
        """Whether a lesson is protected (explicitly, by predicate, or by value)."""
        with self._lock:
            if lesson.id in self._protected:
                return True
            if self._protect is not None and self._protect(lesson):
                return True
            return (
                lesson.confidence >= self._protection_threshold
                and len(lesson.evidence) >= self._min_evidence
            )

    def protected_ids(self) -> frozenset[str]:
        """The explicitly-pinned lesson ids."""
        with self._lock:
            return frozenset(self._protected)

    def protect_valuable(self) -> int:
        """Pin every currently-stored lesson that the value rule protects.

        Returns how many (newly) pinned ids were added — the persisted core set
        of a long-running store.
        """
        pinned = 0
        with self._lock:
            for lesson in self._store.all():
                if self.is_protected(lesson) and lesson.id not in self._protected:
                    self._protected.add(lesson.id)
                    pinned += 1
        return pinned

    # -- conflict detection -------------------------------------------------
    def detect_conflicts(self, lesson: Lesson) -> list[Conflict]:
        """Every stored lesson that is topically the same but opposite in polarity."""
        return [
            Conflict(existing, lesson)
            for existing in self._store.all()
            if existing.id != lesson.id and self._contradicts(existing, lesson)
        ]

    def _contradicts(self, existing: Lesson, incoming: Lesson) -> bool:
        existing_polarity = polarity(existing.principle)
        incoming_polarity = polarity(incoming.principle)
        if existing_polarity == 0 or incoming_polarity == 0:
            return False
        if existing_polarity == incoming_polarity:
            return False
        return _topic_similar(existing.principle, incoming.principle, self._topic_threshold)

    @property
    def conflicts(self) -> tuple[Conflict, ...]:
        """Every conflict recorded so far, oldest first."""
        with self._lock:
            return tuple(self._conflicts)

    # -- consolidation ------------------------------------------------------
    def consolidate(self, lesson: Lesson) -> ConsolidationResult:
        """Add or merge ``lesson`` — sparsely, and never across a contradiction.

        * a contradiction is recorded and the incoming lesson is **added
          alongside** (the existing entry is untouched) → ``"conflict"``;
        * a similar existing lesson (same polarity) is merged, preserving the
          protected status of the entry → ``"merged"``;
        * otherwise the lesson is added untouched → ``"added"``.
        """
        with self._lock:
            conflicts = self.detect_conflicts(lesson)
            if conflicts:
                self._conflicts.extend(conflicts)
                stored = self._store.add(lesson)
                return ConsolidationResult(stored, "conflict", conflicts[0])

            existing = self._find_similar(lesson)
            if existing is None:
                return ConsolidationResult(self._store.add(lesson), "added", None)

            merged = merge_lessons([existing, lesson])
            self._store.remove(existing.id)
            self._store.add(merged)
            if existing.id in self._protected:
                self._protected.discard(existing.id)
                self._protected.add(merged.id)
            return ConsolidationResult(merged, "merged", None)

    def _find_similar(self, lesson: Lesson) -> Lesson | None:
        for existing in self._store.all():
            if existing.id != lesson.id and principles_similar(
                existing.principle, lesson.principle
            ):
                return existing
        return None

    # -- reinforcement ------------------------------------------------------
    def reinforce(self, lesson_id: str, *, amount: float = 0.1) -> Lesson | None:
        """Nudge a lesson's confidence upward (spaced repetition); returns it."""
        with self._lock:
            existing = self._store.get(lesson_id)
            if existing is None:
                return None
            bumped = Lesson(
                id=existing.id,
                principle=existing.principle,
                context=existing.context,
                confidence=round(min(1.0, existing.confidence + amount), 3),
                evidence=existing.evidence,
                source_outcome=existing.source_outcome,
                created_at=existing.created_at,
                agent_role=existing.agent_role,
                tags=existing.tags,
            )
            self._store.add(bumped)
            return bumped

    def resolve(self, conflict: Conflict, *, keep: str = "confident") -> Lesson | None:
        """Resolve a conflict by dropping one side.

        ``keep`` is ``"existing"``, ``"incoming"`` or ``"confident"`` (the higher
        confidence wins, ties to the existing lesson). A protected lesson is
        never dropped — if the loser is protected this returns ``None`` and the
        conflict is left standing for the operator to decide.
        """
        with self._lock:
            if keep == "existing":
                winner, loser = conflict.existing, conflict.incoming
            elif keep == "incoming":
                winner, loser = conflict.incoming, conflict.existing
            else:
                if conflict.incoming.confidence > conflict.existing.confidence:
                    winner, loser = conflict.incoming, conflict.existing
                else:
                    winner, loser = conflict.existing, conflict.incoming
            if self.is_protected(loser):
                return None  # never drop a protected lesson
            self._store.add(winner)
            self._store.remove(loser.id)
            self._protected.discard(loser.id)
            return winner
