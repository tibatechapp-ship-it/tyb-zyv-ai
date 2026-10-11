"""Tests for src/core/context_manager.py (Phase 8) — 100% coverage target."""

from __future__ import annotations

import pytest

from src.core.context_manager import (
    CompactionResult,
    ContextEntry,
    ContextManager,
    estimate_tokens,
)
from src.core.types import Message

_LONG = "x" * 300  # 75 tokens under the default estimator
_TOKENS_OF_LONG = 75


def test_estimate_tokens_default() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("hello") == 2
    assert estimate_tokens("x" * 4) == 1
    assert estimate_tokens("x" * 5) == 2
    assert estimate_tokens(_LONG) == _TOKENS_OF_LONG


def test_constructor_validation() -> None:
    with pytest.raises(ValueError, match="budget"):
        ContextManager(budget=0)
    for bad in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="compaction_ratio"):
            ContextManager(compaction_ratio=bad)


def test_accounting_with_custom_estimator() -> None:
    manager = ContextManager(budget=100, estimator=lambda text: len(text))
    manager.add("user", "abcd")
    manager.add("user", "ef")
    assert manager.tokens_for("abc") == 3
    assert manager.used_tokens() == 6
    assert manager.remaining() == 94
    assert manager.utilization() == pytest.approx(0.06)
    assert manager.budget == 100
    assert abs(manager.compaction_ratio - 0.8) < 1e-9


def test_remaining_never_negative() -> None:
    manager = ContextManager(budget=10, auto_compact=False)
    manager.add("user", _LONG)  # 75 tokens > budget
    assert manager.used_tokens() == 75
    assert manager.remaining() == 0


def test_is_overloaded_boundary() -> None:
    manager = ContextManager(budget=100, auto_compact=False, estimator=lambda t: len(t))
    manager.add("user", "a" * 79)
    assert not manager.is_overloaded()
    manager.add("user", "a")
    assert manager.is_overloaded()
    assert manager.utilization() >= manager.compaction_ratio


def test_add_returns_entry_with_tokens() -> None:
    manager = ContextManager(budget=1000)
    entry = manager.add("user", "hello world")
    assert isinstance(entry, ContextEntry)
    assert entry.message.role == "user"
    assert entry.tokens == estimate_tokens("hello world")
    assert entry.to_dict() == {"role": "user", "content": "hello world", "tokens": entry.tokens}


def test_add_message_and_extend() -> None:
    manager = ContextManager(budget=1000)
    manager.add_message(Message("system", "be concise"))
    added = manager.extend([Message("user", "hi"), Message("assistant", "hello")])
    assert added == 2
    assert len(manager) == 3
    assert [m.role for m in manager.history()] == ["system", "user", "assistant"]
    named = manager.entries()
    assert isinstance(named[0], ContextEntry)
    assert named[1].tokens == estimate_tokens("hi")


def test_clear_keeps_offloaded() -> None:
    manager = ContextManager(budget=100, auto_compact=False)
    manager.add("user", _LONG)
    manager.compact_oldest(1)
    manager.add("user", "short")
    manager.clear()
    assert len(manager) == 0
    assert len(manager.offloaded()) == 1


def test_compact_evicts_until_under_threshold() -> None:
    manager = ContextManager(budget=300, auto_compact=False)
    canary: list[str] = []

    def summarizer(messages: list[Message]) -> str:
        canary.append("called")
        return "summary"

    manager._summarizer = summarizer  # type: ignore[attr-defined]  # test injection
    for _ in range(6):
        manager.add("user", _LONG)  # 6 x 75 = 450 tokens
    result = manager.compact()
    assert isinstance(result, CompactionResult)
    assert result.removed == 3
    assert result.offloaded == 3
    assert result.tokens_freed == 3 * _TOKENS_OF_LONG - estimate_tokens("summary")
    assert result.summary == "summary"
    assert canary == ["called"]
    assert not manager.is_overloaded()
    assert manager.history()[0].role == "system"
    assert manager.history()[0].content == "summary"
    assert len(manager.offloaded()) == 3


