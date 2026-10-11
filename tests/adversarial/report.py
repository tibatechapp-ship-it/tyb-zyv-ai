"""The adversarial report — a per-vector held/broken ledger for the suite.

The adversarial suite inverts the usual pass/fail meaning: an assertion that the
system resists an attack PASSES when the attack is repelled ("HELD") and FAILS
when the attack succeeds ("BROKEN"). :class:`AdversarialReport` is populated
automatically from pytest outcomes (see ``conftest.py``) and printed as a table
at the end of the run, so every vector carries an explicit verdict.

A BROKEN vector is not a test bug: it is a discovered weakness. The project's
policy is to harden the framework and re-run until every vector HELDs, or to
document the vector as out-of-scope in ``ADVERSARIAL.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["AdversarialOutcome", "AdversarialReport"]


@dataclass(slots=True)
class AdversarialOutcome:
    """The verdict for one attack vector."""

    category: str
    vector: str
    held: bool
    detail: str = ""

    @property
    def verdict(self) -> str:
        """``"HELD"`` when the system resisted, ``"BROKEN"`` when it did not."""
        return "HELD" if self.held else "BROKEN"


@dataclass
class AdversarialReport:
    """Collects one :class:`AdversarialOutcome` per attack vector."""

    outcomes: list[AdversarialOutcome] = field(default_factory=list)

    def record(self, category: str, vector: str, *, held: bool, detail: str = "") -> None:
        """Add one vector's verdict (called from the pytest hook)."""
        self.outcomes.append(AdversarialOutcome(category, vector, held, detail))

    @property
    def total(self) -> int:
        """How many vectors were exercised."""
        return len(self.outcomes)

    @property
    def held_count(self) -> int:
        """How many attacks the system repelled."""
        return sum(1 for outcome in self.outcomes if outcome.held)

    @property
    def broken_count(self) -> int:
        """How many attacks broke through."""
        return sum(1 for outcome in self.outcomes if not outcome.held)

    @property
    def resilience(self) -> float:
        """Fraction of vectors held (1.0 == nothing broke through)."""
        return self.held_count / self.total if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        """Map each category to ``(held, broken)`` counts."""
        buckets: dict[str, list[int]] = {}
        for outcome in self.outcomes:
            bucket = buckets.setdefault(outcome.category, [0, 0])
            bucket[0 if outcome.held else 1] += 1
        return {name: (pair[0], pair[1]) for name, pair in buckets.items()}

    def verdict_for(self, vector: str) -> str:
        """``"HELD"`` / ``"BROKEN"`` / ``"UNKNOWN"`` for a named vector."""
        for outcome in self.outcomes:
            if outcome.vector == vector:
                return outcome.verdict
        return "UNKNOWN"

    def summary(self) -> str:
        """A compact, terminal-friendly table of the whole run."""
        lines = ["", "=" * 64, "ADVERSARIAL REPORT", "=" * 64]
        for name, (held, broken) in sorted(self.by_category().items()):
            lines.append(f"  {name:<24} held {held:>3}   broken {broken:>3}")
        lines.append("-" * 64)
        lines.append(
            f"  TOTAL {self.total:>3}   held {self.held_count:>3}   "
            f"broken {self.broken_count:>3}   resilience {self.resilience:.0%}"
        )
        lines.append("=" * 64)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        """A JSON-serialisable snapshot (used by the golden report test)."""
        return {
            "total": self.total,
            "held": self.held_count,
            "broken": self.broken_count,
            "resilience": round(self.resilience, 4),
            "by_category": {name: list(pair) for name, pair in self.by_category().items()},
            "outcomes": [
                {
                    "category": outcome.category,
                    "vector": outcome.vector,
                    "verdict": outcome.verdict,
                    "detail": outcome.detail,
                }
                for outcome in self.outcomes
            ],
        }
