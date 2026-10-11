"""Procedural memory — reusable *skills* (how to do things).

A skill is a named procedure: a description, an ordered list of steps, worked
examples and free-form tags. Skills also carry a track record — how many times
they were used and how often that succeeded — so the best ones rise to the top.

The store doubles as a **skills registry** (:data:`SkillsRegistry`):

* :meth:`learn` / :meth:`get` / :meth:`all` — manage skills;
* :meth:`find` — retrieval by relevance to the current context (cosine when an
  embedder is configured, lexical otherwise);
* :meth:`record_use` / :meth:`top_skills` — learn from outcomes;
* :meth:`learn_from_task` — auto-learn a skill from a task that succeeded.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.memory.base import Clock, MemoryKind, MemoryRecord, MemoryStore, utc_iso
from src.memory.embeddings import Embedder, cosine
from src.memory.semantic import _lexical_score

__all__ = ["ProceduralMemory", "Skill", "SkillsRegistry"]

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str, words: int = 3) -> str:
    """A short, stable skill name from a task string (first few words)."""
    cleaned = _SLUG.sub("_", text.lower()).strip("_")
    parts = [part for part in cleaned.split("_") if part][:words]
    return "_".join(parts) or "task"


@dataclass(frozen=True, slots=True)
class Skill:
    """A typed view of one stored skill with its success rate."""

    name: str
    description: str
    steps: tuple[str, ...]
    examples: tuple[str, ...]
    tags: tuple[str, ...]
    uses: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        """Successful uses over total uses (0.0 when never used)."""
        return self.successes / self.uses if self.uses else 0.0

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view."""
        return {
            "name": self.name,
            "description": self.description,
            "steps": list(self.steps),
            "examples": list(self.examples),
            "tags": list(self.tags),
            "uses": self.uses,
            "successes": self.successes,
            "success_rate": self.success_rate,
        }


