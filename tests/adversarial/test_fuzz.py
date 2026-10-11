"""Bonus category 7 — Fuzzing (deterministic, dependency-free).

Hypothesis is unavailable on this platform, so this is an in-repo property-based
fuzzer: a seeded RNG drives thousands of hostile strings through the framework
and asserts two invariants that must hold for *every* input — the run never
crashes, and the bytes of the task survive the trip losslessly (multi-language,
emoji, control characters and all). Seeds are fixed, so a failure reproduces.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from src.agents.coder import CoderAgent
from src.core.resilience import CommandQueue, ResponseCache
from src.core.types import Message

from deep_helpers import RecordingBackend

CATEGORY = "7-fuzz"

#: A hostile alphabet: ASCII, control chars, accents, Arabic, CJK, emoji.
_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " \t\n\r\x00\x1b!@#$%^&*()[]{}</\\|;:'\",.=+-_`~"
    "àéîöüñçßøå"
    "منفضلك اعمل commit الآن"
    "你好世界こんにちは"
    "🙂🔥✅❌️"
)


def _random_text(rng: random.Random, max_len: int = 48) -> str:
    """A random string drawn from the hostile alphabet."""
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, max_len)))


def _random_json_value(rng: random.Random) -> object:
    """A random JSON-encodable value (dict / list / scalar / None)."""
    choice = rng.randint(0, 4)
    if choice == 0:
        return {"k": _random_text(rng, 8), "n": rng.randint(-99, 99)}
    if choice == 1:
        return _random_text(rng, 12)
    if choice == 2:
        return rng.randint(-10**6, 10**6)
    if choice == 3:
        return rng.choice([True, False, None])
    return [rng.randint(0, 9) for _ in range(rng.randint(0, 4))]


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fuzz_agent_input_is_lossless(seed: int) -> None:
    """100 random hostile tasks per seed: never crash, never mutate the bytes."""
    rng = random.Random(seed)
    for _ in range(100):
        payload = _random_text(rng)
        backend = RecordingBackend(reply="ok")
        result = CoderAgent(backend).run(payload)
        assert result.ok is True
        assert isinstance(result.output, str)
        assert backend.last_user_message() == payload


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [4, 5])
def test_fuzz_message_dict_is_json_safe(seed: int) -> None:
    """A message laid out as a dict must survive a JSON round-trip intact."""
    rng = random.Random(seed)
    for _ in range(100):
        content = _random_text(rng)
        role = rng.choice(["user", "assistant", "system", "tool", ""])
        as_dict = Message(role, content).as_dict()
        round_tripped = json.loads(json.dumps(as_dict, ensure_ascii=False))
        assert round_tripped == as_dict
        assert round_tripped["content"] == content


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [6, 7])
def test_fuzz_command_queue_ignores_non_dicts(tmp_path: Path, seed: int) -> None:
    """A corrupt/partial queue file is loaded defensively, keeping only dicts."""
    rng = random.Random(seed)
    path = tmp_path / "queue.json"
    for _ in range(40):
        data = [_random_json_value(rng) for _ in range(rng.randint(0, 6))]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        queue = CommandQueue(path)
        pending = queue.pending()
        assert isinstance(pending, list)
        assert all(isinstance(item, dict) for item in pending)
        assert len(queue) == len(pending)


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [8])
def test_fuzz_cache_roundtrip_is_lossless(seed: int) -> None:
    """Random unicode keys/values survive a cache put/get byte-for-byte."""
    rng = random.Random(seed)
    cache = ResponseCache(ttl=3600.0, maxsize=256, clock=lambda: 0.0)
    for _ in range(150):
        key = _random_text(rng, 24)
        value = _random_text(rng, 48)
        cache.put(key, value)
        assert cache.get(key) == value
