# Security scanner (Phase 9)

`src/security/` is a **fully offline, deterministic, static** security scanner
for Python/JS/config source trees. It never executes anything, never touches the
network, and needs no dependencies beyond the standard library — so it runs on
the phone in Termux exactly as it does in CI.

Eight specialised **agents** each own one category of regular-expression rules.
An **orchestrator** walks a tree and feeds every file to every agent; a
**classifier** deduplicates, scores and judges the findings; a **reporter**
renders markdown, JSON or a small summary dict.

---

## The eight agents

| Agent | Covers | Example rules (severity) |
|-------|--------|--------------------------|
| `secrets` | Hardcoded credentials & keys | `aws-access-key` (high), `github-token` (high), `api-key` (medium), `private-key-block` (high), `plaintext-credential` (medium) |
| `injection` | Code / shell / SQL sinks | `code-eval` (high), `shell-call` (medium), `sql-concatenation` (high), `shell-true` (high), `command-format` (high) |
| `traversal` | Path traversal, unsafe file access | `path-traversal` (high), `absolute-path-open` (medium) |
| `crypto` | Weak / broken cryptography | `weak-hash` (medium), `weak-cipher` (medium), `weak-rng` (low), `ecb-mode` (medium) |
| `deserialization` | Unsafe deserialization | `pickle` (high), `yaml-load` (high), `marshal` (high), `joblib` (medium), `eval-input` (critical) |
| `network` | Transports, endpoints, sockets | `http-url` (info), `ip-literal` (info), `raw-socket` (medium), `http-client` (info) |
| `dependency` | Unpinned dependencies | `unpinned-dependency` (medium) — only runs on `requirements*` / `pyproject.toml` / `Pipfile*` / `poetry.lock` |
| `pii` | Personal data in logs/output | `pii-in-log` (high), `pii-capture` (medium) |

Each `SecurityAgent` is just a `name`, a `description`, a tuple of compiled
`patterns`, and an optional `path_filter(path) -> bool` that scopes it to
certain filenames (that is how the dependency agent only fires on manifests).

```python
import re
from typing import ClassVar

from src.security import SecurityAgent, Severity

class TodoAgent(SecurityAgent):
    name = "todo"
    description = "Leftover markers that must not ship."
    patterns: ClassVar = (
        (re.compile(r"\b(TODO|FIXME|XXX)\b"), "leftover-marker", Severity.LOW),
    )
```

---

## Scanning

```python
from src.security import SecurityScanner, FindingClassifier, SecurityReporter

scanner = SecurityScanner()                     # the canonical 8 agents
classifier = FindingClassifier(top_limit=10)    # dedup + score + verdict
reporter = SecurityReporter()

result = scanner.scan_path("src")               # walks the tree
classification = classifier.classify(result.findings)

print(classification.verdict)                   # RiskLevel.HIGH
print(reporter.render_markdown(result, classification))
print(reporter.render_json(result, classification))
```

- `scan_text(text, path="<string>")` — scan one in-memory blob (dependency agent
  opts out unless `path` looks like a manifest).
- `scan_file(path, max_bytes=2_000_000)` — scan one file; binary or oversized
  files are skipped (return `[]`), never crash.
- `scan_path(path, exclude=…, max_bytes=…)` — walk a tree; `DEFAULT_EXCLUDE`
  skips VCS dirs, virtualenvs, `node_modules`, caches and build output. A
  missing target raises `FileNotFoundError`.

Pass a custom team with `SecurityScanner(agents=[...])` to run a subset.

---

## The verdict ladder

`FindingClassifier` deduplicates by
`(agent, rule, path, line, message)`, counts findings per severity, sums
`risk_score` from the weights, and picks a `RiskLevel`:

| Condition | Verdict |
|-----------|---------|
| any `critical` | `critical` |
| any `high`, or ≥ 3 `medium` | `high` |
| any `medium`, or ≥ 5 `low` | `medium` |
| any `low` or `info` | `low` |
| nothing | `clean` |

Severity weights: `info=0, low=1, medium=2, high=3, critical=4`. The top
findings are ordered by severity, then path, then line.

---

## Command line