class ProceduralMemory(MemoryStore):
    """Named skills with steps, examples, tags and a success track record."""

    kind = MemoryKind.PROCEDURAL
    _table = "skills"

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Clock = time.time,
        embedder: Embedder | None = None,
    ) -> None:
        self._embedder = embedder
        super().__init__(connection, clock=clock)

    def _init_schema(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                iso         TEXT NOT NULL,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                steps       TEXT NOT NULL DEFAULT '[]',
                examples    TEXT NOT NULL DEFAULT '[]',
                tags        TEXT NOT NULL DEFAULT '[]',
                uses        INTEGER NOT NULL DEFAULT 0,
                successes   INTEGER NOT NULL DEFAULT 0,
                embedding   TEXT
            )
            """
        )
        # Upgrade pre-existing databases that predate the track-record columns.
        self._ensure_column("skills", "uses", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("skills", "successes", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("skills", "embedding", "TEXT")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self._query(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # -- write -------------------------------------------------------------
    def learn(
        self,
        name: str,
        description: str,
        *,
        steps: Sequence[str] = (),
        examples: Sequence[str] = (),
        tags: Sequence[str] = (),
    ) -> int:
        """Create or replace a skill by name; return its row id.

        Replacing keeps the existing track record (uses/successes).
        """
        ts = self.now()
        embedding = self._embed_text(f"{name} {description} {' '.join(tags)}")
        existing = self._query_one("SELECT id FROM skills WHERE name = ?", (name,))
        if existing is not None:
            self._execute(
                """
                UPDATE skills
                SET ts = ?, iso = ?, description = ?, steps = ?, examples = ?, tags = ?,
                    embedding = COALESCE(?, embedding)
                WHERE id = ?
                """,
                (
                    ts, utc_iso(ts), description, json.dumps(list(steps)),
                    json.dumps(list(examples)), json.dumps(list(tags)), embedding,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cursor = self._execute(
            """
            INSERT INTO skills
                (ts, iso, name, description, steps, examples, tags, uses, successes, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                ts, utc_iso(ts), name, description, json.dumps(list(steps)),
                json.dumps(list(examples)), json.dumps(list(tags)), embedding,
            ),
        )
        return int(cursor.lastrowid or 0)

    def forget(self, name: str) -> bool:
        """Delete a skill by name; return whether one was removed."""
        cursor = self._execute("DELETE FROM skills WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def record_use(self, name: str, *, ok: bool = True) -> float | None:
        """Bump a skill's use counter; return its new success rate, or ``None``."""
        row = self._query_one(
            "SELECT id, uses, successes FROM skills WHERE name = ?", (name,)
        )
        if row is None:
            return None
        uses = int(row["uses"]) + 1
        successes = int(row["successes"]) + (1 if ok else 0)
        self._execute(
            "UPDATE skills SET uses = ?, successes = ? WHERE id = ?",
            (uses, successes, int(row["id"])),
        )
        return successes / uses

    def learn_from_task(
        self,
        task: str,
        *,
        ok: bool = True,
        steps: Sequence[str] = (),
        examples: Sequence[str] = (),
        tags: Sequence[str] = (),
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Auto-learn a skill from a task — only when it succeeded.

        Creates the skill if new (name defaulting to a slug of the task), then
        records one successful use. Returns the skill as a dict, or ``None`` when
        ``ok`` is false (we do not learn from failures).
        """
        if not ok:
            return None
        skill_name = name or _slug(task)
        if self.get(skill_name) is None:
            self.learn(
                skill_name,
                description or task,
                steps=steps,
                examples=examples,
                tags=tags,
            )
        self.record_use(skill_name, ok=True)
        record = self.get(skill_name)
        return record.data if record is not None else None

    # -- views -------------------------------------------------------------
    def _embed_text(self, text: str) -> str | None:
        if self._embedder is None:
            return None
        return json.dumps(list(self._embedder(text)))

    def _row_to_skill(self, row: sqlite3.Row) -> Skill:
        return Skill(
            name=str(row["name"]),
            description=str(row["description"]),
            steps=tuple(json.loads(str(row["steps"]))),
            examples=tuple(json.loads(str(row["examples"]))),
            tags=tuple(json.loads(str(row["tags"]))),
            uses=int(row["uses"]),
            successes=int(row["successes"]),
        )

    def _rows_to_records(self, rows: Sequence[sqlite3.Row]) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={
                    "name": row["name"],
                    "description": row["description"],
                    "steps": json.loads(str(row["steps"])),
                    "examples": json.loads(str(row["examples"])),
                    "tags": json.loads(str(row["tags"])),
                    "uses": int(row["uses"]),
                    "successes": int(row["successes"]),
                    "success_rate": (int(row["successes"]) / int(row["uses"]))
                    if int(row["uses"])
                    else 0.0,
                },
            )
            for row in rows
        ]

    def get(self, name: str) -> MemoryRecord | None:
        """One skill by exact name, or ``None``."""
        row = self._query_one("SELECT * FROM skills WHERE name = ?", (name,))
        found = self._rows_to_records([row] if row is not None else [])
        return found[0] if found else None

    def skill(self, name: str) -> Skill | None:
        """One skill as a typed :class:`Skill`, or ``None``."""
        row = self._query_one("SELECT * FROM skills WHERE name = ?", (name,))
        return self._row_to_skill(row) if row is not None else None

    def all(self) -> list[MemoryRecord]:
        """Every skill, ordered by name."""
        return self._rows_to_records(self._query("SELECT * FROM skills ORDER BY name ASC"))

    def all_skills(self) -> list[Skill]:
        """Every skill as typed :class:`Skill` views, ordered by name."""
        rows = self._query("SELECT * FROM skills ORDER BY name ASC")
        return [self._row_to_skill(row) for row in rows]

    def top_skills(self, limit: int = 5) -> list[MemoryRecord]:
        """The best-performing skills (highest success rate, then most used)."""
        rows = self._query(
            """
            SELECT * FROM skills
            ORDER BY (CAST(successes AS REAL) / NULLIF(uses, 0)) DESC, uses DESC, name ASC
            LIMIT ?
            """,
            (int(limit),),
        )
        return self._rows_to_records(rows)

    # -- retrieval ---------------------------------------------------------
    def find(self, query: str, *, limit: int = 3) -> list[MemoryRecord]:
        """Skills most relevant to ``query``, best first."""
        rows = self._query("SELECT * FROM skills")
        if self._embedder is not None:
            return self._vector_find(query, rows, limit)
        return self._lexical_find(query, rows, limit)

    def _skill_text(self, row: sqlite3.Row) -> str:
        return " ".join(
            [
                str(row["name"]),
                str(row["description"]),
                " ".join(json.loads(str(row["tags"]))),
                " ".join(json.loads(str(row["steps"]))),
            ]
        )

    def _lexical_find(
        self, query: str, rows: Sequence[sqlite3.Row], limit: int
    ) -> list[MemoryRecord]:
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            score = _lexical_score(query, self._skill_text(row))
            if score > 0.0:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return self._rows_to_records([row for _, row in scored[:limit]])

    def _vector_find(
        self, query: str, rows: Sequence[sqlite3.Row], limit: int
    ) -> list[MemoryRecord]:
        assert self._embedder is not None
        query_vector = list(self._embedder(query))
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            raw = row["embedding"]
            if raw:
                score = cosine(query_vector, json.loads(str(raw)))
            else:
                score = _lexical_score(query, self._skill_text(row))
            if score > 0.0:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return self._rows_to_records([row for _, row in scored[:limit]])


#: The procedural memory *is* the skills registry.
SkillsRegistry = ProceduralMemory
