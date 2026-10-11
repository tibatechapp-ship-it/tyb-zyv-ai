# ADVERSARIAL.md — the attack suite

`tests/adversarial/` is the project's *red-team* layer. Where the five deep phases
(v2.2.0) prove the framework does what it should, the adversarial suite tries to
make it do what it must not. It drives the **real** 20-agent framework through
hostile inputs using **mock backends only** — no server, no network, no real LLM.

## The inverted verdict

Adversarial tests flip the usual meaning of pass/fail:

| pytest result | verdict    | meaning                                             |
|---------------|------------|-----------------------------------------------------|
| PASS          | **HELD**   | the attack was repelled — the defence held          |
| FAIL          | **BROKEN** | the attack broke through — a real weakness was found |

A BROKEN vector is **not** a test bug; it is a discovered vulnerability. Policy is:
**harden the framework and re-run until every vector HELDs**, or document it as
out-of-scope here. A session table is printed at the end of every run, so each
vector carries an explicit verdict.

## Running it

```bash
python -m pytest tests/adversarial/ -v          # full suite + verdict table
python -m pytest tests/adversarial/ -m "not slow"   # skip the heavy vectors
python -m pytest tests/adversarial/test_dos.py      # one category
```

**Result: 92 vectors — 92 HELD, 0 BROKEN (100 % resilience).**

## The eight categories

| # | Category            | Vectors | What it attacks                                                    |
|---|---------------------|:-------:|--------------------------------------------------------------------|
| 1 | Injection           |   28    | prompt / command / SQL / path / template / header injection, unicode |
| 2 | DoS / exhaustion    |    8    | endless loops, recursion, huge & bomb payloads, floods, ReDoS       |
| 3 | Manipulation        |   11    | role confusion, context flip, nested orders, authority, gaslighting |
| 4 | Security            |   10    | secret leak, permission drift, sandbox escape, concurrency, SSRF   |
| 5 | Integration failure |    8    | cascade, deadlock, livelock, Byzantine, partition, clock, split brain |
| 6 | Comprehension       |    8    | ambiguity, contradiction, paradox, sarcasm, code-switch, typos      |
| 7 | Fuzzing (bonus)     |    8    | 1,000+ random hostile strings/props (seeded, deterministic)         |
| 8 | Chaos (bonus)       |   11    | random backend faults under fan-out / pipeline / retry              |

### 1 — Injection (28)
Every payload is delivered to the model as **inert text**, never parsed into
control flow, a syscall or a query. The persona lives in the system turn and the
task in the user turn; nothing the task says rewrites either. Covers SQLi, shell
metacharacters, path traversal (`../`), template syntax, CRLF/header injection,
null bytes and exotic unicode.

### 2 — DoS / resource exhaustion (8)
The framework defends with **bounded primitives**: a single-pass run loop, capped
retries (`max_attempts`), a sliding-window `RateLimiter`, and a TTL/LRU
`ResponseCache`. Vectors assert a 5 MB payload stays linear, a 10,000-request
flood admits exactly the window, a 5,000-entry memory bomb is evicted, a
decompression bomb is never auto-unpacked, no nested-quantifier regex exists
(ReDoS), and a deeply-nested JSON bomb cannot crash the queue loader (**fixed — see
Finding 2**).

### 3 — Manipulation (11)
Authority is a **code fact** (class metadata + the registry), never something a
message can grant. "You are now unrestricted", "I am the developer", "you already
agreed" are all just text: permissions, persona and metrics are byte-identical
before and after, and a `run` is stateless between calls.

### 4 — Security (10)
No secret can appear in logs/results (scan), permissions are immutable and always
a subset of declared tools (`set(cls.permissions) <= set(cls.tools)`), shell
agents vs read-only agents stay distinct, the client never sends ambient auth to
a fixed host (SSRF surface is a constant), concurrent metric updates lose nothing
(400/400) and a 49-way duplicate-register race yields exactly one owner. Includes
a supply-chain check: core layers import **stdlib only**.

