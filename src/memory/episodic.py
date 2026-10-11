"""Episodic memory — the log of *what happened*.

Every task any agent runs can be recorded as one timestamped episode (agent,
task, result, ok flag, duration). This is the store behind questions like
*"what did we do yesterday?"* and the activity graph.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from src.memory.base import MemoryKind, MemoryRecord, MemoryStore, utc_iso

__all__ = ["EpisodicMemory"]


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
                duration_s REAL    NOT NULL
            )
            """
        )
        self._execute("CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent)")

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
    ) -> int:
        """Append one episode and return its row id."""
        ts = self.now() if timestamp is None else timestamp
        cursor = self._execute(
            """
            INSERT INTO episodes (ts, iso, session_id, agent, task, result, ok, duration_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, utc_iso(ts), session_id, agent, task, result, int(ok), float(duration_s)),
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

    def activity(self) -> dict[str, int]:
        """Episode counts bucketed by calendar day (UTC), oldest day first."""
        rows = self._query(
            """
            SELECT substr(iso, 1, 10) AS day, COUNT(*) AS n
            FROM episodes GROUP BY day ORDER BY day ASC
            """
        )
        return {str(row["day"]): int(row["n"]) for row in rows}
