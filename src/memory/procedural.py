"""Procedural memory — reusable *skills* (how to do things).

A skill is a named procedure: a description, an ordered list of steps, worked
examples and free-form tags. Skills are retrieved by name or by relevance to the
current context (lexical match over name/description/tags/steps).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from src.memory.base import MemoryKind, MemoryRecord, MemoryStore, utc_iso
from src.memory.semantic import _lexical_score

__all__ = ["ProceduralMemory"]


class ProceduralMemory(MemoryStore):
    """Named skills with steps, examples and tags."""

    kind = MemoryKind.PROCEDURAL
    _table = "skills"

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
                tags        TEXT NOT NULL DEFAULT '[]'
            )
            """
        )

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
        """Create or replace a skill by name; return its row id."""
        ts = self.now()
        steps_json = json.dumps(list(steps))
        examples_json = json.dumps(list(examples))
        tags_json = json.dumps(list(tags))

        existing = self._query_one("SELECT id FROM skills WHERE name = ?", (name,))
        if existing is not None:
            self._execute(
                """
                UPDATE skills
                SET ts = ?, iso = ?, description = ?, steps = ?, examples = ?, tags = ?
                WHERE id = ?
                """,
                (ts, utc_iso(ts), description, steps_json, examples_json, tags_json,
                 int(existing["id"])),
            )
            return int(existing["id"])

        cursor = self._execute(
            """
            INSERT INTO skills (ts, iso, name, description, steps, examples, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, utc_iso(ts), name, description, steps_json, examples_json, tags_json),
        )
        return int(cursor.lastrowid or 0)

    def forget(self, name: str) -> bool:
        """Delete a skill by name; return whether one was removed."""
        cursor = self._execute("DELETE FROM skills WHERE name = ?", (name,))
        return cursor.rowcount > 0

    # -- read --------------------------------------------------------------
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
                },
            )
            for row in rows
        ]

    def get(self, name: str) -> MemoryRecord | None:
        """One skill by exact name, or ``None``."""
        row = self._query_one("SELECT * FROM skills WHERE name = ?", (name,))
        found = self._rows_to_records([row] if row is not None else [])
        return found[0] if found else None

    def all(self) -> list[MemoryRecord]:
        """Every skill, ordered by name."""
        return self._rows_to_records(self._query("SELECT * FROM skills ORDER BY name ASC"))

    def find(self, query: str, *, limit: int = 3) -> list[MemoryRecord]:
        """Skills most relevant to ``query``, best first."""
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in self._query("SELECT * FROM skills"):
            haystack = " ".join(
                [
                    str(row["name"]),
                    str(row["description"]),
                    " ".join(json.loads(str(row["tags"]))),
                    " ".join(json.loads(str(row["steps"]))),
                ]
            )
            score = _lexical_score(query, haystack)
            if score > 0.0:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return self._rows_to_records([row for _, row in scored[:limit]])
