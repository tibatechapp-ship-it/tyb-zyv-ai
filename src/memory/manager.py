"""Memory manager — one object that owns the database and its four stores.

:class:`MemoryManager` is the front door to the memory system:

* opens (and migrates) the SQLite database;
* exposes the four stores as attributes — :attr:`episodic`, :attr:`semantic`,
  :attr:`procedural`, :attr:`emotional`;
* tracks **sessions** (start/end/current) so episodes can be grouped;
* assembles a compact **context bundle** an agent can be handed before it runs;
* exports/imports the whole thing as JSON.

Its API is deliberately small and synchronous; it holds no model or network
state, so it works the same on a phone as on a server.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.memory.base import Clock, connect, default_memory_path, utc_iso
from src.memory.embeddings import Embedder, make_embedder
from src.memory.emotional import EmotionalMemory
from src.memory.episodic import EpisodicMemory
from src.memory.procedural import ProceduralMemory
from src.memory.semantic import SemanticMemory

__all__ = ["MemoryManager"]

_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_ts  REAL NOT NULL,
    started_iso TEXT NOT NULL,
    ended_ts    REAL,
    ended_iso   TEXT,
    summary     TEXT NOT NULL DEFAULT ''
)
"""


class MemoryManager:
    """The single owner of a memory database and its four stores."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock: Clock = time.time,
        embedder: Embedder | str | None = None,
    ) -> None:
        self._clock = clock
        self._path = str(default_memory_path() if path is None else path)
        self._connection = connect(self._path)
        self._lock = threading.RLock()
        self._embedder: Embedder | None = make_embedder(embedder)
        self.episodic = EpisodicMemory(self._connection, clock=clock)
        self.semantic = SemanticMemory(self._connection, clock=clock, embedder=self._embedder)
        self.procedural = ProceduralMemory(
            self._connection, clock=clock, embedder=self._embedder
        )
        self.emotional = EmotionalMemory(self._connection, clock=clock)
        self._current_session: str | None = None
        with self._lock:
            self._connection.execute(_SESSIONS_SCHEMA)
            self._connection.commit()

    # -- lifetime ----------------------------------------------------------
    @property
    def path(self) -> str:
        """The database path this manager is bound to."""
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        """The shared SQLite connection."""
        return self._connection

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._connection.close()

    def __enter__(self) -> MemoryManager:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- sessions ----------------------------------------------------------
    def start_session(self, session_id: str | None = None) -> str:
        """Begin a session (auto-id if omitted) and make it current."""
        sid = session_id or uuid.uuid4().hex[:12]
        ts = self._clock()
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (id, started_ts, started_iso, ended_ts, ended_iso, summary)
                VALUES (?, ?, ?, NULL, NULL, '')
                """,
                (sid, ts, utc_iso(ts)),
            )
            self._connection.commit()
        self._current_session = sid
        return sid

    def end_session(self, *, summary: str = "") -> str | None:
        """End the current session; return its id (``None`` if none active)."""
        sid = self._current_session
        if sid is None:
            return None
        ts = self._clock()
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET ended_ts = ?, ended_iso = ?, summary = ? WHERE id = ?",
                (ts, utc_iso(ts), summary, sid),
            )
            self._connection.commit()
        self._current_session = None
        return sid

    def current_session(self) -> str | None:
        """The id of the active session, or ``None``."""
        return self._current_session

    def sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recent sessions, newest first."""
        with self._lock:
            rows = list(
                self._connection.execute(
                    "SELECT * FROM sessions ORDER BY started_ts DESC, id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            )
        return [dict(row) for row in rows]

    def session(self, session_id: str) -> dict[str, Any] | None:
        """One session by id, or ``None``."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    # -- recording ---------------------------------------------------------
    def remember_task(
        self,
        agent: str,
        task: str,
        result: str,
        *,
        ok: bool = True,
        duration_s: float = 0.0,
    ) -> int:
        """Log an agent episode under the current session; return its row id."""
        return self.episodic.record(
            agent,
            task,
            result,
            ok=ok,
            duration_s=duration_s,
            session_id=self._current_session,
        )

    # -- context bundle ----------------------------------------------------
    def context(
        self,
        query: str = "",
        *,
        episodes: int = 5,
        facts: int = 5,
        skills: int = 3,
    ) -> dict[str, Any]:
        """Assemble a compact bundle of memories to prime an agent.

        With a ``query``, facts and skills are ranked by relevance; otherwise the
        most recent / first few are returned. Always includes active preferences
        and the running average satisfaction.
        """
        recent = [record.to_dict() for record in self.episodic.recent(episodes)]
        if query:
            fact_records = self.semantic.search(query, limit=facts)
            skill_records = self.procedural.find(query, limit=skills)
        else:
            fact_records = self.semantic.all()[:facts]
            skill_records = self.procedural.all()[:skills]
        return {
            "session": self._current_session,
            "recent": recent,
            "facts": [record.to_dict() for record in fact_records],
            "skills": [record.to_dict() for record in skill_records],
            "preferences": [record.to_dict() for record in self.emotional.preferences()],
            "satisfaction": self.emotional.average_satisfaction(),
        }

    def context_text(self, query: str = "") -> str:
        """Render :meth:`context` as a short, prompt-friendly text block."""
        bundle = self.context(query)
        lines: list[str] = []
        if bundle["session"]:
            lines.append(f"session: {bundle['session']}")
        for item in bundle["recent"]:
            data = item["data"]
            mark = "ok" if data["ok"] else "fail"
            lines.append(f"- {data['agent']} [{mark}] {data['task']}")
        for item in bundle["facts"]:
            data = item["data"]
            lines.append(f"* {data['key']}: {data['value']}")
        for item in bundle["skills"]:
            lines.append(f"+ skill {item['data']['name']}")
        for item in bundle["preferences"]:
            data = item["data"]
            lines.append(f"~ {data['key']} = {data['value']}")
        if bundle["satisfaction"] is not None:
            lines.append(f"satisfaction: {bundle['satisfaction']:.2f}")
        return "\n".join(lines)

    # -- export / import ---------------------------------------------------
    def stats(self) -> dict[str, int]:
        """Row counts per store plus sessions, for dashboards/tests."""
        with self._lock:
            sessions = self._connection.execute(
                "SELECT COUNT(*) AS n FROM sessions"
            ).fetchone()["n"]
        return {
            "sessions": int(sessions),
            "episodes": self.episodic.count(),
            "facts": self.semantic.count(),
            "skills": self.procedural.count(),
            "preferences": self.emotional.count(),
        }

    def snapshot(self) -> dict[str, Any]:
        """A complete, JSON-serialisable dump of every table."""
        return {
            "version": 1,
            "sessions": self.sessions(limit=1_000_000),
            "episodes": [record.to_dict() for record in self.episodic.all()],
            "facts": [record.to_dict() for record in self.semantic.all()],
            "skills": [record.to_dict() for record in self.procedural.all()],
            "preferences": [record.to_dict() for record in self.emotional.preferences()],
            "signals": [record.to_dict() for record in self.emotional.all_signals()],
            "satisfaction": [record.to_dict() for record in self.emotional.all_satisfaction()],
        }

    def export_json(self, path: str | os.PathLike[str]) -> int:
        """Write :meth:`snapshot` to ``path`` as JSON; return bytes written."""
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return len(payload.encode("utf-8"))

    def import_json(self, path: str | os.PathLike[str]) -> dict[str, int]:
        """Load a snapshot produced by :meth:`export_json`; return rows per table."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        counts["sessions"] = self._import_sessions(data.get("sessions", []))
        counts["episodes"] = self._import_episodes(data.get("episodes", []))
        counts["facts"] = self._import_facts(data.get("facts", []))
        counts["skills"] = self._import_skills(data.get("skills", []))
        counts["preferences"] = self._import_preferences(data.get("preferences", []))
        counts["signals"] = self._import_signals(data.get("signals", []))
        counts["satisfaction"] = self._import_satisfaction(data.get("satisfaction", []))
        return counts

    # -- import internals (raw INSERT OR REPLACE, preserving ids) ----------
    def _executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        if not rows:
            return 0
        with self._lock:
            self._connection.executemany(sql, rows)
            self._connection.commit()
        return len(rows)

    def _import_sessions(self, items: list[dict[str, Any]]) -> int:
        return self._executemany(
            """
            INSERT OR REPLACE INTO sessions
                (id, started_ts, started_iso, ended_ts, ended_iso, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"],
                    float(item["started_ts"]),
                    str(item["started_iso"]),
                    item.get("ended_ts"),
                    item.get("ended_iso"),
                    item.get("summary", ""),
                )
                for item in items
            ],
        )

    def _import_episodes(self, items: list[dict[str, Any]]) -> int:
        return self._executemany(
            """
            INSERT OR REPLACE INTO episodes
                (id, ts, iso, session_id, agent, task, result, ok, duration_s, event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    item["data"].get("session_id"), item["data"]["agent"],
                    item["data"]["task"], item["data"]["result"],
                    int(bool(item["data"]["ok"])), float(item["data"]["duration_s"]),
                    str(item["data"].get("event") or ("success" if item["data"]["ok"] else "error")),
                )
                for item in items
            ],
        )

    def _import_facts(self, items: list[dict[str, Any]]) -> int:
        embedder = self._embedder
        rows: list[tuple[Any, ...]] = []
        for item in items:
            data = item["data"]
            embedding: str | None = None
            if embedder is not None:
                text = f"{data.get('subject', '')} {data['key']} {data['value']}"
                embedding = json.dumps(list(embedder(text)))
            rows.append(
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    data.get("subject", ""), data["key"], data["value"],
                    data.get("source", ""), embedding,
                )
            )
        return self._executemany(
            """
            INSERT OR REPLACE INTO facts
                (id, ts, iso, subject, key, value, source, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _import_skills(self, items: list[dict[str, Any]]) -> int:
        embedder = self._embedder
        rows: list[tuple[Any, ...]] = []
        for item in items:
            data = item["data"]
            embedding: str | None = None
            if embedder is not None:
                text = f"{data['name']} {data['description']} {' '.join(data['tags'])}"
                embedding = json.dumps(list(embedder(text)))
            rows.append(
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    data["name"], data["description"], json.dumps(data["steps"]),
                    json.dumps(data["examples"]), json.dumps(data["tags"]),
                    int(data.get("uses", 0)), int(data.get("successes", 0)), embedding,
                )
            )
        return self._executemany(
            """
            INSERT OR REPLACE INTO skills
                (id, ts, iso, name, description, steps, examples, tags,
                 uses, successes, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _import_preferences(self, items: list[dict[str, Any]]) -> int:
        return self._executemany(
            """
            INSERT OR REPLACE INTO preferences (id, ts, iso, key, value, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    item["data"]["key"], item["data"]["value"],
                    float(item["data"].get("confidence", 1.0)),
                )
                for item in items
            ],
        )

    def _import_signals(self, items: list[dict[str, Any]]) -> int:
        return self._executemany(
            """
            INSERT OR REPLACE INTO signals
                (id, ts, iso, session_id, text, sentiment, score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    item["data"].get("session_id"), item["data"]["text"],
                    item["data"]["sentiment"], float(item["data"]["score"]),
                )
                for item in items
            ],
        )

    def _import_satisfaction(self, items: list[dict[str, Any]]) -> int:
        return self._executemany(
            """
            INSERT OR REPLACE INTO satisfaction
                (id, ts, iso, session_id, score, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"], float(item["ts"]), str(item["iso"]),
                    item["data"].get("session_id"), float(item["data"]["score"]),
                    item["data"].get("note", ""),
                )
                for item in items
            ],
        )
