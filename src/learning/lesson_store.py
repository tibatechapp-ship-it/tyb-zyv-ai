"""Lesson store + injection — feed learned lessons back into agent decisions.

Phase 3 closes the learning loop: :class:`~src.learning.lesson.LessonExtractor`
distils experiences into :class:`~src.learning.lesson.Lesson` principles, and
this module *uses* them. A :class:`LessonStore` keeps the lessons and ranks the
ones relevant to a task; :func:`render_lessons` formats them as a prompt block;
:class:`LessonInjector` wires that block into any agent's run loop (the block is
fused into the **user turn**, never the persona — same rule as context).

Phase 4 adds **durable persistence**: :class:`LessonDatabase` is a small SQLite
wrapper (one ``lessons`` table) and :meth:`LessonStore.save_to_db` /
:meth:`LessonStore.load_from_db` move lessons to and from it (with a versioned
:meth:`LessonDatabase.migrate_schema`). The in-memory store stays the fast
working copy; the database is the durable record.

Design notes:

* the store is an in-memory, thread-safe index (RLock) with JSON / JSONL
  export/import — the same posture as :class:`ExperienceCollector`;
* persistence is plain SQLite (via :func:`src.memory.base.connect`) so the whole
  subsystem still works on a phone with no extra dependencies;
* relevance is deterministic: token overlap with the query, plus small bonuses
  for a matching agent role, matching tags, and the lesson's own confidence;
* injection is **opt-in** and backward-compatible — an agent told nothing about
  lessons behaves exactly as before (extra bytes appear only when lessons are
  supplied).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from src.core.types import AgentResult
from src.learning.experience_collector import Outcome
from src.learning.lesson import Lesson, _tokenize, merge_lessons, principles_similar
from src.memory.base import connect

__all__ = [
    "LessonDatabase",
    "LessonInjector",
    "LessonStore",
    "default_lesson_db_path",
    "render_lessons",
]

logger = logging.getLogger(__name__)

#: Schema version written to ``PRAGMA user_version`` of the lessons database.
_SCHEMA_VERSION = 1


#: Relevance-weighting knobs (deterministic; tune in one place).
_LEXICAL_WEIGHT = 1.0
_TAG_WEIGHT = 0.5
_ROLE_BONUS = 0.3
_CONFIDENCE_WEIGHT = 0.1


class _LessonAware(Protocol):
    """Anything that can take an injected lessons block (an agent, in practice)."""

    name: str

    def run(self, task: str, *, context: str = "", lessons: str = "") -> AgentResult:
        raise NotImplementedError


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Token-set Jaccard similarity (0.0 when either side is empty)."""
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def render_lessons(lessons: Iterable[Lesson]) -> str:
    """Render lessons as a compact, human-readable prompt block.

    Each line is ``- (confidence) principle`` (the context, when present, is
    appended after an em dash). An empty iterable renders to ``""`` so callers
    can append it unconditionally.
    """
    lines: list[str] = []
    for lesson in lessons:
        line = f"- ({lesson.confidence:.2f}) {lesson.principle}"
        if lesson.context:
            line += f" — {lesson.context}"
        lines.append(line)
    return "\n".join(lines)


def default_lesson_db_path() -> Path:
    """Where the lessons database lives when the caller gives no path.

    ``$ZYVO_LESSON_DB`` wins (handy in tests/containers); otherwise
    ``~/.local/share/tyb-zyv-ai/lessons.db``.
    """
    override = os.environ.get("ZYVO_LESSON_DB")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "tyb-zyv-ai" / "lessons.db"


