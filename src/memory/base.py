"""Shared foundation for the memory system.

Every memory store (:mod:`~src.memory.episodic`, ``semantic``, ``procedural``,
``emotional``) is a thin, typed wrapper over its own SQLite table(s). The
machinery they share lives here:

* :func:`connect` — one configured :class:`sqlite3.Connection` (WAL mode, row
  factory, foreign keys on).
* :class:`MemoryRecord` — the immutable, JSON-friendly view of a stored row.
* :class:`MemoryStore` — the base class: a connection, an injectable clock, a
  re-entrant lock (mirroring the thread-safety work in :mod:`src.agents`), and
  helpers for executing/querying.
* :data:`Clock` — a ``() -> float`` epoch-seconds source, injectable so tests
  can control time deterministically.

Nothing here talks to a model or the network; persistence is plain SQLite so the
whole subsystem works on a phone with no extra dependencies.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "Clock",
    "MemoryKind",
    "MemoryRecord",
    "MemoryStore",
    "connect",
    "default_memory_path",
    "utc_iso",
]

#: A monotonic-ish epoch-seconds source (``time.time`` by default, injectable).
Clock = Callable[[], float]


def utc_iso(timestamp: float) -> str:
    """Format an epoch-seconds value as a second-precision UTC ISO-8601 string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def default_memory_path() -> Path:
    """Where the database lives when the caller gives no path.

    ``$ZYVO_MEMORY_DB`` wins (handy in tests/containers); otherwise
    ``~/.local/share/tyb-zyv-ai/memory.db``.
    """
    override = os.environ.get("ZYVO_MEMORY_DB")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "tyb-zyv-ai" / "memory.db"


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite connection tuned for a memory store."""
    target = str(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


class MemoryKind(StrEnum):
    """The four memory systems the agents draw on."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    EMOTIONAL = "emotional"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """An immutable, JSON-friendly snapshot of one stored row."""

    id: int
    kind: MemoryKind
    ts: float
    iso: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view (kind flattened to its string value)."""
        return {
            "id": self.id,
            "kind": str(self.kind),
            "ts": self.ts,
            "iso": self.iso,
            "data": dict(self.data),
        }


class MemoryStore(ABC):
    """Base class for one SQLite-backed memory store.

    Subclasses set :attr:`kind` and :attr:`_table`, implement
    :meth:`_init_schema` and their own typed methods, and inherit the locking,
    counting and clearing behaviour here.
    """

    #: Which memory system this store implements.
    kind: MemoryKind = MemoryKind.EPISODIC
    #: Primary table name used by :meth:`count` / :meth:`clear`.
    _table: str = "memory"

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = time.time) -> None:
        self._connection = connection
        self._clock = clock
        # RLock, to stay consistent with AgentMetrics: helpers may nest.
        self._lock = threading.RLock()
        self._init_schema()

    @abstractmethod
    def _init_schema(self) -> None:
        """Create this store's tables and indexes if they do not exist."""

    @property
    def connection(self) -> sqlite3.Connection:
        """The shared SQLite connection backing this store."""
        return self._connection

    def now(self) -> float:
        """Current time (epoch seconds) from the injected clock."""
        return self._clock()

    # -- low-level helpers (all serialised by the lock) --------------------
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, params)
            self._connection.commit()
            return cursor

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, params).fetchall())

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -- shared surface ----------------------------------------------------
    def count(self) -> int:
        """How many rows the primary table holds."""
        row = self._query_one(f"SELECT COUNT(*) AS n FROM {self._table}")
        return int(row["n"]) if row is not None else 0

    def clear(self) -> None:
        """Remove every row from the primary table."""
        self._execute(f"DELETE FROM {self._table}")

    def __len__(self) -> int:
        return self.count()
