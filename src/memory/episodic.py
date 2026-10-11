"""Episodic memory — the log of *what happened*.

Every task any agent runs can be recorded as one timestamped episode (agent,
task, result, outcome, duration). On top of the raw log this store answers the
questions a person actually asks:

* *"what did we do yesterday / last week / last month?"* — :meth:`today`,
  :meth:`yesterday`, :meth:`last_week`, :meth:`last_month`, :meth:`ask`;
* *"how busy were we?"* — :meth:`activity` and :meth:`activity_graph`;
* *"show me the story"* — :meth:`timeline` / :meth:`render_timeline`.

Each episode carries an :class:`EventKind` (``task`` / ``success`` / ``error``),
derived from the ``ok`` flag unless given explicitly.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from src.memory.base import MemoryKind, MemoryRecord, MemoryStore, utc_iso

__all__ = ["EpisodicMemory", "EventKind"]

#: Seconds in a day.
DAY = 86_400.0


class EventKind(StrEnum):
    """How an episode ended."""

    TASK = "task"
    SUCCESS = "success"
    ERROR = "error"


def _day_start(timestamp: float) -> float:
    """Epoch seconds of the UTC midnight at or before ``timestamp``."""
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


class EpisodicMemory(MemoryStore):
    """Append-only log of agent episodes, keyed by time."""

    kind = MemoryKind.EPISODIC
    _table = "episodes"

    def _init_schema(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL    NOT NULL,
                iso        TEXT    NOT NULL,
                session_id TEXT,
                agent      TEXT    NOT NULL,
                task       TEXT    NOT NULL,
                result     TEXT    NOT NULL,
                ok         INTEGER NOT NULL,
                duration_s REAL    NOT NULL,
                event      TEXT    NOT NULL DEFAULT 'task'
            )
            """
        )
        self._ensure_column("episodes", "event", "TEXT NOT NULL DEFAULT 'task'")
        self._execute("CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_episodes_event ON episodes(event)")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Add ``column`` to ``table`` if a pre-existing database lacks it."""
        columns = {str(row["name"]) for row in self._query(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # -- write -------------------------------------------------------------
    def record(
        self,
        agent: str,
        task: str,
        result: str,
        *,
        ok: bool = True,
        duration_s: float = 0.0,
        session_id: str | None = None,
        timestamp: float | None = None,
        event: EventKind | str | None = None,
    ) -> int:
        """Append one episode and return its row id.

        ``event`` defaults to :attr:`EventKind.SUCCESS` when ``ok`` is true and
        :attr:`EventKind.ERROR` otherwise; pass it explicitly for a plain
        :attr:`EventKind.TASK`.
        """
        if event is None:
            kind = EventKind.SUCCESS if ok else EventKind.ERROR
        else:
            kind = EventKind(str(event))
        ts = self.now() if timestamp is None else timestamp
        cursor = self._execute(
            """
            INSERT INTO episodes
                (ts, iso, session_id, agent, task, result, ok, duration_s, event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, utc_iso(ts), session_id, agent, task, result,
                int(ok), float(duration_s), str(kind),
            ),
        )
        return int(cursor.lastrowid or 0)

    # -- read --------------------------------------------------------------
    def _rows_to_records(self, rows: Sequence[sqlite3.Row]) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id=int(row["id"]),
                kind=self.kind,
                ts=float(row["ts"]),
                iso=str(row["iso"]),
                data={
                    "session_id": row["session_id"],
                    "agent": row["agent"],
                    "task": row["task"],
                    "result": row["result"],
                    "ok": bool(row["ok"]),
                    "duration_s": float(row["duration_s"]),
                    "event": str(row["event"]),
                },
            )
            for row in rows
        ]

    def recent(self, limit: int = 10) -> list[MemoryRecord]:
        """The most recent episodes, newest first."""
        rows = self._query(
            "SELECT * FROM episodes ORDER BY ts DESC, id DESC LIMIT ?", (int(limit),)
        )
        return self._rows_to_records(rows)

    def all(self) -> list[MemoryRecord]:
        """Every episode, oldest first (used for export)."""
        return self._rows_to_records(
            self._query("SELECT * FROM episodes ORDER BY ts ASC, id ASC")
        )

    def last(self) -> MemoryRecord | None:
        """The single most recent episode, or ``None``."""
        found = self.recent(1)
        return found[0] if found else None

    def between(
        self, start: float, end: float, *, limit: int | None = None
    ) -> list[MemoryRecord]:
        """Episodes with ``start <= ts <= end``, oldest first."""
        sql = "SELECT * FROM episodes WHERE ts >= ? AND ts <= ? ORDER BY ts ASC, id ASC"
        params: list[Any] = [float(start), float(end)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return self._rows_to_records(self._query(sql, params))

    def for_agent(self, agent: str, *, limit: int = 20) -> list[MemoryRecord]:
        """Episodes produced by one agent, newest first."""
        rows = self._query(
            "SELECT * FROM episodes WHERE agent = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (agent, int(limit)),
        )
        return self._rows_to_records(rows)

    def for_session(self, session_id: str, *, limit: int | None = None) -> list[MemoryRecord]:
        """Episodes belonging to one session, oldest first."""
        sql = "SELECT * FROM episodes WHERE session_id = ? ORDER BY ts ASC, id ASC"
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return self._rows_to_records(self._query(sql, params))

    def search(self, needle: str, *, limit: int = 20) -> list[MemoryRecord]:
        """Substring match over task and result text, newest first."""
        pattern = f"%{needle}%"
        rows = self._query(
            """
            SELECT * FROM episodes
            WHERE task LIKE ? OR result LIKE ?
            ORDER BY ts DESC, id DESC LIMIT ?
            """,
            (pattern, pattern, int(limit)),
        )
        return self._rows_to_records(rows)

    def by_event(self, event: EventKind | str, *, limit: int = 20) -> list[MemoryRecord]:
        """Episodes with a given outcome, newest first."""
        rows = self._query(
            "SELECT * FROM episodes WHERE event = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (str(EventKind(str(event))), int(limit)),
        )
        return self._rows_to_records(rows)

    def errors(self, *, limit: int = 20) -> list[MemoryRecord]:
        """Episodes that ended in error, newest first."""
        return self.by_event(EventKind.ERROR, limit=limit)

    def successes(self, *, limit: int = 20) -> list[MemoryRecord]:
        """Episodes that succeeded, newest first."""
        return self.by_event(EventKind.SUCCESS, limit=limit)

    def event_counts(self) -> dict[str, int]:
        """Episode totals per outcome."""
        rows = self._query("SELECT event, COUNT(*) AS n FROM episodes GROUP BY event")
        return {str(row["event"]): int(row["n"]) for row in rows}

    # -- time-window queries ----------------------------------------------
    def since(self, seconds: float, *, now: float | None = None) -> list[MemoryRecord]:
        """Episodes in the last ``seconds`` up to ``now`` (default: the clock)."""
        reference = self.now() if now is None else now
        return self.between(reference - float(seconds), reference)

    def today(self, *, now: float | None = None) -> list[MemoryRecord]:
        """Episodes recorded during the current UTC day."""
        reference = self.now() if now is None else now
        return self.between(_day_start(reference), reference)

    def yesterday(self, *, now: float | None = None) -> list[MemoryRecord]:
        """Episodes recorded during the previous UTC day."""
        reference = self.now() if now is None else now
        start = _day_start(reference) - DAY
        return self.between(start, start + DAY - 0.001)

    def last_week(self, *, now: float | None = None) -> list[MemoryRecord]:
        """Episodes from the last 7 days."""
        reference = self.now() if now is None else now
        return self.between(reference - 7 * DAY, reference)

    def last_month(self, *, now: float | None = None) -> list[MemoryRecord]:
        """Episodes from the last 30 days."""
        reference = self.now() if now is None else now
        return self.between(reference - 30 * DAY, reference)

    # -- activity graph ----------------------------------------------------
    def activity(self) -> dict[str, int]:
        """Episode counts bucketed by calendar day (UTC), oldest day first."""
        rows = self._query(
            """
            SELECT substr(iso, 1, 10) AS day, COUNT(*) AS n
            FROM episodes GROUP BY day ORDER BY day ASC
            """
        )
        return {str(row["day"]): int(row["n"]) for row in rows}

    def activity_graph(self, days: int = 14, *, now: float | None = None) -> list[dict[str, Any]]:
        """A zero-filled daily series for the last ``days`` days.

        Returns one ``{"day", "count"}`` dict per day, oldest first, so a chart
        can plot it directly without filling gaps itself.
        """
        reference = self.now() if now is None else now
        counts = self.activity()
        start = _day_start(reference) - (int(days) - 1) * DAY
        series: list[dict[str, Any]] = []
        for offset in range(int(days)):
            day_ts = start + offset * DAY
            label = utc_iso(day_ts)[:10]
            series.append({"day": label, "count": counts.get(label, 0)})
        return series

    # -- timeline ----------------------------------------------------------
    def timeline(self, *, limit: int | None = None) -> list[MemoryRecord]:
        """The most recent ``limit`` episodes in chronological order."""
        if limit is None:
            return self.all()
        return list(reversed(self.recent(limit)))

    def render_timeline(self, *, limit: int | None = None) -> str:
        """A one-line-per-episode, chronological text view of the log."""
        lines: list[str] = []
        for episode in self.timeline(limit=limit):
            data = episode.data
            lines.append(
                f"- {episode.iso} | {data['agent']} | [{data['event']}] {data['task']}"
            )
        return "\n".join(lines)

    # -- natural-ish questions --------------------------------------------
    def ask(self, query: str, *, now: float | None = None) -> dict[str, Any]:
        """Answer *"what did we do <when>?"* with a small structured summary."""
        reference = self.now() if now is None else now
        window = _classify_window(query)
        if window == "today":
            episodes = self.today(now=reference)
        elif window == "yesterday":
            episodes = self.yesterday(now=reference)
        elif window == "last_week":
            episodes = self.last_week(now=reference)
        elif window == "last_month":
            episodes = self.last_month(now=reference)
        else:
            episodes = self.recent(5)
        by_event = _tally(episode.data["event"] for episode in episodes)
        by_agent = _tally(episode.data["agent"] for episode in episodes)
        parts = ", ".join(f"{count} {name}" for name, count in sorted(by_event.items()))
        return {
            "window": window,
            "count": len(episodes),
            "by_event": by_event,
            "by_agent": by_agent,
            "episodes": [episode.to_dict() for episode in episodes],
            "answer": f"{window}: {len(episodes)} episodes" + (f" ({parts})" if parts else ""),
        }


def _tally(values: Any) -> dict[str, int]:
    """Count occurrences of each value (keys sorted by name)."""
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _classify_window(text: str) -> str:
    """Map a free-text question to a window name (english + arabic cues)."""
    lowered = text.lower()
    if any(cue in lowered for cue in ("yesterday", "ams", "albarha", "امس")):
        return "yesterday"
    if any(cue in lowered for cue in ("last week", "this week", "usbu", "اسبوع")):
        return "last_week"
    if any(cue in lowered for cue in ("last month", "shahr", "شهر")):
        return "last_month"
    if any(cue in lowered for cue in ("today", "alyawm", "al yawm", "اليوم")):
        return "today"
    return "recent"
