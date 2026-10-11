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

## Recommendations (hardening — not currently exploited)

- **Lock the mutable singletons.** `AgentMetrics.record` (`tasks_run += 1`) and
  `AgentRegistry.register` (check-then-act) are not lock-protected. They are safe
  under today's CPython GIL (every concurrency vector HELDs), but a free-threaded
  build or a different interpreter could lose updates or race-register. A
  `threading.Lock` around both is cheap insurance.
- **Document the single-writer contract.** `CommandQueue`, `ResponseCache` and
  `AgentCommunication` are single-writer by design; keep that explicit in docstrings
  so callers don't assume they are thread-safe.
- **Keep the limiter fail-closed.** The backwards-clock behaviour (refuse rather
  than over-admit) is correct; guard it with the existing clock-skew vector when
  the limiter changes.
- **CI wiring.** Add `python -m pytest tests/adversarial/` to CI so any new BROKEN
  vector fails the build (inverted-meaning tests still exit non-zero on failure,
  so the existing coverage gate already catches regressions).

## Meta / contract tests

`test_report_contract.py` keeps the suite honest: it asserts all six required
categories (plus the two bonuses) exist, that each carries **≥ 8 vectors**, that
the total is **≥ 48**, that the `AdversarialReport` tallies correctly, and it pins
a **golden snapshot** of the report's JSON so its shape can never drift.