class LessonDatabase:
    """SQLite persistence for :class:`Lesson` records (one ``lessons`` table).

    The schema is versioned through ``PRAGMA user_version``;
    :meth:`migrate_schema` is idempotent and safe to call on every open. Rows are
    keyed by lesson id, so :meth:`save` upserts and re-saving an updated lesson
    overwrites the old row.
    """

    def __init__(self, path: str | os.PathLike[str] = ":memory:") -> None:
        self._connection = connect(path)
        self._lock = RLock()
        self.migrate_schema()

    # -- schema ------------------------------------------------------------
    def migrate_schema(self) -> None:
        """Create or upgrade the ``lessons`` table to the current version."""
        with self._lock:
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    id             TEXT PRIMARY KEY,
                    principle      TEXT NOT NULL,
                    context        TEXT NOT NULL DEFAULT '',
                    confidence     REAL NOT NULL DEFAULT 0.0,
                    evidence       TEXT NOT NULL DEFAULT '[]',
                    source_outcome TEXT NOT NULL,
                    created_at     REAL NOT NULL DEFAULT 0.0,
                    agent_role     TEXT NOT NULL DEFAULT '',
                    tags           TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            if version < _SCHEMA_VERSION:
                # v0 -> v1: the table itself is the migration (created above).
                self._connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying SQLite connection."""
        return self._connection

    # -- write / read ------------------------------------------------------
    def save(self, lessons: Iterable[Lesson]) -> int:
        """Upsert every lesson; returns how many were written."""
        rows = [
            (
                lesson.id,
                lesson.principle,
                lesson.context,
                lesson.confidence,
                json.dumps(list(lesson.evidence), ensure_ascii=False),
                lesson.source_outcome.value,
                lesson.created_at,
                lesson.agent_role,
                json.dumps(sorted(lesson.tags), ensure_ascii=False),
            )
            for lesson in lessons
        ]
        with self._lock:
            self._connection.executemany(
                """
                INSERT INTO lessons (
                    id, principle, context, confidence,
                    evidence, source_outcome, created_at, agent_role, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    principle=excluded.principle,
                    context=excluded.context,
                    confidence=excluded.confidence,
                    evidence=excluded.evidence,
                    source_outcome=excluded.source_outcome,
                    created_at=excluded.created_at,
                    agent_role=excluded.agent_role,
                    tags=excluded.tags
                """,
                rows,
            )
            self._connection.commit()
        return len(rows)

    def load(self) -> list[Lesson]:
        """Read every lesson, newest first; malformed rows are skipped."""
        with self._lock:
            rows = list(self._connection.execute("SELECT * FROM lessons").fetchall())
        lessons: list[Lesson] = []
        for row in rows:
            try:
                lessons.append(self._row_to_lesson(row))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("lesson database: skipping row %r: %s", row["id"], exc)
        lessons.sort(key=lambda lesson: lesson.created_at, reverse=True)
        return lessons

    def count(self) -> int:
        """How many lessons the table holds."""
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) FROM lessons").fetchone()
        return int(row[0]) if row is not None else 0

    def clear(self) -> None:
        """Delete every stored lesson."""
        with self._lock:
            self._connection.execute("DELETE FROM lessons")
            self._connection.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._connection.close()

    @staticmethod
    def _row_to_lesson(row: sqlite3.Row) -> Lesson:
        return Lesson(
            id=str(row["id"]),
            principle=str(row["principle"]),
            context=str(row["context"]),
            confidence=float(row["confidence"]),
            evidence=tuple(str(item) for item in json.loads(row["evidence"])),
            source_outcome=Outcome(str(row["source_outcome"])),
            created_at=float(row["created_at"]),
            agent_role=str(row["agent_role"]),
            tags=frozenset(str(item) for item in json.loads(row["tags"])),
        )


class LessonStore:
    """Thread-safe, in-memory index of :class:`Lesson` principles."""

    def __init__(self, *, database: LessonDatabase | None = None) -> None:
        self._lessons: list[Lesson] = []
        self._by_id: dict[str, Lesson] = {}
        self._lock = RLock()
        self._database = database

    # -- mutation -----------------------------------------------------------
    def add(self, lesson: Lesson) -> Lesson:
        """Add a lesson, replacing any existing one with the same id (upsert)."""
        with self._lock:
            existing = self._by_id.get(lesson.id)
            if existing is not None:
                index = self._lessons.index(existing)
                self._lessons[index] = lesson
            else:
                self._lessons.append(lesson)
            self._by_id[lesson.id] = lesson
            return lesson

    def add_many(self, lessons: Iterable[Lesson]) -> int:
        """Add several lessons; returns how many were stored."""
        count = 0
        for lesson in lessons:
            self.add(lesson)
            count += 1
        return count

    def merge(self, lesson: Lesson) -> Lesson:
        """Fold ``lesson`` into the store against a similar existing lesson.

        If a stored lesson has a similar principle (token Jaccard ≥ 0.6) the two
        are merged — union evidence/tags, re-scored confidence, fresh id — and
        the merged lesson replaces the old entry. Otherwise ``lesson`` is added
        as-is. Returns the resulting (stored) lesson; a changed id signals a
        merge, an unchanged id signals a plain add.
        """
        with self._lock:
            for existing in self._lessons:
                if principles_similar(existing.principle, lesson.principle):
                    merged = merge_lessons([existing, lesson])
                    index = self._lessons.index(existing)
                    self._lessons[index] = merged
                    del self._by_id[existing.id]
                    self._by_id[merged.id] = merged
                    return merged
            return self.add(lesson)

    def remove(self, lesson_id: str) -> bool:
        """Remove a lesson by id; returns whether it existed."""
        with self._lock:
            lesson = self._by_id.pop(lesson_id, None)
            if lesson is None:
                return False
            self._lessons.remove(lesson)
            return True

    def clear(self) -> None:
        """Forget every lesson."""
        with self._lock:
            self._lessons.clear()
            self._by_id.clear()

    # -- reading ------------------------------------------------------------
    def get(self, lesson_id: str) -> Lesson | None:
        """Look up a lesson by id (or ``None``)."""
        with self._lock:
            return self._by_id.get(lesson_id)

    def all(self, limit: int | None = None) -> list[Lesson]:
        """Snapshot of every lesson, newest first (by ``created_at``)."""
        with self._lock:
            ordered = sorted(
                self._lessons, key=lambda lesson: lesson.created_at, reverse=True
            )
        return ordered if limit is None else ordered[:limit]

    def __len__(self) -> int:
        with self._lock:
            return len(self._lessons)

    def by_agent(self, agent_role: str, *, limit: int = 50) -> list[Lesson]:
        """Lessons distilled from ``agent_role``'s experiences (newest first)."""
        return self.search(lambda lesson: lesson.agent_role == agent_role, limit=limit)

    def by_tag(self, tag: str, *, limit: int = 50) -> list[Lesson]:
        """Lessons carrying ``tag`` (newest first)."""
        return self.search(lambda lesson: tag in lesson.tags, limit=limit)

    def by_outcome(self, outcome: Outcome, *, limit: int = 50) -> list[Lesson]:
        """Lessons whose dominant source outcome is ``outcome`` (newest first)."""
        return self.search(lambda lesson: lesson.source_outcome is outcome, limit=limit)

    def search(self, predicate: Callable[[Lesson], bool], *, limit: int = 50) -> list[Lesson]:
        """Return, newest first, the lessons matching ``predicate``."""
        matches = [lesson for lesson in self.all() if predicate(lesson)]
        return matches[:limit]

    def top(self, n: int = 10) -> list[Lesson]:
        """The ``n`` most confident lessons (ties broken newest first)."""
        with self._lock:
            ordered = sorted(
                self._lessons,
                key=lambda lesson: (lesson.confidence, lesson.created_at),
                reverse=True,
            )
        return ordered[:n]

    # -- relevance + prompt injection --------------------------------------
    def relevant(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        tags: Iterable[str] | None = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> list[Lesson]:
        """Rank lessons by relevance to ``query`` (best first).

        A lesson is a candidate when it clears ``min_confidence`` and either
        overlaps the query's tokens, shares a requested tag, or matches
        ``agent_role``. When ``query`` is blank there is nothing to match, so
        confidence alone decides the order.
        """
        query_tokens = _tokenize(query)
        wanted_tags = {str(tag).strip() for tag in (tags or ()) if str(tag).strip()}
        consider_matches = bool(query_tokens or wanted_tags or agent_role)

        scored: list[tuple[float, Lesson]] = []
        for lesson in self.all():
            if lesson.confidence < min_confidence:
                continue
            lesson_tokens = _tokenize(f"{lesson.principle} {lesson.context}") | _tokenize(
                " ".join(sorted(lesson.tags))
            )
            lexical = _jaccard(query_tokens, lesson_tokens) * _LEXICAL_WEIGHT
            tag_hits = len(wanted_tags & lesson.tags)
            tag_bonus = tag_hits * _TAG_WEIGHT
            role_bonus = _ROLE_BONUS if agent_role and lesson.agent_role == agent_role else 0.0
            matched = bool(lexical or tag_hits or role_bonus)
            if consider_matches and not matched:
                continue
            score = lexical + tag_bonus + role_bonus + lesson.confidence * _CONFIDENCE_WEIGHT
            scored.append((score, lesson))

        scored.sort(key=lambda item: (item[0], item[1].confidence, item[1].created_at), reverse=True)
        return [lesson for _, lesson in scored[:limit]]

    def build_context(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        tags: Iterable[str] | None = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> str:
        """Render the lessons relevant to ``query`` as a prompt block (or ``""``).

        Drop the result straight into an agent run:
        ``agent.run(task, lessons=store.build_context(task, agent_role=agent.name))``.
        """
        return render_lessons(
            self.relevant(
                query,
                agent_role=agent_role,
                tags=tags,
                limit=limit,
                min_confidence=min_confidence,
            )
        )

    # -- statistics ---------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """Summary counts over the stored lessons."""
        with self._lock:
            lessons = list(self._lessons)
        if not lessons:
            return {
                "total": 0,
                "avg_confidence": 0.0,
                "by_agent": {},
                "by_outcome": {},
                "by_tag": {},
            }
        return {
            "total": len(lessons),
            "avg_confidence": round(
                sum(lesson.confidence for lesson in lessons) / len(lessons), 3
            ),
            "by_agent": dict(Counter(lesson.agent_role for lesson in lessons).most_common()),
            "by_outcome": dict(
                Counter(lesson.source_outcome.value for lesson in lessons).most_common()
            ),
            "by_tag": dict(
                Counter(tag for lesson in lessons for tag in lesson.tags).most_common()
            ),
        }

    # -- export / import ----------------------------------------------------
    def export(self, path: str | Path) -> int:
        """Write every lesson to ``path`` as JSON; returns how many were written."""
        with self._lock:
            payload = [lesson.to_dict() for lesson in self._lessons]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(payload)

    def import_from(self, path: str | Path) -> int:
        """Load lessons from a JSON file; invalid items are skipped (returns added).

        Raises ``FileNotFoundError`` when ``path`` does not exist.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("lesson store file must contain a JSON array")
        return self._add_items(raw, "lesson store import")

    def export_to_jsonl(self, path: str | Path) -> int:
        """Write every lesson to ``path`` as JSON Lines; returns count written."""
        with self._lock:
            payload = [lesson.to_dict() for lesson in self._lessons]
        lines = "\n".join(json.dumps(item, ensure_ascii=False) for item in payload)
        Path(path).write_text(lines + ("\n" if lines else ""), encoding="utf-8")
        return len(payload)

    def import_from_jsonl(self, path: str | Path) -> int:
        """Load lessons from a JSONL file; blank/broken lines are skipped.

        Raises ``FileNotFoundError`` when ``path`` does not exist.
        """
        text = Path(path).read_text(encoding="utf-8")
        items: list[Any] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                items.append(json.loads(stripped))
            except json.JSONDecodeError:
                logger.warning("lesson store import: skipping malformed line")
        return self._add_items(items, "lesson store jsonl import")

    def _add_items(self, items: Iterable[Any], label: str) -> int:
        added = 0
        for item in items:
            if not isinstance(item, dict):
                logger.warning("%s: skipping non-object item", label)
                continue
            try:
                self.add(Lesson.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("%s: skipping invalid lesson: %s", label, exc)
                continue
            added += 1
        return added

    # -- SQLite persistence (Phase 4) --------------------------------------
    @property
    def database(self) -> LessonDatabase | None:
        """The configured :class:`LessonDatabase` (``None`` when unset)."""
        return self._database

    def _resolve_database(self, database: LessonDatabase | None) -> LessonDatabase:
        resolved = database if database is not None else self._database
        if resolved is None:
            raise ValueError(
                "no lesson database configured; pass one or construct the store "
                "with LessonStore(database=...)"
            )
        return resolved

    def save_to_db(self, database: LessonDatabase | None = None) -> int:
        """Persist every in-memory lesson to the database; returns how many."""
        return self._resolve_database(database).save(self.all())

    def load_from_db(self, database: LessonDatabase | None = None) -> int:
        """Load lessons from the database into memory; returns how many were added."""
        return self.add_many(self._resolve_database(database).load())

    def migrate_schema(self, database: LessonDatabase | None = None) -> None:
        """Ensure the database schema is current (safe to call repeatedly)."""
        self._resolve_database(database).migrate_schema()


class LessonInjector:
    """Wire a :class:`LessonStore` into any agent's run loop.

    The injector turns a task into the lessons relevant to that agent and hands
    them to :meth:`agent.run` as the ``lessons`` block — the agent never needs
    to know the store exists.
    """

    def __init__(
        self,
        store: LessonStore,
        *,
        limit: int = 5,
        min_confidence: float = 0.0,
        tags: Iterable[str] | None = None,
    ) -> None:
        self._store = store
        self._limit = limit
        self._min_confidence = min_confidence
        self._tags = tuple(tags or ())

    def context_for(self, task: str, *, agent_role: str | None = None) -> str:
        """The lessons block relevant to ``task`` (``""`` when nothing matches)."""
        return self._store.build_context(
            task,
            agent_role=agent_role,
            tags=self._tags,
            limit=self._limit,
            min_confidence=self._min_confidence,
        )

    def run(self, agent: _LessonAware, task: str, *, context: str = "") -> AgentResult:
        """Run ``agent`` on ``task`` with its relevant lessons injected."""
        lessons = self.context_for(task, agent_role=agent.name)
        return agent.run(task, context=context, lessons=lessons)
