"""Context manager — track, compact and offload LLM conversation context
(Phase 8).

Long-running agents accumulate conversation. Two problems follow: the prompt
grows past the model's context window, and stale turns waste budget. The
:class:`ContextManager` owns one conversation transcript and gives it three
behaviours:

* **token accounting** — every message is priced with a deterministic offline
  estimator (~4 chars/token) and the manager tracks used / remaining / budget;
* **compaction at 80%** — when utilisation crosses ``compaction_ratio`` the
  oldest turns are moved out and (if a summarizer is provided) replaced by a
  single running digest, so the active history stays under budget;
* **offload** — evicted turns are kept in an offload store (in memory here) and
  can be restored later if the budget allows.

The digest lives in its own slot (one per manager) so it is never evicted and
never confused with a real turn; each compaction folds the previous digest into
the next, so the summary grows monotonically rather than stacking markers.

All pure and offline; the summarizer is an injected callable so production can
use the model while tests stay deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any

from src.core.types import Message

__all__ = [
    "CompactionResult",
    "ContextEntry",
    "ContextManager",
    "estimate_tokens",
]


def _default_estimator(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def estimate_tokens(text: str) -> int:
    """A deterministic offline token count (~4 chars/token, never below 1)."""
    return _default_estimator(text)


#: A callable that turns a run of messages into one short summary string.
Summarizer = Callable[[Sequence[Message]], str]


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One message and its estimated token cost."""

    message: Message
    tokens: int

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this entry."""
        return {"role": self.message.role, "content": self.message.content, "tokens": self.tokens}


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """What one compaction pass did."""

    removed: int
    kept: int
    tokens_freed: int
    summary: str | None = None
    offloaded: int = 0

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this result."""
        return {
            "removed": self.removed,
            "kept": self.kept,
            "tokens_freed": self.tokens_freed,
            "summary": self.summary,
            "offloaded": self.offloaded,
        }


