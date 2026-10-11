"""Semantic memory — durable *facts* (subject → key → value).

Facts are stored as rows and retrieved either by exact key or by relevance.
Phase 1 ranks by lexical (token-overlap) relevance, which needs no dependencies;
an optional :class:`Embedder` can be supplied so ``remember`` stores a vector and
``search`` ranks by cosine similarity instead — the Phase 2 upgrade is a
drop-in, no API change.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections.abc import Sequence
from typing import Protocol

from src.memory.base import Clock, MemoryKind, MemoryRecord, MemoryStore, utc_iso

__all__ = ["Embedder", "SemanticMemory"]

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Lower-cased word tokens (Unicode-aware, so Arabic words survive)."""
    return {word for word in _WORD.findall(text.lower()) if len(word) > 1}


def _lexical_score(query: str, text: str) -> float:
    """Fraction of the query's tokens present in ``text`` (0.0–1.0)."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokens(text)) / len(query_tokens)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on mismatch/zero)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class Embedder(Protocol):
    """Anything that maps text to a dense vector (e.g. a model client)."""

    def __call__(self, text: str) -> Sequence[float]: ...


class SemanticMemory(MemoryStore):
    """Long-term facts, retrievable by key or by relevance."""

    kind = MemoryKind.SEMANTIC
    _table = "facts"

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
            CREATE TABLE IF NOT EXISTS facts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL NOT NULL,
                iso       TEXT NOT NULL,
                subject   TEXT NOT NULL DEFAULT '',
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                source    TEXT NOT NULL DEFAULT '',
                embedding TEXT,
                UNIQUE (subject, key)
            )
            """
        )

    # -- write -------------------------------------------------------------
    def remember(
        self, key: str, value: str, *, subject: str = "", source: str = ""
    ) -> int:
        """Store/update a fact ``(subject, key) -> value``; return its row id."""
        ts = self.now()
        embedding: str | None = None
        if self._embedder is not None:
            embedding = json.dumps(list(self._embedder(f"{subject} {key} {value}")))

        existing = self._query_one(
            "SELECT id FROM facts WHERE subject = ? AND key = ?", (subject, key)
        )
        if existing is not None:
            self._execute(
                """
                UPDATE facts SET ts = ?, iso = ?, value = ?, source = ?,
                    embedding = COALESCE(?, embedding)
                WHERE id = ?
                """,
                (ts, utc_iso(ts), value, source, embedding, int(existing["id"])),
            )
            return int(existing["id"])

        cursor = self._execute(
            """
            INSERT INTO facts (ts, iso, subject, key, value, source, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, utc_iso(ts), subject, key, value, source, embedding),
        )
        return int(cursor.lastrowid or 0)

    def forget(self, key: str, *, subject: str = "") -> bool:
        """Delete a fact; return whether one was removed."""
        cursor = self._execute(
            "DELETE FROM facts WHERE subject = ? AND key = ?", (subject, key)
        )
        return cursor.rowcount > 0

    # -- read --------------------------------------------------------------
    def recall(self, key: str, *, subject: str = "") -> str | None:
        """The value of ``(subject, key)``, or ``None`` if unknown."""
        row = self._query_one(
            "SELECT value FROM facts WHERE subject = ? AND key = ?", (subject, key)
        )
        return str(row["value"]) if row is not None else None

    def _rows_to_records(self, rows: Sequence[sqlite3.Row]) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={
                    "subject": row["subject"],
                    "key": row["key"],
                    "value": row["value"],
                    "source": row["source"],
                },
            )
            for row in rows
        ]

    def all(self) -> list[MemoryRecord]:
        """Every fact, ordered by subject then key."""
        rows = self._query("SELECT * FROM facts ORDER BY subject ASC, key ASC")
        return self._rows_to_records(rows)

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        """Facts most relevant to ``query`` (cosine if embedded, else lexical)."""
        rows = self._query("SELECT * FROM facts")
        if self._embedder is not None:
            return self._vector_search(query, rows, limit)
        return self._lexical_search(query, rows, limit)

    def _lexical_search(
        self, query: str, rows: Sequence[sqlite3.Row], limit: int
    ) -> list[MemoryRecord]:
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            text = f"{row['subject']} {row['key']} {row['value']}"
            score = _lexical_score(query, text)
            if score > 0.0:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return self._rows_to_records([row for _, row in scored[:limit]])

    def _vector_search(
        self, query: str, rows: Sequence[sqlite3.Row], limit: int
    ) -> list[MemoryRecord]:
        assert self._embedder is not None
        query_vector = list(self._embedder(query))
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            raw = row["embedding"]
            if raw:
                score = _cosine(query_vector, json.loads(str(raw)))
            else:
                score = _lexical_score(query, f"{row['subject']} {row['key']} {row['value']}")
            if score > 0.0:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return self._rows_to_records([row for _, row in scored[:limit]])
