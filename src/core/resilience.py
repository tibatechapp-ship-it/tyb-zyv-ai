"""Reliability layer for the LLM backend.

:class:`src.core.backend.LLMBackend` deliberately stays a one-method protocol;
everything that makes it *survivable* in production lives here and is composed
by :class:`ResilientBackend`:

* :class:`RetryPolicy` — exponential backoff (default 5s, 10s, 20s, 40s).
* :func:`retry_call` — run a callable until it succeeds or the policy is spent.
* :class:`RateLimiter` — sliding-window limit on how often the server is hit.
* :class:`ResponseCache` — in-memory TTL cache (default one hour).
* :class:`CommandQueue` — a persistent FIFO of requests captured while offline.
* :class:`FallbackBackend` — try a primary model, then alternates.
* :class:`ResilientBackend` — glues all of the above around any backend.

Every piece takes an injectable clock/sleeper so the whole layer is testable
without real timers or a network. All code is standard library only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from src.core.backend import LLMBackend
from src.core.types import Message, utc_now_iso

__all__ = [
    "RetryPolicy",
    "retry_call",
    "RateLimiter",
    "ResponseCache",
    "CommandQueue",
    "FallbackBackend",
    "ResilientBackend",
    "BackendUnavailable",
]

T = TypeVar("T")


# ── retries ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential-backoff retry settings.

    With the defaults the delays *between* the 5 attempts are 5s, 10s, 20s and
    40s (the delay is capped at ``max_delay``).
    """

    max_attempts: int = 5
    base_delay: float = 5.0
    factor: float = 2.0
    max_delay: float = 300.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.factor < 1:
            raise ValueError("factor must be >= 1")
        if self.max_delay < 0:
            raise ValueError("max_delay must be >= 0")
        if not 0.0 <= self.jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")

    def delay_for(self, attempt: int) -> float:
        """Delay in seconds before retry number ``attempt`` (1-based)."""
        raw = self.base_delay * (self.factor ** (attempt - 1))
        return min(raw, self.max_delay)

    def delays(self) -> list[float]:
        """Every inter-attempt delay: ``max_attempts - 1`` values."""
        return [self.delay_for(i) for i in range(1, self.max_attempts)]