class ContextManager:
    """Tracks one conversation's tokens and keeps it under budget.

    Guarantees:

    * ``add`` never fails and returns a priced entry;
    * with ``auto_compact`` on, the history is at or below the compaction
      ratio when every ``add`` returns (unless a single oversized turn makes
      that impossible — the report then just ends above the line);
    * evicted turns always land in the offload store — nothing is dropped
      silently, and ``restore`` brings them back while the budget allows.
    """

    def __init__(
        self,
        *,
        budget: int = 8192,
        compaction_ratio: float = 0.8,
        estimator: Callable[[str], int] | None = None,
        summarizer: Summarizer | None = None,
        summary_role: str = "system",
        auto_compact: bool = True,
    ) -> None:
        if budget < 1:
            raise ValueError("budget must be >= 1")
        if not 0.0 < compaction_ratio < 1.0:
            raise ValueError("compaction_ratio must be strictly between 0 and 1")
        self._budget = budget
        self._compaction_ratio = compaction_ratio
        self._estimator = estimator or estimate_tokens
        self._summarizer = summarizer
        self._summary_role = summary_role
        self._auto_compact = auto_compact
        self._summary: ContextEntry | None = None
        self._entries: list[ContextEntry] = []
        self._offloaded: list[ContextEntry] = []
        self._lock = RLock()

    # -- accounting ---------------------------------------------------------
    @property
    def budget(self) -> int:
        """The token budget this manager enforces."""
        return self._budget

    @property
    def compaction_ratio(self) -> float:
        """The utilisation at which auto-compaction kicks in (0..1)."""
        return self._compaction_ratio

    def tokens_for(self, content: str) -> int:
        """Price ``content`` with the configured estimator."""
        return self._estimator(content)

    def used_tokens(self) -> int:
        """Tokens consumed by the active history (digest included)."""
        with self._lock:
            return (self._summary.tokens if self._summary else 0) + sum(
                entry.tokens for entry in self._entries
            )

    def remaining(self) -> int:
        """Free budget left in the active history (never negative)."""
        return max(0, self._budget - self.used_tokens())

    def utilization(self) -> float:
        """Used tokens as a fraction of the budget."""
        return self.used_tokens() / self._budget

    def is_overloaded(self) -> bool:
        """Whether utilisation is at or above ``compaction_ratio``."""
        return self.utilization() >= self._compaction_ratio

    def __len__(self) -> int:
        """Active turns including the digest slot (0 or 1)."""
        with self._lock:
            return len(self._entries) + (1 if self._summary is not None else 0)

    # -- mutation -----------------------------------------------------------
    def add(self, role: str, content: str) -> ContextEntry:
        """Append one message; auto-compacts afterwards when overloaded."""
        entry = ContextEntry(Message(role, content), self.tokens_for(content))
        with self._lock:
            self._entries.append(entry)
            if self._auto_compact:
                self._compact_locked()
            return entry

    def add_message(self, message: Message) -> ContextEntry:
        """Append a ready-made :class:`Message`."""
        return self.add(message.role, message.content)

    def extend(self, messages: Sequence[Message]) -> int:
        """Append several messages; returns how many were appended."""
        for message in messages:
            self.add(message.role, message.content)
        return len(messages)

    def history(self) -> list[Message]:
        """The active conversation (digest first, then real turns)."""
        with self._lock:
            messages = [self._summary.message] if self._summary is not None else []
            messages.extend(entry.message for entry in self._entries)
            return messages

    def entries(self) -> list[ContextEntry]:
        """The active conversation with token metadata (digest included)."""
        with self._lock:
            result = [self._summary] if self._summary is not None else []
            result.extend(self._entries)
            return result

    def clear(self) -> None:
        """Drop the active history (digest included); offloaded turns are kept."""
        with self._lock:
            self._summary = None
            self._entries.clear()

    # -- compaction ---------------------------------------------------------
    def compact(self, *, keep_at_least: int = 2) -> CompactionResult:
        """Evict the oldest turns until utilisation drops below the ratio.

        Evicted turns land in the offload store; the digest (if a summarizer is
        configured) is rewritten to cover them plus the previous digest.
        """
        with self._lock:
            return self._compact_locked(keep_at_least=keep_at_least)

    def _compact_locked(self, *, keep_at_least: int = 2) -> CompactionResult:
        offloaded_before = len(self._offloaded)
        removed: list[ContextEntry] = []
        freed = 0
        while len(self._entries) > keep_at_least and self.is_overloaded():
            entry = self._entries.pop(0)
            removed.append(entry)
            freed += entry.tokens
        if not removed:
            return CompactionResult(
                removed=0, kept=self._kept_count(), tokens_freed=0
            )
        self._offloaded.extend(removed)
        summary: str | None = None
        if self._summarizer is not None:
            batch: list[Message] = [entry.message for entry in removed]
            if self._summary is not None:
                batch.insert(0, self._summary.message)
            summary = self._summarizer(batch)
            self._summary = ContextEntry(
                Message(self._summary_role, summary), self.tokens_for(summary)
            )
            freed -= self._summary.tokens
            # A dense digest can tip utilization back over the ratio: keep
            # evicting the oldest real turns until under the ratio or floor.
            while len(self._entries) > keep_at_least and self.is_overloaded():
                extra = self._entries.pop(0)
                self._offloaded.append(extra)
                freed -= extra.tokens
        return CompactionResult(
            removed=len(removed),
            kept=self._kept_count(),
            tokens_freed=max(0, freed),
            summary=summary,
            offloaded=len(self._offloaded) - offloaded_before,
        )

    def _kept_count(self) -> int:
        return len(self._entries) + (1 if self._summary is not None else 0)

    def compact_oldest(self, count: int) -> CompactionResult:
        """Always evict the ``count`` oldest turns (no load check), folding them
        into the digest when a summarizer is configured."""
        if count < 1:
            raise ValueError("count must be >= 1")
        with self._lock:
            offloaded_before = len(self._offloaded)
            removed = self._entries[:count]
            if not removed:
                return CompactionResult(
                    removed=0, kept=self._kept_count(), tokens_freed=0
                )
            del self._entries[:count]
            self._offloaded.extend(removed)
            freed = sum(entry.tokens for entry in removed)
            summary: str | None = None
            if self._summarizer is not None:
                batch: list[Message] = [entry.message for entry in removed]
                if self._summary is not None:
                    batch.insert(0, self._summary.message)
                summary = self._summarizer(batch)
                self._summary = ContextEntry(
                    Message(self._summary_role, summary), self.tokens_for(summary)
                )
                freed -= self._summary.tokens
                while len(self._entries) > 0 and self.is_overloaded():
                    extra = self._entries.pop(0)
                    self._offloaded.append(extra)
                    freed -= extra.tokens
            return CompactionResult(
                removed=len(removed),
                kept=self._kept_count(),
                tokens_freed=max(0, freed),
                summary=summary,
                offloaded=len(self._offloaded) - offloaded_before,
            )

    # -- offload ------------------------------------------------------------
    def offloaded(self) -> list[ContextEntry]:
        """The evicted turns, oldest first."""
        with self._lock:
            return list(self._offloaded)

    def restore(self, *, limit: int | None = None) -> int:
        """Restore the most recent offloaded turns while the budget allows.

        Returns how many turns were restored. Turns that do not fit are left
        offloaded — nothing is ever silently dropped for lack of space.
        """
        restored = 0
        with self._lock:
            while self._offloaded and (limit is None or restored < limit):
                candidate = self._offloaded[-1]
                if candidate.tokens > self.remaining():
                    break
                self._offloaded.pop()
                self._entries.append(candidate)
                restored += 1
        return restored

    # -- snapshots ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """A JSON-serialisable state read (budget, usage, entries, offload)."""
        with self._lock:
            used = self.used_tokens()
            return {
                "budget": self._budget,
                "compaction_ratio": self._compaction_ratio,
                "used_tokens": used,
                "remaining": max(0, self._budget - used),
                "utilization": round(used / self._budget, 4),
                "active_entries": [entry.to_dict() for entry in self.entries()],
                "offloaded_count": len(self._offloaded),
            }