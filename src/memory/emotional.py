"""Emotional memory — tone, preferences and satisfaction.

This store keeps three related things:

* **preferences** — how the user likes things (subject → key → value);
* **signals** — a log of detected tone in user messages;
* **satisfaction** — a 0.0–1.0 score per interaction.

Tone is detected with a small, dependency-free bilingual lexicon
(English + common Arabic cues). Phase 5 can swap in a real sentiment model via
the same :func:`analyze_tone` seam without touching callers.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from src.memory.base import MemoryKind, MemoryRecord, MemoryStore, utc_iso

__all__ = ["EmotionalMemory", "analyze_tone"]

_WORD = re.compile(r"\w+", re.UNICODE)

POSITIVE_WORDS: frozenset[str] = frozenset(
    {
        "good", "great", "awesome", "perfect", "thanks", "thank", "nice", "love",
        "excellent", "amazing", "helpful", "yes", "shukran", "zain", "mumtaz",
        "helw", "kwayes", "tamam", "afdal",
    }
)
NEGATIVE_WORDS: frozenset[str] = frozenset(
    {
        "bad", "wrong", "broken", "hate", "terrible", "awful", "useless", "error",
        "fail", "failed", "no", "slow", "confusing", "mesh", "zafta", "sayea",
        "ghalat", "mush", "batel",
    }
)


def analyze_tone(text: str) -> tuple[str, float]:
    """Classify ``text`` as ``positive``/``negative``/``neutral`` + score.

    The score is ``(positives - negatives) / total_hits`` clamped to [-1, 1];
    with no hits the label is ``neutral`` and the score is ``0.0``.
    """
    words = [word for word in _WORD.findall(text.lower())]
    if not words:
        return "neutral", 0.0
    positives = sum(1 for word in words if word in POSITIVE_WORDS)
    negatives = sum(1 for word in words if word in NEGATIVE_WORDS)
    hits = positives + negatives
    if hits == 0:
        return "neutral", 0.0
    score = (positives - negatives) / hits
    if score > 0.0:
        return "positive", score
    if score < 0.0:
        return "negative", score
    return "neutral", 0.0


class EmotionalMemory(MemoryStore):
    """Preferences, tone signals and satisfaction scores."""

    kind = MemoryKind.EMOTIONAL
    _table = "preferences"

    def _init_schema(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL,
                iso        TEXT NOT NULL,
                key        TEXT NOT NULL UNIQUE,
                value      TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL,
                iso        TEXT NOT NULL,
                session_id TEXT,
                text       TEXT NOT NULL,
                sentiment  TEXT NOT NULL,
                score      REAL NOT NULL
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS satisfaction (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL,
                iso        TEXT NOT NULL,
                session_id TEXT,
                score      REAL NOT NULL,
                note       TEXT NOT NULL DEFAULT ''
            )
            """
        )

    # -- preferences -------------------------------------------------------
    def set_preference(self, key: str, value: str, *, confidence: float = 1.0) -> int:
        """Create or update a preference; return its row id."""
        ts = self.now()
        existing = self._query_one("SELECT id FROM preferences WHERE key = ?", (key,))
        if existing is not None:
            self._execute(
                "UPDATE preferences SET ts = ?, iso = ?, value = ?, confidence = ? WHERE id = ?",
                (ts, utc_iso(ts), value, float(confidence), int(existing["id"])),
            )
            return int(existing["id"])
        cursor = self._execute(
            "INSERT INTO preferences (ts, iso, key, value, confidence) VALUES (?, ?, ?, ?, ?)",
            (ts, utc_iso(ts), key, value, float(confidence)),
        )
        return int(cursor.lastrowid or 0)

    def preference(self, key: str) -> str | None:
        """The value of a preference, or ``None``."""
        row = self._query_one("SELECT value FROM preferences WHERE key = ?", (key,))
        return str(row["value"]) if row is not None else None

    def preferences(self) -> list[MemoryRecord]:
        """Every preference, ordered by key."""
        rows = self._query("SELECT * FROM preferences ORDER BY key ASC")
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={"key": row["key"], "value": row["value"],
                      "confidence": float(row["confidence"])},
            )
            for row in rows
        ]

    # -- tone signals ------------------------------------------------------
    def record_signal(
        self,
        text: str,
        *,
        sentiment: str | None = None,
        score: float | None = None,
        session_id: str | None = None,
        timestamp: float | None = None,
    ) -> int:
        """Store one tone signal; classify ``text`` when label/score are omitted."""
        if sentiment is None or score is None:
            detected_label, detected_score = analyze_tone(text)
            sentiment = detected_label if sentiment is None else sentiment
            score = detected_score if score is None else score
        ts = self.now() if timestamp is None else timestamp
        cursor = self._execute(
            """
            INSERT INTO signals (ts, iso, session_id, text, sentiment, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, utc_iso(ts), session_id, text, sentiment, float(score)),
        )
        return int(cursor.lastrowid or 0)

    def signals(self, *, limit: int = 20) -> list[MemoryRecord]:
        """Recent tone signals, newest first."""
        rows = self._query(
            "SELECT * FROM signals ORDER BY ts DESC, id DESC LIMIT ?", (int(limit),)
        )
        return self._signals_to_records(rows)

    def all_signals(self) -> list[MemoryRecord]:
        """Every tone signal, oldest first (used for export)."""
        return self._signals_to_records(
            self._query("SELECT * FROM signals ORDER BY ts ASC, id ASC")
        )

    def _signals_to_records(self, rows: Sequence[sqlite3.Row]) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={"session_id": row["session_id"], "text": row["text"],
                      "sentiment": row["sentiment"], "score": float(row["score"])},
            )
            for row in rows
        ]

    # -- satisfaction ------------------------------------------------------
    def record_satisfaction(
        self,
        score: float,
        *,
        session_id: str | None = None,
        note: str = "",
        timestamp: float | None = None,
    ) -> int:
        """Store a 0.0–1.0 satisfaction score; return its row id."""
        clamped = max(0.0, min(1.0, float(score)))
        ts = self.now() if timestamp is None else timestamp
        cursor = self._execute(
            """
            INSERT INTO satisfaction (ts, iso, session_id, score, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, utc_iso(ts), session_id, clamped, note),
        )
        return int(cursor.lastrowid or 0)

    def satisfaction(self, *, limit: int = 50) -> list[MemoryRecord]:
        """Recent satisfaction scores, newest first."""
        rows = self._query(
            "SELECT * FROM satisfaction ORDER BY ts DESC, id DESC LIMIT ?", (int(limit),)
        )
        return self._satisfaction_to_records(rows)

    def all_satisfaction(self) -> list[MemoryRecord]:
        """Every satisfaction score, oldest first (used for export)."""
        return self._satisfaction_to_records(
            self._query("SELECT * FROM satisfaction ORDER BY ts ASC, id ASC")
        )

    def _satisfaction_to_records(self, rows: Sequence[sqlite3.Row]) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={"session_id": row["session_id"], "score": float(row["score"]),
                      "note": row["note"]},
            )
            for row in rows
        ]

    def average_satisfaction(self) -> float | None:
        """Mean satisfaction across all records, or ``None`` when empty."""
        row = self._query_one("SELECT AVG(score) AS avg FROM satisfaction")
        if row is None or row["avg"] is None:
            return None
        return float(row["avg"])

    # -- convenience -------------------------------------------------------
    def observe(self, text: str, *, session_id: str | None = None) -> tuple[str, float]:
        """Classify ``text`` and log it as a signal; return ``(label, score)``."""
        label, score = analyze_tone(text)
        self.record_signal(text, sentiment=label, score=score, session_id=session_id)
        return label, score