def test_compact_without_summarizer_drops_silently() -> None:
    manager = ContextManager(budget=200, auto_compact=False)
    for _ in range(4):  # 4 x 75 = 300 tokens
        manager.add("user", _LONG)
    result = manager.compact()
    assert result.removed == 2
    assert result.summary is None
    assert not manager.is_overloaded()
    assert manager.history()[0].content == _LONG


def test_compact_respects_keep_at_least() -> None:
    manager = ContextManager(budget=200, auto_compact=False)
    for _ in range(4):  # 300 tokens
        manager.add("user", _LONG)
    manager.compact(keep_at_least=1)  # evicts until 150 < 160 threshold: 2 left
    assert len(manager) == 2
    assert not manager.is_overloaded()
    manager.compact(keep_at_least=5)  # already below the floor: nothing to do
    assert len(manager) == 2


def test_compact_extra_eviction_when_marker_tips_over() -> None:
    # budget 500, threshold 400; 120-token turns, dense 75-token marker.
    manager = ContextManager(
        budget=500, auto_compact=False, summarizer=lambda msgs: "y" * 300
    )
    for _ in range(5):
        manager.add("user", "y" * 480)  # 120 tokens each -> 600 total
    result = manager.compact(keep_at_least=1)
    assert result.removed == 2  # the summarized block
    assert result.offloaded == 3  # block + one extra to fit the marker
    assert result.tokens_freed == 2 * 120 - 75 - 120
    assert result.summary == "y" * 300
    assert not manager.is_overloaded()
    assert manager.history()[0].role == "system"
    assert [m.content for m in manager.history()[1:]] == ["y" * 480, "y" * 480]


def test_compact_empty_history() -> None:
    manager = ContextManager(budget=100)
    called = False

    def summarizer(messages: list[Message]) -> str:
        nonlocal called
        called = True
        return "never"

    manager._summarizer = summarizer  # type: ignore[attr-defined]
    result = manager.compact()
    assert result.removed == 0
    assert result.tokens_freed == 0
    assert not called


def test_compact_oldest_always_evicts() -> None:
    manager = ContextManager(budget=100, auto_compact=False)
    for i in range(4):
        manager.add("user", f"m{i}")
    result = manager.compact_oldest(2)
    assert result.removed == 2
    assert [m.content for m in manager.history()] == ["m2", "m3"]
    assert [e.message.content for e in manager.offloaded()] == ["m0", "m1"]


def test_compact_oldest_with_summarizer() -> None:
    manager = ContextManager(budget=1000, auto_compact=False, summarizer=lambda msgs: "S")
    manager.add("user", _LONG)
    manager.add("user", _LONG)
    result = manager.compact_oldest(1)
    assert result.removed == 1
    assert result.summary == "S"
    assert result.tokens_freed == estimate_tokens(_LONG) - estimate_tokens("S")
    assert manager.history()[0].role == "system"
    assert manager.history()[0].content == "S"
    assert len(manager) == 2  # marker + remaining


def test_compact_oldest_validation_and_empty() -> None:
    manager = ContextManager(budget=100)
    with pytest.raises(ValueError, match="count"):
        manager.compact_oldest(0)
    result = manager.compact_oldest(3)
    assert result.removed == 0
    assert result.kept == 0
    assert result.tokens_freed == 0


def test_auto_compact_on_add() -> None:
    manager = ContextManager(budget=300, summarizer=lambda msgs: "s")
    for _ in range(5):
        manager.add("user", _LONG)  # 5 x 75 = 375 tokens
    assert len(manager.offloaded()) >= 1  # eviction happened automatically
    assert manager.history()[0].content == "s"  # summary marker in place
    assert manager.used_tokens() <= manager.budget
    assert not manager.is_overloaded()


