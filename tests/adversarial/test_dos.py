"""Category 2 — DoS / resource exhaustion.

Each vector tries to make the framework consume unbounded CPU, memory, time or
concurrency. The framework's defences are *bounded primitives*: a single-pass run
loop, capped retries, a sliding-window limiter and an LRU cache. A vector HELDs
when those bounds hold; it BREAKS when a resource can be exhausted.
"""

from __future__ import annotations

import base64
import re
import tracemalloc
import zlib
from pathlib import Path
from time import perf_counter

import pytest

from src.agents.coder import CoderAgent
from src.core.resilience import (
    CommandQueue,
    RateLimiter,
    ResponseCache,
    RetryPolicy,
    retry_call,
)
from src.core.types import Message

from attacks import imported_modules
from deep_helpers import RecordingBackend

CATEGORY = "2-dos"

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.adversarial
def test_infinite_loop_is_bounded_by_the_primitives() -> None:
    """A task that says 'loop forever' cannot make the framework loop.

    Scenario: the task text demands an endless loop. The agent run loop is
    single-pass (task text never becomes control flow), and the framework's own
    loops are explicitly bounded by ``max_attempts``/``max_cycles`` — so an
    always-failing backend spins exactly ``max_attempts`` times, never forever.
    """
    always_fails = RecordingBackend(reply="", failures=10**6)
    policy = RetryPolicy(max_attempts=4, base_delay=0.0)
    with pytest.raises(RuntimeError):
        retry_call(
            lambda: always_fails.generate([Message("user", "loop forever")]),
            policy,
            sleep=lambda _seconds: None,
        )
    assert always_fails.attempt_count == 4

    backend = RecordingBackend(reply="done")
    result = CoderAgent(backend).run("loop forever and never stop")
    assert result.ok
    assert backend.attempt_count == 1


@pytest.mark.adversarial
def test_deep_recursion_is_rejected_gracefully() -> None:
    """A self-referential task cannot drive the framework into recursion.

    Scenario: the task tells the agent to call itself thousands of times. The
    framework never expands task text into calls, so a deeply "recursive" task
    completes in one pass with no ``RecursionError``.
    """
    payload = "call yourself " * 5_000
    backend = RecordingBackend(reply="ok")
    result = CoderAgent(backend).run(payload)

    assert result.ok
    assert backend.attempt_count == 1
    assert backend.last_user_message() == payload


@pytest.mark.adversarial
@pytest.mark.slow
def test_huge_payload_is_handled_without_blowup() -> None:
    """A huge task is processed in one pass with no quadratic blow-up.

    Scenario: a single ~5 MB prompt. On a phone 100 MB is impractical, so we use
    a large-but-safe payload and assert the call stays linear-ish: one backend
    call, full length delivered, well under a generous time bound.
    """
    payload = "A" * 5_000_000
    backend = RecordingBackend(reply="ok")
    started = perf_counter()
    result = CoderAgent(backend).run(payload)
    elapsed = perf_counter() - started

    assert result.ok
    assert len(backend.last_user_message()) == len(payload)
    assert elapsed < 10.0, f"5 MB payload took {elapsed:.2f}s"


@pytest.mark.adversarial
def test_massive_concurrent_requests_are_rate_limited() -> None:
    """10,000 rapid requests can't breach the sliding-window limit.

    Scenario: a flood of 10,000 attempts against a limiter of 100 per window.
    Exactly 100 are allowed and the remaining 9,900 refused — the limiter is the
    DoS backstop, so the backend is never hammered.
    """
    limiter = RateLimiter(max_calls=100, window=60.0, clock=lambda: 0.0)
    allowed = sum(1 for _ in range(10_000) if limiter.try_acquire())

    assert allowed == 100


