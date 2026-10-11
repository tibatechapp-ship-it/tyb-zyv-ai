"""Tests for src.core.resilience — retries, rate limiting, cache, queue, fallback."""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.core.resilience import (
    BackendUnavailable,
    CommandQueue,
    FallbackBackend,
    RateLimiter,
    ResilientBackend,
    ResponseCache,
    RetryPolicy,
    retry_call,
)
from src.core.types import Message


class _Clock:
    """A manually advanced monotonic clock + its matching sleeper."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class ScriptedBackend:
    """A backend whose calls return (or raise) a scripted sequence of results."""

    def __init__(self, results: Sequence[object]) -> None:
        self.results = list(results)
        self.calls: list[str | None] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.calls.append(model)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return str(result)


# ── RetryPolicy ──────────────────────────────────────────────────────────
def test_retry_policy_defaults_and_delays() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 5
    assert policy.delays() == [5.0, 10.0, 20.0, 40.0]


def test_retry_policy_caps_at_max_delay() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, factor=10.0, max_delay=50.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 10.0
    assert policy.delay_for(3) == 50.0
    assert policy.delay_for(4) == 50.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay": -1.0},
        {"factor": 0.5},
        {"max_delay": -1.0},
        {"jitter": 1.0},
        {"jitter": -0.1},
    ],
)
def test_retry_policy_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


# ── retry_call ───────────────────────────────────────────────────────────
def test_retry_call_succeeds_first_try() -> None:
    assert retry_call(lambda: "ok", sleep=lambda _s: None) == "ok"


def test_retry_call_recovers_and_reports() -> None:
    attempts: list[int] = []
    slept: list[float] = []

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("nope")
        return "done"

    result = retry_call(
        flaky,
        RetryPolicy(max_attempts=5, base_delay=2.0),
        on_retry=lambda attempt, delay, exc: slept.append(delay),
        sleep=lambda s: None,
    )
    assert result == "done"
    assert len(attempts) == 3
    assert slept == [2.0, 4.0]


def test_retry_call_exhausts_and_raises_last() -> None:
    calls: list[int] = []

    def always_fail() -> str:
        calls.append(1)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        retry_call(always_fail, RetryPolicy(max_attempts=3), sleep=lambda _s: None)
    assert len(calls) == 3


def test_retry_call_jitter_stays_in_bounds() -> None:
    slept: list[float] = []
    policy = RetryPolicy(max_attempts=2, base_delay=10.0, jitter=0.5)

    def fail() -> str:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        retry_call(fail, policy, sleep=slept.append, rng=random.Random(0))
    assert len(slept) == 1
    assert 5.0 <= slept[0] <= 15.0


# ── RateLimiter ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs", [{"max_calls": 0, "window": 1.0}, {"max_calls": 1, "window": 0.0}])
def test_rate_limiter_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RateLimiter(**kwargs)


def test_rate_limiter_try_acquire_and_properties() -> None:
    clock = _Clock()
    limiter = RateLimiter(2, 10.0, clock=clock.now, sleep=clock.sleep)
    assert limiter.max_calls == 2 and limiter.window == 10.0
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False


def test_rate_limiter_acquire_waits_for_a_slot() -> None:
    clock = _Clock()
    limiter = RateLimiter(2, 10.0, clock=clock.now, sleep=clock.sleep)
    limiter.try_acquire()
    limiter.try_acquire()
    waited = limiter.acquire()
    assert waited == 10.0
    assert clock.now() == 10.0


# ── ResponseCache ────────────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs", [{"ttl": 0}, {"maxsize": 0}])
def test_response_cache_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ResponseCache(**kwargs)


def test_response_cache_put_get_expiry_and_clear() -> None:
    clock = _Clock()
    cache = ResponseCache(ttl=100.0, clock=clock.now)
    assert cache.ttl == 100.0
    assert cache.get("missing") is None
    cache.put("k", "v")
    assert cache.get("k") == "v"
    assert len(cache) == 1
    clock.t = 100.0
    assert cache.get("k") is None  # expired
    assert len(cache) == 0
    cache.put("k", "v")
    cache.clear()
    assert len(cache) == 0


def test_response_cache_lru_eviction() -> None:
    clock = _Clock()
    cache = ResponseCache(ttl=100.0, maxsize=2, clock=clock.now)
    cache.put("a", "1")
    cache.put("b", "2")
    cache.get("a")  # refresh recency
    cache.put("c", "3")  # evicts "b"
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"


def test_response_cache_make_key_is_stable_and_sensitive() -> None:
    msgs = [Message("user", "hi")]
    key = ResponseCache.make_key(msgs, "m1")
    assert key == ResponseCache.make_key([Message("user", "hi")], "m1")
    assert key != ResponseCache.make_key(msgs, "m2")
    assert key != ResponseCache.make_key([Message("user", "yo")], "m1")


# ── CommandQueue ─────────────────────────────────────────────────────────
def test_command_queue_basic_ops(tmp_path: Path) -> None:
    queue = CommandQueue(tmp_path / "q.json")
    assert queue.path == tmp_path / "q.json"
    assert len(queue) == 0
    assert queue.peek() is None
    assert queue.dequeue() is None
    assert queue.enqueue({"a": 1}) == 1
    queue.enqueue({"b": 2})
    assert queue.peek() == {"a": 1}
    assert queue.pending() == [{"a": 1}, {"b": 2}]
    assert queue.dequeue() == {"a": 1}
    queue.clear()
    assert len(queue) == 0


def test_command_queue_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "q.json"
    CommandQueue(path).enqueue({"x": 1})
    reloaded = CommandQueue(path)
    assert reloaded.pending() == [{"x": 1}]


def test_command_queue_handles_corrupt_and_non_list_files(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text("not json", encoding="utf-8")
    assert len(CommandQueue(path)) == 0
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert len(CommandQueue(path)) == 0
    path.write_text(json.dumps([1, {"ok": True}, "x"]), encoding="utf-8")
    assert CommandQueue(path).pending() == [{"ok": True}]


def test_command_queue_drain_stops_on_failure(tmp_path: Path) -> None:
    queue = CommandQueue(tmp_path / "q.json")
    queue.enqueue({"n": 1})
    queue.enqueue({"n": 2})

    def handler(item: dict[str, object]) -> None:
        if item["n"] == 2:
            raise RuntimeError("still offline")

    assert queue.drain(handler) == 1
    assert queue.pending() == [{"n": 2}]


def test_command_queue_drain_empty(tmp_path: Path) -> None:
    assert CommandQueue(tmp_path / "q.json").drain(lambda _item: None) == 0


# ── FallbackBackend ──────────────────────────────────────────────────────
def test_fallback_backend_requires_models() -> None:
    with pytest.raises(ValueError):
        FallbackBackend(ScriptedBackend(["x"]), [])


def test_fallback_backend_primary_success() -> None:
    backend = ScriptedBackend(["primary"])
    fb = FallbackBackend(backend, ["primary", "backup"])
    assert fb.models == ["primary", "backup"]
    assert fb.generate([Message("user", "hi")]) == "primary"
    assert backend.calls == ["primary"]


def test_fallback_backend_switches_on_failure() -> None:
    backend = ScriptedBackend([RuntimeError("down"), "backup"])
    seen: list[str | None] = []
    fb = FallbackBackend(backend, ["a", "b"], on_fallback=lambda m, _e: seen.append(m))
    assert fb.generate([Message("user", "hi")]) == "backup"
    assert backend.calls == ["a", "b"]
    assert seen == ["a"]


def test_fallback_backend_raises_when_all_fail() -> None:
    backend = ScriptedBackend([RuntimeError("a"), RuntimeError("b")])
    fb = FallbackBackend(backend, ["a", "b"])
    with pytest.raises(RuntimeError, match="b"):
        fb.generate([Message("user", "hi")])


def test_fallback_backend_prepends_given_model_and_dedupes() -> None:
    backend = ScriptedBackend([RuntimeError("x"), "ok"])
    fb = FallbackBackend(backend, ["a", "b"])
    assert fb.generate([Message("user", "hi")], model="a") == "ok"
    assert backend.calls == ["a", "b"]  # "a" tried once, then "b"


# ── ResilientBackend ─────────────────────────────────────────────────────
def test_resilient_backend_caches_repeated_calls() -> None:
    backend = ScriptedBackend(["answer"])
    cache = ResponseCache(ttl=3600.0, clock=_Clock().now)
    resilient = ResilientBackend(backend, cache=cache, policy=RetryPolicy(max_attempts=1))
    msgs = [Message("user", "q")]
    assert resilient.generate(msgs) == "answer"
    assert resilient.generate(msgs) == "answer"
    assert backend.calls == [None]  # second call served from cache
    assert resilient.stats["cache_hits"] == 1
    assert resilient.stats["calls"] == 2


def test_resilient_backend_uses_fallback_models() -> None:
    backend = ScriptedBackend([RuntimeError("primary down"), "from backup"])
    resilient = ResilientBackend(
        backend, models=["primary", "backup"], policy=RetryPolicy(max_attempts=1)
    )
    assert resilient.generate([Message("user", "q")]) == "from backup"
    assert backend.calls == ["primary", "backup"]
    assert resilient.stats["fallbacks"] == 1


def test_resilient_backend_queues_on_total_failure(tmp_path: Path) -> None:
    backend = ScriptedBackend([RuntimeError("down"), RuntimeError("down")])
    queue = CommandQueue(tmp_path / "q.json")
    resilient = ResilientBackend(
        backend, models=["a", "b"], queue=queue, policy=RetryPolicy(max_attempts=1)
    )
    with pytest.raises(BackendUnavailable):
        resilient.generate([Message("user", "please queue me")], model="a")
    assert len(queue) == 1
    assert resilient.stats["queued"] == 1
    assert resilient.stats["failures"] == 1
    item = queue.peek()
    assert item is not None
    assert item["messages"][0]["content"] == "please queue me"


def test_resilient_backend_reraises_without_queue() -> None:
    backend = ScriptedBackend([RuntimeError("down")])
    resilient = ResilientBackend(backend, policy=RetryPolicy(max_attempts=1))
    with pytest.raises(RuntimeError, match="down"):
        resilient.generate([Message("user", "q")])


def test_resilient_backend_replay_drains_queue(tmp_path: Path) -> None:
    queue = CommandQueue(tmp_path / "q.json")
    queue.enqueue({"model": "m", "messages": [{"role": "user", "content": "queued"}]})
    queue.enqueue({"model": None, "messages": [{"role": "user", "content": "second"}]})
    backend = ScriptedBackend(["ok1", "ok2"])
    resilient = ResilientBackend(backend, queue=queue, policy=RetryPolicy(max_attempts=1))
    assert resilient.replay() == 2
    assert len(queue) == 0
    assert backend.calls == ["m", None]


def test_resilient_backend_replay_without_queue() -> None:
    assert ResilientBackend(ScriptedBackend([])).replay() == 0


def test_resilient_backend_replay_stops_on_failure(tmp_path: Path) -> None:
    queue = CommandQueue(tmp_path / "q.json")
    queue.enqueue({"messages": [{"role": "user", "content": "still down"}]})
    backend = ScriptedBackend([RuntimeError("nope")])
    resilient = ResilientBackend(backend, queue=queue, policy=RetryPolicy(max_attempts=1))
    assert resilient.replay() == 0
    assert len(queue) == 1  # kept for the next attempt


def test_resilient_backend_uses_rate_limiter() -> None:
    backend = ScriptedBackend(["a", "b"])
    limiter = RateLimiter(10, 1.0, clock=_Clock().now, sleep=lambda _s: None)
    resilient = ResilientBackend(backend, limiter=limiter, policy=RetryPolicy(max_attempts=1))
    resilient.generate([Message("user", "1")])
    resilient.generate([Message("user", "2")])
    assert backend.calls == [None, None]


def test_resilient_backend_default_logger_and_bad_item_replay(tmp_path: Path) -> None:
    logger = logging.getLogger("resilience.test")
    queue = CommandQueue(tmp_path / "q.json")
    queue.enqueue({"messages": "not-a-list"})  # exercises the defensive branch
    backend = ScriptedBackend(["ok"])
    resilient = ResilientBackend(backend, queue=queue, logger=logger, policy=RetryPolicy(max_attempts=1))
    assert resilient.logger is logger
    assert resilient.replay() == 1
    assert backend.calls == [None]