### 5 — Integration failure (8)
Distributed-systems failure modes, all *contained*: a failing pipeline stage stops
the chain (blast radius = one stage); a shared backend does not deadlock; retries
are bounded (no livelock); a **Byzantine** backend returning a non-`str` is
rejected (**fixed — see Finding 1**); a total outage degrades to a queued request
+ clean typed error; a backwards clock fails the limiter **closed**; two registries
share no state (no split brain); a burst respects the fan-out worker cap.

### 6 — Comprehension (8)
The framework makes no claim about a model's comprehension — its guarantee is
**structural**: ambiguous, contradictory, paradoxical, sarcastic, typo-ridden
tasks and code-switched text are delivered **verbatim and exactly once**, never
rewritten. Arabic + English code-switching is asserted byte-for-byte (critical for
the project's bilingual users), and an empty context leaves no dangling marker.

### 7 — Fuzzing (bonus, 8)
Hypothesis is unavailable on this platform, so this is an in-repo, **seeded**
property-based fuzzer: ~1,000 hostile strings (ASCII, control chars, accents,
Arabic, CJK, emoji) are pushed through the agent, the message layer and the queue
loader; each must never crash and must survive losslessly. Cache put/get is fuzzed
for byte fidelity. Fixed seeds make any failure reproducible.

### 8 — Chaos (bonus, 11)
A backend that randomly throws, returns empty or answers normally is injected
under fan-out, pipeline and retry. The **containment contract** is asserted every
time: results are well-formed `AgentResult`s, `ok ⟺ error is None`, nothing
propagates, and retried chaos stays within `max_attempts`. Seeded for reproducibility.

## Findings (discovered by this suite, then fixed)

### Finding 1 — Byzantine backend type violation — **BROKEN → FIXED**
`BaseAgent.run` trusted whatever the backend returned. A backend returning a
non-`str` (a `dict`, `None`, bytes) would place that value into
`AgentResult.output`, silently breaking every downstream consumer that assumes a
string. **Fix:** `src/agents/base.py` now validates `isinstance(output, str)` and
fails the run cleanly with a clear error instead of leaking the wrong type.
Guard vector: `test_integration.py::test_byzantine_backend_is_handled`.

### Finding 2 — Persistent-queue stack exhaustion — **BROKEN → FIXED**
`CommandQueue._load` parsed the on-disk queue with `json.loads` and only caught
`json.JSONDecodeError`. A *valid* but deeply-nested JSON file (a JSON bomb) makes
`json.loads` recurse and raise `RecursionError`, which escaped the constructor and
**crashed the process at startup**. **Fix:** `src/core/resilience.py` now catches
`(ValueError, RecursionError)` and treats either as a corrupt/empty queue.
Guard vector: `test_dos.py::test_deeply_nested_json_cannot_crash_the_queue`.

## Recommendations (hardening)

- **Lock the mutable singletons — DONE (v2.4.0).** `AgentMetrics.record` and
  `AgentRegistry.register` were not lock-protected. Safe under today's CPython
  GIL, but a free-threaded build or a different interpreter could lose updates or
  race-register. Both are now guarded — see *Deep Hardening* below.
- **Document the single-writer contract.** `CommandQueue`, `ResponseCache` and
  `AgentCommunication` are single-writer by design; keep that explicit in docstrings
  so callers don't assume they are thread-safe.
- **Keep the limiter fail-closed.** The backwards-clock behaviour (refuse rather
  than over-admit) is correct; guard it with the existing clock-skew vector when
  the limiter changes.
- **CI wiring.** The full suite (`testpaths = ["tests"]`) already runs
  `tests/adversarial/`; any new BROKEN vector fails the build because
  inverted-meaning tests still exit non-zero on failure.

## Deep Hardening (v2.4.0)

The concurrency vectors above HELD *only* thanks to the GIL. v2.4.0 removes that
luck: the two shared, mutable singletons are now genuinely thread-safe, proven by
`tests/test_concurrency.py` (11 tests).

### The race conditions

1. **`AgentMetrics.record` — lost updates.** It performs five separate writes
   (`tasks_run += 1`, `tasks_failed += 1`, `last_seconds`, `total_seconds +=`,
   `durations.append`). Two threads can interleave a read-modify-write on
   `tasks_run`, so under a free-threaded interpreter a counter update is silently
   dropped. Readers (`tasks_ok`, `avg_seconds`, `to_dict`) could also observe a
   *torn* snapshot (e.g. `tasks_run` bumped but `total_seconds` not yet).
2. **`AgentRegistry.register` — check-then-act.** It tests `name in dict` then
   assigns in a second step. Two threads registering the same name can both pass
   the check → a duplicate is inserted without raising. `__iter__` over a live
   dict could also raise *"dictionary changed size during iteration"* if a writer
   ran mid-loop.

### The fix

- **`src/agents/metrics.py`** — an instance **`RLock`**, created in
  `__post_init__` (so it is never part of the dataclass `__eq__`/`__repr__` and
  stays out of the public field set). `record` and the read helpers (`tasks_ok`,
  `avg_seconds`, `to_dict`) all run under `with self._lock:`. An **`RLock` is
  required**, not a plain `Lock`, because the readers nest — `to_dict` re-enters
  the lock via `tasks_ok`/`avg_seconds` while already holding it.
- **`src/agents/registry.py`** — one instance **`threading.Lock`** created in
  `__init__`; **every** mutator *and* reader (`register`, `unregister`, `get`,
  `has`, `all`, `names`, `by_group`, `groups`, `snapshot`, `__len__`,
  `__contains__`) takes it. `__iter__` yields over a stable snapshot
  (`iter(list(...))`) so iteration can never race a write. A plain `Lock` suffices
  here — the registry never nests its own lock.

### The tests (`tests/test_concurrency.py`)

Every test releases its threads from a shared `threading.Barrier` for maximum
contention and joins with a **30 s timeout** (a stuck join *is* the deadlock
assertion).

| Test | What it proves |
|------|----------------|
| `…record_from_100_threads_loses_nothing` | 100 threads × 50 = exactly 5,000 updates, `total_seconds` exact |
| `…counts_successes_and_failures_under_contention` | 50 ok + 50 fail threads → exact 2,000 / 2,000 split |
| `…snapshot_is_never_torn_under_writes` | readers assert `tasks_run == tasks_ok + tasks_failed` throughout |
| `…register_from_100_threads_registers_all` | 100 threads → 100 distinct agents, none lost |
| `…duplicate_race_has_exactly_one_winner` | 100 threads, one name → exactly 1 win, 99 `DuplicateAgentError` |
| `…concurrent_reads_during_writes_are_safe` | 4 writers + 4 readers, iterating live → no crash, exactly **200 agents** |
| `…record_is_safe_under_asyncio_to_thread` | 100 `asyncio.to_thread` workers (pytest-asyncio) lose nothing |
| `…register_is_safe_under_asyncio_to_thread` | 100 async-driven registrations all land |
| `…no_deadlock_across_all_registry_methods` | 60 threads mixing every locked method finish in time |
| `…lock_ordering_registry_then_metrics_is_consistent` | `snapshot()` (registry→metrics) vs `record()` → no cycle, no deadlock |
| `…benchmark_metrics_record_lock_overhead` | locked `record` ≈ **1.5 µs** (~658 k ops/s) — overhead negligible |

**Result: 11 concurrency tests pass, 0 errors; 615 tests total, 98.5 % coverage.**
Lock ordering is fixed and one-directional — registry lock → agent metrics lock,
never the reverse — so no cycle can form.

## Meta / contract tests

`test_report_contract.py` keeps the suite honest: it asserts all six required
categories (plus the two bonuses) exist, that each carries **≥ 8 vectors**, that
the total is **≥ 48**, that the `AdversarialReport` tallies correctly, and it pins
a **golden snapshot** of the report's JSON so its shape can never drift.