def retry_call(
    func: Callable[[], T],
    policy: RetryPolicy | None = None,
    *,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> T:
    """Call ``func`` until it succeeds or ``policy`` is exhausted.

    Returns the first successful value. If every attempt fails the last
    exception is re-raised. ``on_retry(attempt, delay, exc)`` — if given — runs
    just before each wait; ``sleep`` and ``rng`` are injectable for tests.
    """
    active = policy or RetryPolicy()
    generator = rng or random.Random()
    last: BaseException | None = None

    for attempt in range(1, active.max_attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - retry on any failure
            last = exc
            if attempt == active.max_attempts:
                break
            delay = active.delay_for(attempt)
            if active.jitter:
                delay *= 1.0 + generator.uniform(-active.jitter, active.jitter)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            sleep(delay)

    assert last is not None  # the loop only ends after a failure
    raise last


# ── rate limiting ────────────────────────────────────────────────────────
class RateLimiter:
    """Sliding-window limiter: at most ``max_calls`` per ``window`` seconds."""

    def __init__(
        self,
        max_calls: int,
        window: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if window <= 0:
            raise ValueError("window must be > 0")
        self._max_calls = max_calls
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._events: deque[float] = deque()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    @property
    def window(self) -> float:
        return self._window

    def _trim(self, now: float) -> None:
        cutoff = now - self._window
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def try_acquire(self) -> bool:
        """Record one call if a slot is free; return whether it was allowed."""
        now = self._clock()
        self._trim(now)
        if len(self._events) >= self._max_calls:
            return False
        self._events.append(now)
        return True

    def acquire(self) -> float:
        """Block until a slot is free, then record it. Returns seconds waited."""
        waited = 0.0
        while not self.try_acquire():
            now = self._clock()
            pause = max(self._events[0] + self._window - now, 0.0)
            self._sleep(pause)
            waited += pause
        return waited


# ── caching ──────────────────────────────────────────────────────────────
class ResponseCache:
    """In-memory TTL cache of model replies (default one hour, LRU-evicted)."""

    def __init__(
        self,
        ttl: float = 3600.0,
        *,
        maxsize: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._ttl = ttl
        self._maxsize = maxsize
        self._clock = clock
        self._store: OrderedDict[str, tuple[float, str]] = OrderedDict()

    @property
    def ttl(self) -> float:
        return self._ttl

    @staticmethod
    def make_key(messages: Sequence[Message], model: str | None) -> str:
        """Return a stable cache key for a ``(messages, model)`` pair."""
        payload = json.dumps(
            [[m.role, m.content] for m in messages] + [["@model", model]],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        """Return the cached value, or ``None`` when absent or expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def put(self, key: str, value: str) -> None:
        """Store ``value`` under ``key`` for ``ttl`` seconds."""
        self._store[key] = (self._clock() + self._ttl, value)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ── persistent queue ─────────────────────────────────────────────────────
class CommandQueue:
    """A tiny persistent FIFO of pending requests that survives restarts.

    Items are plain JSON dicts written atomically to ``path``, so a command
    captured while the network was down is executed once it returns.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._items: list[dict[str, Any]] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            self._items = []
            return
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            # ValueError covers malformed JSON (JSONDecodeError); RecursionError
            # covers a valid-but-deeply-nested JSON bomb that blows the stack.
            # Either way the queue is treated as empty, never crashing startup.
            self._items = []
            return
        self._items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def enqueue(self, item: dict[str, Any]) -> int:
        """Append ``item`` and persist; return the new queue length."""
        self._items.append(item)
        self._flush()
        return len(self._items)

    def dequeue(self) -> dict[str, Any] | None:
        """Remove and return the head item (``None`` when empty)."""
        if not self._items:
            return None
        item = self._items.pop(0)
        self._flush()
        return item

    def peek(self) -> dict[str, Any] | None:
        """Return the head item without removing it."""
        return self._items[0] if self._items else None

    def clear(self) -> None:
        """Empty the queue and persist the empty state."""
        self._items.clear()
        self._flush()

    def drain(self, handler: Callable[[dict[str, Any]], Any]) -> int:
        """Run ``handler`` over queued items FIFO until one raises.

        Returns how many were handled. On the first failure the offending item
        stays at the head (nothing is lost) and the loop stops — exactly what is
        wanted while the network is still down.
        """
        done = 0
        while self._items:
            item = self._items[0]
            try:
                handler(item)
            except Exception:  # noqa: BLE001 - keep the item, stop draining
                break
            self._items.pop(0)
            done += 1
        if done:
            self._flush()
        return done

    def pending(self) -> list[dict[str, Any]]:
        """A copy of the queued items, oldest first."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


# ── model fallback ───────────────────────────────────────────────────────
class FallbackBackend:
    """Try a primary model, then each alternate, in order.

    Every attempt drives the same underlying backend; only the ``model`` hint
    changes. If all candidates fail, the last exception is re-raised.
    """

    def __init__(
        self,
        backend: LLMBackend,
        models: Sequence[str | None],
        *,
        on_fallback: Callable[[str | None, BaseException], None] | None = None,
    ) -> None:
        if not models:
            raise ValueError("at least one model candidate is required")
        self._backend = backend
        self._models = list(models)
        self._on_fallback = on_fallback

    @property
    def models(self) -> list[str | None]:
        return list(self._models)

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        candidates: list[str | None] = [model, *self._models] if model is not None else list(self._models)
        last: BaseException | None = None
        tried: set[str | None] = set()
        for candidate in candidates:
            if candidate in tried:
                continue
            tried.add(candidate)
            try:
                return self._backend.generate(messages, model=candidate)
            except Exception as exc:  # noqa: BLE001 - fall through to the next model
                last = exc
                if self._on_fallback is not None:
                    self._on_fallback(candidate, exc)
        assert last is not None
        raise last


# ── the composed backend ─────────────────────────────────────────────────
class BackendUnavailable(RuntimeError):
    """Raised when every model failed and the request was queued instead."""


class ResilientBackend:
    """A backend wrapped in the full reliability stack.

    Per call the order is: rate-limit → cache lookup → (fallback models × retry)
    → cache store. When every attempt fails the request is optionally queued (so
    it can be replayed once the server is reachable) and
    :class:`BackendUnavailable` is raised.
    """

    def __init__(
        self,
        backend: LLMBackend,
        *,
        models: Sequence[str | None] | None = None,
        policy: RetryPolicy | None = None,
        cache: ResponseCache | None = None,
        limiter: RateLimiter | None = None,
        queue: CommandQueue | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._backend = backend
        self.policy = policy or RetryPolicy()
        self.cache = cache
        self.limiter = limiter
        self.queue = queue
        self.logger = logger or logging.getLogger("src.core.resilience")
        self.stats: dict[str, int] = {
            "calls": 0,
            "cache_hits": 0,
            "failures": 0,
            "queued": 0,
            "fallbacks": 0,
        }
        self._fallback = (
            FallbackBackend(backend, models, on_fallback=self._note_fallback)
            if models
            else None
        )

    def _note_fallback(self, model: str | None, exc: BaseException) -> None:
        self.stats["fallbacks"] += 1
        self.logger.warning("model failed; trying fallback", extra={"model": model, "error": str(exc)})

    def _attempt(self, messages: Sequence[Message], model: str | None) -> str:
        if self._fallback is not None:
            return self._fallback.generate(messages, model=model)
        return self._backend.generate(messages, model=model)

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        """Return a reply, using cache/retry/fallback, or raise if all fail."""
        self.stats["calls"] += 1
        if self.limiter is not None:
            self.limiter.acquire()

        cache_key: str | None = None
        if self.cache is not None:
            cache_key = self.cache.make_key(messages, model)
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.stats["cache_hits"] += 1
                self.logger.debug("cache hit", extra={"model": model})
                return cached

        try:
            result = retry_call(lambda: self._attempt(messages, model), self.policy)
        except Exception as exc:  # noqa: BLE001 - exhausted, decide how to recover
            self.stats["failures"] += 1
            self.logger.error("backend exhausted", extra={"model": model, "error": str(exc)})
            if self.queue is None:
                raise
            self.queue.enqueue(
                {
                    "model": model,
                    "messages": [m.as_dict() for m in messages],
                    "enqueued_at": utc_now_iso(),
                }
            )
            self.stats["queued"] += 1
            raise BackendUnavailable(str(exc)) from exc

        if self.cache is not None and cache_key is not None:
            self.cache.put(cache_key, result)
        return result

    def replay(self) -> int:
        """Execute queued requests now that the server is reachable.

        Returns how many were replayed; a failing item stops the drain and
        stays queued for the next attempt.
        """

        def handle(item: dict[str, Any]) -> str:
            raw = item.get("messages")
            messages: list[Message] = []
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict):
                        messages.append(
                            Message(
                                str(entry.get("role", "user")),
                                str(entry.get("content", "")),
                            )
                        )
            model = item.get("model")
            model_name = model if isinstance(model, str) else None
            return retry_call(lambda: self._attempt(messages, model_name), self.policy)

        if self.queue is None:
            return 0
        return self.queue.drain(handle)
