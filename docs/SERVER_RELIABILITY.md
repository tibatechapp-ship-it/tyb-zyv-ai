# Server reliability layer

How the framework keeps talking to ZYVO when a phone, a flaky network and a
long-running process conspire against it. Everything here lives in
`src/core/` and is standard-library only.

---

## The idea

`LLMBackend` (`src/core/backend.py`) is deliberately a one-method protocol:
`generate(messages, *, model) -> str`. All the machinery that makes that call
*survivable* is layered **above** it and composed by
`ResilientBackend` (`src/core/resilience.py`).

```
      generate()
          │
   ┌──────▼───────┐   rate limit (sliding window)
   │ RateLimiter  │
   ├──────▼───────┤   cache lookup (TTL, default 1 h)
   │ ResponseCache│
   ├──────▼───────┤   retry × fallback models
   │ retry_call   │──► FallbackBackend ──► ZYVO backend
   ├──────▼───────┤   cache store  ── or ──  CommandQueue.enqueue()
   └──────────────┘
```

---

## The pieces

| Class / function | File | Responsibility |
|------------------|------|----------------|
| `RetryPolicy` | `resilience.py` | Backoff schedule (default 5s, 10s, 20s, 40s; 5 attempts). |
| `retry_call` | `resilience.py` | Runs a callable until success or the policy is spent. |
| `RateLimiter` | `resilience.py` | Sliding-window limit: at most *N* calls per *window* seconds. |
| `ResponseCache` | `resilience.py` | In-memory TTL cache of replies (default 3600 s, LRU-evicted). |
| `CommandQueue` | `resilience.py` | Persistent FIFO of requests captured while offline. |
| `FallbackBackend` | `resilience.py` | Try the primary model, then each alternate. |
| `ResilientBackend` | `resilience.py` | Composes all of the above around any backend. |
| `JsonFormatter`, `setup_json_logging` | `logging_utils.py` | One JSON object per line, daily rotation. |
| `Watchdog` | `watchdog.py` | Health-check + restart every interval (default 30 s). |
| `Heartbeat` | `watchdog.py` | Periodic "still alive" ping (default 300 s). |

---

## Error handling & retries

`retry_call` retries on **any** exception and re-raises the last one when the
policy is exhausted. The default `RetryPolicy()` waits 5, 10, 20 and 40 seconds
between the five attempts:

```python
from src.core.resilience import RetryPolicy, retry_call

result = retry_call(
    lambda: backend.generate(messages),
    RetryPolicy(),                       # delays() == [5, 10, 20, 40]
    on_retry=lambda attempt, delay, exc: log.warning("retry %s in %.1fs", attempt, delay),
)
```

`on_retry`, `sleep` and `rng` are injectable, so tests run instantly and
deterministically (no real timers, no randomness leakage).

## Rate limiting

```python
from src.core.resilience import RateLimiter

limiter = RateLimiter(max_calls=60, window=60.0)   # ≤ 60 calls / minute
limiter.acquire()                                   # blocks until a slot is free
```

## Caching

Identical `(messages, model)` pairs are served from memory for one hour:

```python
from src.core.resilience import ResponseCache
cache = ResponseCache(ttl=3600.0, maxsize=256)      # key = sha256(messages + model)
```

## Queue (offline capture)

When every attempt fails, the request is written **atomically** to disk so it is
not lost:

```python
from src.core.resilience import CommandQueue
queue = CommandQueue("~/.local/state/zyvo/queue.json")
queue.enqueue({"model": None, "messages": [{"role": "user", "content": "…"}]})
queue.drain(handle)      # stops at the first failure, keeping the bad item
```

## Fallback models

```python
from src.core.resilience import FallbackBackend
fb = FallbackBackend(backend, ["big-pickle", "small-fast"])
fb.generate(messages)    # big-pickle first; on failure, small-fast
```

## Putting it together

```python
from src.core.logging_utils import setup_json_logging
from src.core.resilience import (CommandQueue, RateLimiter, ResilientBackend,
                                 ResponseCache, RetryPolicy)
from src.core.zyvo_client import ZyvoBackend, ZyvoClient

log = setup_json_logging("~/.local/state/zyvo/server.jsonl", logger="zyvo.server")

backend = ResilientBackend(
    ZyvoBackend(ZyvoClient(port=4142)),
    models=["big-pickle", "small-fast"],
    policy=RetryPolicy(),
    cache=ResponseCache(ttl=3600.0),
    limiter=RateLimiter(60, 60.0),
    queue=CommandQueue("~/.local/state/zyvo/queue.json"),
    logger=log,
)

reply = backend.generate([Message("user", "hello")])
backend.replay()   # once the server is back: run everything that was queued
```

When all attempts fail and a queue is attached, `generate` raises
`BackendUnavailable` **after** storing the request — nothing is silently dropped.

---

## Structured logging (JSON, daily rotation)

```python
log = setup_json_logging("~/.local/state/zyvo/server.jsonl", logger="zyvo.server",
                         backup_count=7, also_stderr=True)
log.info("started", extra={"port": 4142})
```

Each line is a standalone JSON object; `extra=…` fields are merged in:

```json
{"time":"2026-10-11T08:52:51+00:00","level":"INFO","logger":"zyvo.server","message":"started","port":4142}
```

`TimedRotatingFileHandler(when="midnight")` rotates by calendar day and keeps
`backup_count` days.

---

## Watchdog + heartbeat

`Watchdog` runs a health check on an interval and calls a restart hook when it
reports the service is down. `Heartbeat` fires a periodic message.

The shipped monitor (`scripts/health_monitor.py`) ties both together for the
Telegram stack:

- every **30 s** it checks the `zyvo-telegram` runit service **and** a TCP
  connect to the ZYVO server port; if either is down it runs `sv restart zyvo-telegram`;
- every **5 min** it posts a Telegram "bot alive" heartbeat to the owner;
- all events go to `~/.local/state/zyvo/health_monitor.jsonl`.

```bash
python scripts/health_monitor.py --status   # one-shot JSON health report
python scripts/health_monitor.py --once     # one check + a forced heartbeat, exit
python scripts/health_monitor.py            # run forever (under runit)
```

Install it as a service (Termux):

```bash
mkdir -p "$PREFIX/var/service/zyvo-health/log" "$PREFIX/var/log/zyvo-health"
# write the two run scripts (see Repository layout), then:
chmod +x "$PREFIX/var/service/zyvo-health/run" "$PREFIX/var/service/zyvo-health/log/run"
export SVDIR="$PREFIX/var/service" LOGDIR="$PREFIX/var/log"
sv status zyvo-health
```

Both services start automatically on boot via Termux:Boot (`service-daemon start`).

---

## Honest limits

- The cache is **in-memory**: it is per-process and gone after a restart. The
  queue, by contrast, is on disk and survives restarts.
- A queued request is only replayed when you call `replay()` (e.g. after a
  successful health check) — the layer never silently retries in the background.
- The heartbeat proves the **process** is alive; it does not prove a full model
  turn succeeds. Combine it with a periodic real prompt if you need that.

---

Back to the [README](../README.md) · See also: [ARCHITECTURE](ARCHITECTURE.md)
