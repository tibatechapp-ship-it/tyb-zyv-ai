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
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.memory.base import Clock, MemoryRecord, connect, default_memory_path, utc_iso
from src.memory.embeddings import Embedder, make_embedder
from src.memory.emotional import EmotionalMemory
from src.memory.episodic import DAY, EpisodicMemory
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

_SUMMARIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_summaries (
    day            TEXT NOT NULL,
    agent          TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    duration_total REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, agent)
)
"""

_SLUG = re.compile(r"[^a-z0-9]+")

#: Query cues that suggest which store a smart :meth:`MemoryManager.recall` uses.
_SKILL_HINTS = (
    "how to", "how do", "how do i", "steps", "procedure", "tutorial", "kayfa", "kaif",
)
_EPISODE_HINTS = (
    "what did we", "what have we", "history", "yesterday", "today", "last week",
    "last month", "timeline", "ams",
)
_EMOTION_HINTS = ("prefer", "preference", "feel", "tone", "like my", "style")

_WRITE_SYNONYMS = {
    "fact": "semantic",
    "semantic": "semantic",
    "episode": "episodic",
    "episodic": "episodic",
    "skill": "procedural",
    "procedural": "procedural",
    "preference": "emotional",
    "emotional": "emotional",
}


def _slugify(text: str, words: int = 4) -> str:
    """A short stable key/skill name derived from text (first few words)."""
    cleaned = _SLUG.sub("_", text.lower()).strip("_")
    parts = [part for part in cleaned.split("_") if part][:words]
    return "_".join(parts) or "memory"


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
            self._connection.execute(_SUMMARIES_SCHEMA)
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

    # -- unified remember / recall / forget --------------------------------
    def remember(
        self,
        content: str,
        *,
        kind: str = "auto",
        key: str | None = None,
        subject: str = "",
        source: str = "",
        agent: str = "assistant",
        task: str | None = None,
        result: str = "",
        ok: bool = True,
        duration_s: float = 0.0,
        steps: Sequence[str] = (),
        tags: Sequence[str] = (),
        confidence: float = 1.0,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Store one memory of any kind, dispatching to the right store.

        ``kind`` is ``"semantic"``, ``"episodic"``, ``"procedural"``,
        ``"emotional"`` or ``"auto"`` (synonyms like ``"fact"``/``"skill"`` are
        accepted). With ``"auto"`` the kind is inferred: ``steps`` -> skill,
        ``task`` -> episode, otherwise a fact. ``content`` is the value (fact),
        the task text (episode), the description (skill) or the value
        (preference). Returns ``{"kind", "id"}``.
        """
        resolved = self._resolve_write_kind(kind, task=task, steps=steps)
        if resolved == "episodic":
            row_id = self.remember_task(
                agent, task or content, result, ok=ok, duration_s=duration_s
            )
        elif resolved == "procedural":
            row_id = self.procedural.learn(
                key or _slugify(content), content, steps=steps, tags=tags
            )
        elif resolved == "emotional":
            row_id = self.emotional.set_preference(
                key or _slugify(content), content, confidence=confidence
            )
        else:
            row_id = self.semantic.remember(
                key or _slugify(content), content, subject=subject, source=source
            )
        return {"kind": resolved, "id": row_id}

    def _resolve_write_kind(
        self, kind: str, *, task: str | None, steps: Sequence[str]
    ) -> str:
        if kind != "auto":
            return _WRITE_SYNONYMS.get(kind, kind)
        if steps:
            return "procedural"
        if task is not None:
            return "episodic"
        return "semantic"

    @staticmethod
    def _infer_recall_kind(query: str) -> str:
        lowered = query.lower()
        if any(cue in lowered for cue in _SKILL_HINTS):
            return "procedural"
        if any(cue in lowered for cue in _EPISODE_HINTS):
            return "episodic"
        if any(cue in lowered for cue in _EMOTION_HINTS):
            return "emotional"
        return "semantic"

    def recall(
        self, query: str, *, kind: str = "auto", limit: int = 5
    ) -> dict[str, Any]:
        """Smart retrieval: pick the right store for ``query`` and search it.

        With ``kind="auto"`` the store is inferred from the question (a *how-to*
        asks procedural memory, a *what did we do* asks episodic, a *preference*
        asks emotional, else semantic). Returns ``{"kind", "query", "results"}``.
        """
        resolved = self._infer_recall_kind(query) if kind == "auto" else kind
        resolved = _WRITE_SYNONYMS.get(resolved, resolved)
        if resolved == "episodic":
            records = self.episodic.search(query, limit=limit)
        elif resolved == "procedural":
            records = self.procedural.find(query, limit=limit)
        elif resolved == "emotional":
            records = self._match_preferences(query, limit)
        else:
            records = self.semantic.search(query, limit=limit)
        return {
            "kind": resolved,
            "query": query,
            "results": [record.to_dict() for record in records],
        }

    def _match_preferences(self, query: str, limit: int) -> list[MemoryRecord]:
        """Preferences whose key/value shares a word with ``query`` (else all)."""
        tokens = [token for token in re.split(r"\W+", query.lower()) if len(token) > 2]
        prefs = self.emotional.preferences()
        matched = [
            record
            for record in prefs
            if any(
                token in str(record.data["key"]).lower()
                or token in str(record.data["value"]).lower()
                for token in tokens
            )
        ]
        return (matched or prefs)[:limit]

    def forget(self, target: str, *, kind: str = "auto", subject: str = "") -> int:
        """Remove a memory by key/name; return how many rows were removed."""
        if kind in {"semantic", "fact"}:
            return 1 if self.semantic.forget(target, subject=subject) else 0
        if kind in {"procedural", "skill"}:
            return 1 if self.procedural.forget(target) else 0
        removed = 0
        if self.semantic.forget(target, subject=None):
            removed += 1
        if self.procedural.forget(target):
            removed += 1
        return removed

    # -- retention & compression ------------------------------------------
    def retention(
        self, *, max_age_days: float | None = None, max_episodes: int | None = None
    ) -> dict[str, int]:
        """Apply a retention policy to the episodic log; return rows removed.

        * ``max_age_days`` — drop episodes older than this many days;
        * ``max_episodes`` — keep only the most recent N episodes.
        """
        removed = 0
        if max_age_days is not None:
            cutoff = self._clock() - float(max_age_days) * DAY
            cursor = self._execute("DELETE FROM episodes WHERE ts < ?", (cutoff,))
            removed += cursor.rowcount
        if max_episodes is not None:
            cursor = self._execute(
                """
                DELETE FROM episodes WHERE id NOT IN (
                    SELECT id FROM episodes ORDER BY ts DESC, id DESC LIMIT ?
                )
                """,
                (int(max_episodes),),
            )
            removed += cursor.rowcount
        return {"episodes_removed": int(removed)}

    def compress(self, *, older_than_days: float = 30) -> dict[str, int]:
        """Roll episodes older than the cutoff into per-day summaries.

        Raw episodes are replaced by ``(day, agent)`` summary rows and the
        database is vacuumed. Returns rows removed / summaries written.
        """
        cutoff = self._clock() - float(older_than_days) * DAY
        with self._lock:
            groups = list(
                self._connection.execute(
                    """
                    SELECT substr(iso, 1, 10) AS day, agent,
                           COUNT(*) AS n,
                           SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors,
                           COALESCE(SUM(duration_s), 0) AS total
                    FROM episodes WHERE ts < ?
                    GROUP BY day, agent
                    """,
                    (cutoff,),
                ).fetchall()
            )
            for row in groups:
                self._connection.execute(
                    """
                    INSERT INTO episode_summaries (day, agent, count, errors, duration_total)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(day, agent) DO UPDATE SET
                        count = count + excluded.count,
                        errors = errors + excluded.errors,
                        duration_total = duration_total + excluded.duration_total
                    """,
                    (row["day"], row["agent"], row["n"], row["errors"], row["total"]),
                )
            cursor = self._connection.execute("DELETE FROM episodes WHERE ts < ?", (cutoff,))
            removed = cursor.rowcount
            self._connection.commit()
            self._connection.execute("VACUUM")
        return {"episodes_removed": int(removed), "summaries": len(groups), "vacuumed": 1}

    def summaries(self) -> list[dict[str, Any]]:
        """Per-day per-agent compressed episode summaries, oldest day first."""
        with self._lock:
            rows = list(
                self._connection.execute(
                    "SELECT * FROM episode_summaries ORDER BY day ASC, agent ASC"
                ).fetchall()
            )
        return [dict(row) for row in rows]

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

    # -- short aliases (the tiny, friendly front door) ---------------------
    def export(self, path: str | os.PathLike[str]) -> int:
        """Alias of :meth:`export_json`."""
        return self.export_json(path)

    def load(self, path: str | os.PathLike[str]) -> dict[str, int]:
        """Alias of :meth:`import_json`."""
        return self.import_json(path)

    # -- import internals (raw INSERT OR REPLACE, preserving ids) ----------
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, params)
            self._connection.commit()
            return cursor

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