The scanner ships a CLI that drops straight into a pre-commit hook or CI:

```bash
# markdown report, fail the build on HIGH or worse (the default)
python -m src.security src

# machine-readable
python -m src.security src --json --top 20

# extra skip directory + change the failure gate
python -m src.security . --exclude fixtures --fail-on critical
```

| Flag | Meaning | Default |
|------|---------|---------|
| `paths…` | files or directories to scan | `.` |
| `--json` | emit JSON instead of markdown | off |
| `--top N` | how many top findings to show | `10` |
| `--exclude NAME` | extra directory name to skip (repeatable) | — |
| `--fail-on LEVEL` | exit non-zero at this verdict or worse (`clean`/`low`/`medium`/`high`/`critical`) | `high` |

Exit code is `0` when the worst verdict is below the gate, `1` otherwise — so
`python -m src.security . && git commit` blocks a commit that introduces a leak.

---

## Design notes

- **Deterministic and offline.** Regex only — no AST execution, no imports of the
  scanned code, no network. The same input always yields the same findings.
- **One finding per match per line**, carrying `agent`, `rule`, `path`, `line`,
  `severity`, `message` and a short `snippet`.
- **Never fatal.** Unreadable, binary or oversized files are skipped silently;
  only a missing scan target raises.
- **Identification, not exploitation.** The rules flag *shapes* (an AWS key, an
  `eval(input())`, a `pickle.loads`) for a human to review; they are deliberately
  conservative to keep false positives low.

Tests: `tests/test_security_scanner.py` (agents, orchestrator, classifier,
reporter) and `tests/test_security_cli.py` (the CLI).

---

## The report front door (Phase 10)

`SecurityOrchestrator` is the one object the TUI (and any caller) holds: pick a
**profile**, scan a file/tree/archive, then classify and score. It ties the bare
`SecurityScanner` together with three small helpers.

```python
from src.security import SecurityOrchestrator

orch = SecurityOrchestrator()
report = orch.scan("app.apk", profile="full")   # SecurityReport
print(report.score.value, report.score.grade)    # 0–100, A–F
print(orch.render_markdown(report, title="Audit"))
```

- **Profiles** (`src/security/profiles.py`) — `PROFILES` / `PROFILE_ORDER` /
  `get_profile(name)`. A profile selects which of the eight agents run, so you
  can scope a scan to `secrets` only or run `full`. Unknown name → `KeyError`.
- **Scoring** (`src/security/scoring.py`) — `compute_score(counts)` subtracts a
  per-severity penalty from 100 (floored at 0) and `grade_for(score)` bands it
  A–F. `SecurityScore.from_counts(...)` is the one-liner used internally.
- **Rule metadata** (`src/security/metadata.py`) — `RULE_METADATA` /
  `metadata_for(rule)` give each rule its CWE, CVSS and remediation text (shown
  in the TUI detail pane); an unknown rule falls back to `DEFAULT_META`.

### Archive scanning is bomb-safe

`scan()` also accepts an `.apk`/`.zip` file: it walks the UTF-8 entries inside.
Reading untrusted archives is the classic decompression-bomb trap, so the reader
is bounded — **every guard runs before anything is decompressed**:

1. an entry's *declared* uncompressed size is read from the ZIP central
   directory and the entry is skipped if it exceeds `_MAX_ENTRY_BYTES` (2 MB)
   — the bomb never reaches `archive.read`;
2. at most `_MAX_ENTRIES` (5 000) entries are inspected;
3. the total decompressed bytes are capped at `_MAX_TOTAL_BYTES` (20 MB);
4. a corrupt, truncated or password-protected entry is skipped, never fatal.

This invariant is enforced by the adversarial DoS suite
(`test_zip_bomb_bytes_are_treated_as_inert_data`) plus two behavioural
regression tests in `tests/test_security_orchestrator.py`.

### In the TUI

Press `ctrl+s` (or type `:security`) to open the **Security** screen. Enter a
target, press `r` to scan, cycle profiles with `f`, search the findings and read
each one's CWE/CVSS/remediation below the table. `e` exports a markdown report —
by default to `~/.local/share/tyb-zyv-ai/reports/`, never into the working tree.

