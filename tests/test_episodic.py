"""Tests for Phase 3 episodic memory — event kinds, time windows, activity, timeline.

Timestamps are injected and anchored on a UTC midday so day boundaries
("today" / "yesterday") are unambiguous.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.memory.base import connect
from src.memory.episodic import DAY, EpisodicMemory, EventKind

# 2026-06-15 12:00:00 UTC — midday, so ±1 day crosses exactly one boundary.
T0 = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def episodic():
    conn = connect(":memory:")
    store = EpisodicMemory(conn)
    try:
        yield store
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# event classification
# --------------------------------------------------------------------------- #
def test_event_defaults_to_success_when_ok(episodic):
    episodic.record("A", "t", "r", ok=True, timestamp=T0)
    assert episodic.last().data["event"] == "success"


def test_event_defaults_to_error_when_not_ok(episodic):
    episodic.record("A", "t", "r", ok=False, timestamp=T0)
    assert episodic.last().data["event"] == "error"


def test_event_can_be_set_explicitly(episodic):
    episodic.record("A", "t", "r", event=EventKind.TASK, timestamp=T0)
    assert episodic.last().data["event"] == "task"


def test_event_accepts_plain_string(episodic):
    episodic.record("A", "t", "r", event="task", timestamp=T0)
    assert episodic.last().data["event"] == "task"


def test_event_rejects_unknown_value(episodic):
    with pytest.raises(ValueError):
        episodic.record("A", "t", "r", event="exploded", timestamp=T0)


def test_by_event_and_helpers(episodic):
    episodic.record("A", "ok", "r", ok=True, timestamp=T0)
    episodic.record("A", "bad", "r", ok=False, timestamp=T0 + 1)
    episodic.record("A", "plain", "r", event="task", timestamp=T0 + 2)
    assert [r.data["task"] for r in episodic.successes()] == ["ok"]
    assert [r.data["task"] for r in episodic.errors()] == ["bad"]
    assert episodic.event_counts() == {"error": 1, "success": 1, "task": 1}


# --------------------------------------------------------------------------- #
# time windows
# --------------------------------------------------------------------------- #
def _seed(episodic):
    episodic.record("A", "today", "r", timestamp=T0)
    episodic.record("A", "yesterday", "r", timestamp=T0 - DAY)
    episodic.record("A", "two days", "r", timestamp=T0 - 2 * DAY)
    episodic.record("A", "three days", "r", timestamp=T0 - 3 * DAY)
    episodic.record("A", "twenty days", "r", timestamp=T0 - 20 * DAY)
    episodic.record("A", "forty days", "r", timestamp=T0 - 40 * DAY)


def test_today_only_current_day(episodic):
    _seed(episodic)
    assert [r.data["task"] for r in episodic.today(now=T0)] == ["today"]


def test_yesterday_only_previous_day(episodic):
    _seed(episodic)
    assert [r.data["task"] for r in episodic.yesterday(now=T0)] == ["yesterday"]


def test_last_week_covers_seven_days(episodic):
    _seed(episodic)
    tasks = {r.data["task"] for r in episodic.last_week(now=T0)}
    assert {"today", "yesterday", "two days", "three days"} <= tasks
    assert "twenty days" not in tasks


def test_last_month_covers_thirty_days(episodic):
    _seed(episodic)
    tasks = {r.data["task"] for r in episodic.last_month(now=T0)}
    assert "twenty days" in tasks
    assert "forty days" not in tasks


def test_since_window(episodic):
    _seed(episodic)
    assert {r.data["task"] for r in episodic.since(2.5 * DAY, now=T0)} == {
        "today", "yesterday", "two days"
    }


def test_windows_use_clock_when_now_omitted(episodic):
    clock_store = EpisodicMemory(episodic.connection, clock=lambda: T0)
    clock_store.record("A", "now", "r")
    assert [r.data["task"] for r in clock_store.today()] == ["now"]


# --------------------------------------------------------------------------- #
# activity graph
# --------------------------------------------------------------------------- #
def test_activity_graph_is_zero_filled_and_ordered(episodic):
    episodic.record("A", "a", "r", timestamp=T0)
    episodic.record("A", "b", "r", timestamp=T0)
    episodic.record("A", "c", "r", timestamp=T0 - 2 * DAY)
    series = episodic.activity_graph(3, now=T0)
    assert [point["day"] for point in series] == ["2026-06-13", "2026-06-14", "2026-06-15"]
    assert [point["count"] for point in series] == [1, 0, 2]


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #
def test_timeline_is_chronological(episodic):
    _seed(episodic)
    tasks = [r.data["task"] for r in episodic.timeline()]
    assert tasks[0] == "forty days"
    assert tasks[-1] == "today"


def test_timeline_limit_keeps_latest_in_order(episodic):
    _seed(episodic)
    assert [r.data["task"] for r in episodic.timeline(limit=2)] == ["yesterday", "today"]


def test_render_timeline_contains_lines(episodic):
    episodic.record("Planner", "plan", "r", timestamp=T0)
    text = episodic.render_timeline()
    assert "Planner" in text and "[success] plan" in text


# --------------------------------------------------------------------------- #
# ask()
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("question", "window"),
    [
        ("what did we do yesterday?", "yesterday"),
        ("show me today", "today"),
        ("what happened last week", "last_week"),
        ("summary of the last month", "last_month"),
        ("anything at all", "recent"),
        ("matha fa3alna ams", "yesterday"),
    ],
)
def test_ask_classifies_window(episodic, question, window):
    assert episodic.ask(question, now=T0)["window"] == window


def test_ask_counts_events_and_agents(episodic):
    episodic.record("Planner", "plan", "r", ok=True, timestamp=T0 - DAY)
    episodic.record("Coder", "build", "r", ok=False, timestamp=T0 - DAY + 60)
    result = episodic.ask("what did we do yesterday", now=T0)
    assert result["count"] == 2
    assert result["by_event"] == {"error": 1, "success": 1}
    assert result["by_agent"] == {"Coder": 1, "Planner": 1}
    assert "2 episodes" in result["answer"]


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #
def test_migration_adds_event_column_to_old_database():
    conn = connect(":memory:")
    conn.execute(
        """
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, iso TEXT NOT NULL,
            session_id TEXT, agent TEXT NOT NULL, task TEXT NOT NULL, result TEXT NOT NULL,
            ok INTEGER NOT NULL, duration_s REAL NOT NULL
        )
        """
    )
    conn.commit()
    store = EpisodicMemory(conn)  # _init_schema should ALTER-add the column
    columns = {str(row["name"]) for row in store._query("PRAGMA table_info(episodes)")}
    assert "event" in columns
    store.record("A", "t", "r", timestamp=T0)
    assert store.last().data["event"] == "success"
    conn.close()
