"""Emotional memory — tone, preferences and satisfaction.

This store keeps three related things:

* **preferences** — how the user likes things (key → value + confidence);
* **signals** — a log of detected tone in user messages;
* **satisfaction** — a 0.0–1.0 score per interaction.

Tone is detected with a small, dependency-free bilingual lexicon
(English + common Arabic cues) that also understands simple negation
("not good" is negative). :meth:`EmotionalMemory.adjust_reply` uses the tone and
the stored preferences to soften or warm up a reply automatically.

A real sentiment model can replace :func:`analyze_tone` later without touching
callers.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from src.memory.base import MemoryKind, MemoryRecord, MemoryStore, utc_iso

__all__ = ["EmotionalMemory", "Tone", "analyze_tone", "classify"]

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
        "fail", "failed", "no", "slow", "confusing", "zafta", "sayea", "ghalat",
        "batel",
    }
)
#: Words that flip the polarity of the next sentiment word.
_NEGATORS: frozenset[str] = frozenset(
    {"not", "never", "dont", "didnt", "cant", "cannot", "mesh", "mush", "mish"}
)

#: Default reply prefixes/suffixes applied by :meth:`EmotionalMemory.adjust_reply`.
_DEFAULT_PREFIX: dict[str, str] = {
    "positive": "",
    "neutral": "",
    "negative": "Sorry about that — ",
}
_DEFAULT_SUFFIX: dict[str, str] = {"positive": "", "neutral": "", "negative": ""}


@dataclass(frozen=True, slots=True)
class Tone:
    """A detected tone: a ``label`` and a score in ``[-1, 1]``."""

    label: str
    score: float

    @property
    def is_negative(self) -> bool:
        """Whether the tone is negative."""
        return self.label == "negative"

    @property
    def is_positive(self) -> bool:
        """Whether the tone is positive."""
        return self.label == "positive"


def analyze_tone(text: str) -> tuple[str, float]:
    """Classify ``text`` as ``positive``/``negative``/``neutral`` + score.

    The score is ``(positives - negatives) / total_hits`` clamped to [-1, 1];
    a negator in front of a sentiment word flips its polarity. With no hits the
    label is ``neutral`` and the score is ``0.0``.
    """
    words = _WORD.findall(text.lower())
    if not words:
        return "neutral", 0.0
    total = 0
    hits = 0
    negate = False
    for word in words:
        if word in _NEGATORS:
            negate = True
            continue
        if word in POSITIVE_WORDS:
            polarity = 1
        elif word in NEGATIVE_WORDS:
            polarity = -1
        else:
            negate = False
            continue
        if negate:
            polarity = -polarity
            negate = False
        total += polarity
        hits += 1
    if hits == 0:
        return "neutral", 0.0
    score = max(-1.0, min(1.0, total / hits))
    if score > 0.0:
        return "positive", score
    if score < 0.0:
        return "negative", score
    return "neutral", 0.0


def classify(text: str) -> Tone:
    """Return the :class:`Tone` of ``text``."""
    label, score = analyze_tone(text)
    return Tone(label=label, score=score)


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

    def observe(self, text: str, *, session_id: str | None = None) -> tuple[str, float]:
        """Classify ``text`` and log it as a signal; return ``(label, score)``."""
        label, score = analyze_tone(text)
        self.record_signal(text, sentiment=label, score=score, session_id=session_id)
        return label, score

    def dominant_tone(self, *, limit: int = 50) -> str:
        """The most frequent recent tone label (``neutral`` when undecided)."""
        counts: dict[str, int] = {}
        for record in self.signals(limit=limit):
            label = str(record.data["sentiment"])
            counts[label] = counts.get(label, 0) + 1
        if not counts:
            return "neutral"
        return max(counts, key=lambda label: (counts[label], label))

    # -- adjust replies ----------------------------------------------------
    def adjust_reply(
        self,
        reply: str,
        *,
        user_tone: Tone | str | None = None,
        user_text: str | None = None,
    ) -> str:
        """Adapt ``reply`` to the user's tone using stored preferences.

        Tone is taken from ``user_tone`` (a :class:`Tone` or label) or detected
        from ``user_text``. A per-tone ``reply_prefix_<label>`` /
        ``reply_suffix_<label>`` preference overrides the built-in default.
        """
        tone = _resolve_tone(user_tone, user_text)
        label = tone.label
        prefix = self.preference(f"reply_prefix_{label}")
        if prefix is None:
            prefix = _DEFAULT_PREFIX.get(label, "")
        suffix = self.preference(f"reply_suffix_{label}") or _DEFAULT_SUFFIX.get(label, "")
        return f"{prefix}{reply}{suffix}"

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

    def latest_satisfaction(self) -> float | None:
        """The most recent satisfaction score, or ``None``."""
        found = self.satisfaction(limit=1)
        return float(found[0].data["score"]) if found else None

    def average_satisfaction(self) -> float | None:
        """Mean satisfaction across all records, or ``None`` when empty."""
        row = self._query_one("SELECT AVG(score) AS avg FROM satisfaction")
        if row is None or row["avg"] is None:
            return None
        return float(row["avg"])


_LABEL_SCORES = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}


def _resolve_tone(user_tone: Tone | str | None, user_text: str | None) -> Tone:
    """Coerce the various tone inputs into a :class:`Tone`."""
    if isinstance(user_tone, Tone):
        return user_tone
    if isinstance(user_tone, str):
        label = user_tone.strip().lower()
        if label in _LABEL_SCORES:
            return Tone(label, _LABEL_SCORES[label])
        return classify(user_tone)
    if user_text is not None:
        return classify(user_text)
    return Tone("neutral", 0.0)
