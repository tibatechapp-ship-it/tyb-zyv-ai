# Learning

`src/learning/` is the agent team's learning loop: it watches what agents do
and turns those experiences into behaviour improvements. Phase 1 + 1.5 are the
data layer; later phases add the learning itself.

```
src/learning/
├── __init__.py             # Experience, Outcome, ExperienceCollector, KNOWN_*
└── experience_collector.py # the in-memory buffer, queries, stats, IO, pruning
```

## Experience — one recorded action

An `Experience` captures *who did what and how it ended*:

| Field | Meaning |
|-------|---------|
| `task_id` / `step_id` | which run and step produced it (non-empty, enforced) |
| `agent_role` | canonical role — must be in `KNOWN_AGENT_ROLES` |
| `action_type` | canonical action — must be in `KNOWN_ACTION_TYPES` |
| `outcome` | `Outcome.SUCCESS` / `FAILURE` / `PARTIAL` |
| `action` / `result` | free-text detail |
| `ts` | clock time of the record |

`Outcome` is a `StrEnum`, so `to_dict()` serialises straight to JSON, and
`Experience.from_dict()` rebuilds it (raising `ValueError` on an unknown
outcome).

## Quick start

```python
from src.learning import ExperienceCollector, Outcome

collector = ExperienceCollector()          # clock=time.time by default

collector.record(
    "task-42", "step-1", "coder", "write_file",
    Outcome.FAILURE, action="night build", result="exit 1",
)
```

### Fail-safe recording

`record()` validates everything before touching the buffer and raises
`ValueError` (plus a `logging` warning) when:

* `task_id` / `step_id` is blank;
* `agent_role` is not in `KNOWN_AGENT_ROLES` (20 roles: architect, coder,
  reviewer, tester, debugger, planner, analyst, researcher, designer,
  estimator, security_auditor, performance, documentation, refactor,
  compliance, orchestrator, devops, monitor, recovery, reporter);
* `action_type` is not in `KNOWN_ACTION_TYPES` (10 actions: read_file,
  write_file, edit_file, list_files, search_files, run_command, web_search,
  web_fetch, delegate_task, write_todos).

Only the canonical lowercase forms are accepted.

## Statistics

```python
collector.stats()
# {
#   "total": 100,
#   "success_rate": 0.82, "failure_rate": 0.12,     # floats in [0, 1]
#   "by_agent":  {"coder": 40, "planner": 30, ...}, # Counter.most_common() desc
#   "by_action": {"write_file": 50, "read_file": 30, ...},
#   "by_outcome": {"success": 82, "failure": 12, "partial": 6},
#   "time_range": {"first": ..., "last": ..., "span_seconds": ...},
# }
```

## Queries — all newest first

```python
collector.get_by_agent("coder")                 # one agent's latest
collector.get_by_action("write_file")           # one action type's latest
collector.get_by_outcome(Outcome.FAILURE)       # failures only
collector.search(lambda e: "build" in e.action) # anything else you want
```

Every query takes `limit=50` by default and returns a snapshot list.

## Export / import

```python
collector.export("exp.json")            # JSON list   -> int (written)
collector.import_from("exp.json")       # -> int (added); invalid items skipped
collector.export_to_jsonl("exp.jsonl")  # JSON Lines  -> int (written)
collector.import_from_jsonl("exp.jsonl")# -> int (added); blank/broken lines skipped
```

Malformed items are logged with `logging.warning` and skipped, never fatal.
Missing files raise `FileNotFoundError`.

## Retention

```python
collector.prune(max_age_days=30, max_count=10_000)   # -> int removed
collector.prune_by_outcome(Outcome.FAILURE, max_count=1000)  # keep newest N of a kind
```

## Why in-memory?

The collector is the *working* buffer for one process — cheap and fast. The
long-lived record of episodes already lives in `src.memory` (the episodic
store), so nothing here needs a database.

## Roadmap

- **Phase 1 (done)** — collector core: record / all / len / clear.
- **Phase 1.5 (done)** — queries (get_by_agent / get_by_action / get_by_outcome
  / search), statistics, JSON + JSONL export/import, retention pruning,
  fail-safe `record` with KNOWN_* validators.
- **Phase 2** — turn batches of experiences into rules / adjustments
  (e.g. outcome-weighted statistics per action type).
- **Phase 3** — feed the learned rules back into agent decisions.