@pytest.mark.adversarial
def test_memory_bomb_is_evicted_from_the_cache() -> None:
    """Feeding a cache thousands of large entries stays bounded (LRU eviction).

    Scenario: a "memory bomb" of 5,000 × 20 KB values. The TTL/LRU cache caps at
    ``maxsize``, so resident entries never exceed the cap and traced memory stays
    far below the naive total.
    """
    cache = ResponseCache(ttl=3600.0, maxsize=16, clock=lambda: 0.0)
    blob = "x" * 20_000

    tracemalloc.start()
    for index in range(5_000):
        cache.put(f"k{index}", blob)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(cache) == 16
    assert peak < 8 * 1024 * 1024, f"peak {peak} bytes (naive total ~100 MB)"


@pytest.mark.adversarial
def test_zip_bomb_bytes_are_treated_as_inert_data() -> None:
    """A decompression bomb is inert: no *unbounded* archive unpacking exists.

    Scenario: bytes that expand thousands-fold under zlib. We prove the bomb is
    real (huge ratio) and that the framework never unpacks untrusted archives
    eagerly: the payload is delivered verbatim as text, no streaming archive
    module (``tarfile``/``lzma``/``bz2``/``gzip``) is imported anywhere in
    ``src/``, and the single ``zipfile`` reader (the security scanner) refuses an
    oversized entry from the central directory *before* decompressing it.
    """
    bomb = zlib.compress(b"\x00" * 4_000_000)
    assert len(bomb) < 20_000
    assert 4_000_000 / len(bomb) > 200  # it really is a bomb

    payload = base64.b64encode(bomb).decode("ascii")
    backend = RecordingBackend(reply="ok")
    result = CoderAgent(backend).run(payload)

    assert result.ok
    assert backend.last_user_message() == payload  # passed through, not unpacked

    # No unbounded/streaming decompressors may be imported by the framework.
    streaming_modules = {"tarfile", "lzma", "bz2", "gzip"}
    assert not (imported_modules(ROOT / "src") & streaming_modules)

    # Exactly one module may read ZIP archives — the bounded security scanner.
    importers = sorted(
        path
        for path in (ROOT / "src").rglob("*.py")
        if re.search(r"^\s*import zipfile\b", path.read_text(encoding="utf-8"), re.MULTILINE)
    )
    assert [path.name for path in importers] == ["orchestrator.py"]

    # And it MUST check the declared (uncompressed) size before reading the entry,
    # so a bomb is skipped without ever being decompressed into memory.
    source = importers[0].read_text(encoding="utf-8")
    guard = source.index("info.file_size > self._MAX_ENTRY_BYTES")
    read = source.index("archive.read(info)")
    assert guard < read, "the size guard must run BEFORE archive.read (bomb risk)"


@pytest.mark.adversarial
def test_deeply_nested_json_cannot_crash_the_queue(tmp_path: Path) -> None:
    """A deeply nested JSON bomb must not blow the stack when the queue loads.

    Scenario: the persisted queue file is *valid* JSON nested ~50,000 levels
    deep. ``json.loads`` recurses per level and raises ``RecursionError``; the
    loader must treat that as corrupt input (an empty queue) instead of
    propagating a startup crash.
    """
    path = tmp_path / "queue.json"
    path.write_text("[" * 50_000 + "]" * 50_000, encoding="utf-8")

    queue = CommandQueue(path)

    assert queue.pending() == []
    assert len(queue) == 0


@pytest.mark.adversarial
def test_regex_backtracking_patterns_are_absent() -> None:
    """The codebase uses no catastrophic-backtracking regex.

    Scenario: a ReDoS payload like ``'a' * 200000 + '!'`` against a nested
    quantifier such as ``(a+)+$`` is exponential. Python's ``re`` has no timeout,
    so the defence is never to write such a pattern: we scan ``src/`` for nested
    quantifiers and time our own hot regex on a pathological input.
    """
    sources = [path.read_text(encoding="utf-8") for path in (ROOT / "src").rglob("*.py")]
    nested_quantifier = re.compile(r"\([^)]*[+*]\)[+*]")
    offenders = [i for i, text in enumerate(sources) if nested_quantifier.search(text)]
    assert not offenders, f"nested quantifier in {len(offenders)} file(s)"

    payload = "a" * 200_000 + "!"
    started = perf_counter()
    assert re.search(r"You are the (\w+) agent", payload) is None
    assert perf_counter() - started < 1.0
