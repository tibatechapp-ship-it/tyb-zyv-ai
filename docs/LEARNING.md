# Learning

`src/learning/` is the agent team's learning loop: it watches what agents do
and turns those experiences into behaviour improvements. Phase 1 + 1.5 are the
data layer; later phases add the learning itself.

```
src/learning/
├── __init__.py            # Experience, Outcome, ExperienceCollector
└── experience_collector.py# the in-memory buffer + queries
```

## Experience — one recorded action

An `Experience` captures *who did what and how it ended*:

| Field | Meaning |
|-------|---------|
| `agent_role` | which agent (Coder, Planner, ...) |
| `action_type` | kind of action (deploy, review, ...) |
| `outcome` | `Outcome.SUCCESS` / `FAILURE` / `PARTIAL` |
| `action` / `result` | free-text detail |
| `ts` | clock time of the record |

`Outcome` is a `StrEnum`, so `to_dict()` serialises straight to JSON.

## Quick start

```python
from src.learning import ExperienceCollector, Outcome

collector = ExperienceCollector()          # clock=time.time by default

collector.record("Coder", "deploy", Outcome.FAILURE, action="night build")
collector.record("Planner", "plan", Outcome.SUCCESS, action="plan day")

collector.all()                            # chronological, oldest first
collector.stats()                          # {"total": 2, "successes": 1, ...}
```

## Queries (Phase 1.5) — all newest first

```python
collector.get_by_agent("Coder")                 # one agent's latest
collector.get_by_action("deploy")               # one action type's latest
collector.get_by_outcome(Outcome.FAILURE)       # failures only
collector.search(lambda e: "build" in e.action) # anything else you want
```

Every query takes `limit=50` by default and returns a snapshot list, so reading
is safe while other threads keep recording. The collector itself is guarded by
an `RLock` (same discipline as `src.agents` and `src.memory`).

## Why in-memory?

The collector is the *working* buffer for one process — cheap and fast. The
long-lived record of episodes already lives in `src.memory` (the episodic
store), so nothing here needs a database.

## Roadmap

- **Phase 1 (done)** — collector core: record / all / len / clear / stats.
- **Phase 1.5 (done)** — advanced queries: get_by_agent, get_by_action,
  get_by_outcome, search (thread-safe, newest-first, limit).
- **Phase 2** — turn batches of experiences into rules / adjustments
  (e.g. outcome-weighted statistics per action type).
- **Phase 3** — feed the learned rules back into agent decisions.