def test_auto_compact_disabled() -> None:
    manager = ContextManager(budget=100, auto_compact=False)
    for i in range(4):
        manager.add("user", f"m{i}")
    # nothing was evicted
    assert len(manager) == 4
    assert len(manager.offloaded()) == 0


def test_offloaded_order_oldest_first() -> None:
    manager = ContextManager(budget=100, auto_compact=False)
    manager.add("user", "first")
    manager.add("user", _LONG)
    manager.compact_oldest(1)
    off = manager.offloaded()
    assert [e.message.content for e in off] == ["first"]


def test_restore_most_recent_first() -> None:
    manager = ContextManager(budget=140, auto_compact=False)
    manager.add("user", _LONG)
    manager.add("user", _LONG)
    assert manager.compact_oldest(2).removed == 2
    assert len(manager) == 0
    assert manager.restore() == 1  # only one 75-token turn fits in 140
    assert [e.message.content for e in manager.entries()] == [_LONG]
    assert len(manager.offloaded()) == 1


def test_restore_with_limit() -> None:
    manager = ContextManager(budget=1000, auto_compact=False)
    manager.add("user", _LONG)
    manager.add("user", _LONG)
    assert manager.compact_oldest(2).removed == 2
    assert manager.restore(limit=1) == 1
    assert len(manager.offloaded()) == 1
    assert manager.restore() == 1
    assert manager.restore() == 0
    assert len(manager) == 2


def test_restore_stops_when_tokens_do_not_fit() -> None:
    manager = ContextManager(budget=100, auto_compact=False)
    manager.add("user", _LONG)
    manager.compact_oldest(1)
    # active is empty (75 free), offloaded turn costs 75 -> fits exactly
    assert manager.restore() == 1
    manager.add("user", _LONG)  # now 150 used/remaining 0
    manager.compact_oldest(1)
    assert manager.restore() == 0  # nothing fits
    assert len(manager.offloaded()) == 1


def test_compact_oldest_folds_previous_digest() -> None:
    manager = ContextManager(budget=1000, auto_compact=False, summarizer=lambda msgs: "S")
    for _ in range(3):
        manager.add("user", _LONG)
    first = manager.compact_oldest(1)
    assert first.summary == "S"
    second = manager.compact_oldest(1)
    assert second.removed == 1
    assert second.summary == "S"  # summarizer saw the old digest + the new batch
    assert manager.history()[0].role == "system"
    assert len(manager) == 2  # digest + 1 surviving turn


def test_compact_oldest_extra_eviction_when_digest_tips_over() -> None:
    manager = ContextManager(
        budget=500, auto_compact=False, summarizer=lambda msgs: "y" * 300
    )
    for _ in range(5):
        manager.add("user", "y" * 480)  # 120 tokens each -> 600 total
    result = manager.compact_oldest(1)
    assert result.removed == 1
    assert result.offloaded == 3  # 1 explicit + 2 more to fit the 75-token digest
    assert result.tokens_freed == 0  # freed went negative -> clamped
    assert not manager.is_overloaded()
    assert manager.history()[0].content == "y" * 300


def test_snapshot_shape() -> None:
    manager = ContextManager(budget=1000)
    manager.add("user", "hi")
    snap = manager.snapshot()
    assert snap["budget"] == 1000
    assert snap["used_tokens"] == estimate_tokens("hi")
    assert snap["remaining"] == 1000 - estimate_tokens("hi")
    assert snap["utilization"] == pytest.approx(estimate_tokens("hi") / 1000)
    assert snap["active_entries"][0]["role"] == "user"
    assert snap["offloaded_count"] == 0
    manager._summarizer = lambda msgs: "s"  # type: ignore[attr-defined]
    result = manager.compact()
    assert result.to_dict()["removed"] == result.removed


def test_message_wrapping() -> None:
    manager = ContextManager(budget=1000)
    manager.add_message(Message("assistant", "answer"))
    assert manager.history() == [Message("assistant", "answer")]