"""Model router — pick the right model for a task and its complexity.

Not every task deserves the biggest (slowest, priciest) model. The
:class:`ModelRouter` estimates how complex a task is with a fast, deterministic
heuristic, maps that to a :class:`ModelTier`, and returns the cheapest
:class:`ModelProfile` in that tier — optionally constrained by a required
*strength* ("coding", "reasoning", …).

Everything is pure and offline: no network, no model calls, fully testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DEFAULT_PROFILES",
    "ModelProfile",
    "ModelRouter",
    "ModelTier",
    "RoutingDecision",
]

_WORD = re.compile(r"[a-z0-9]+")

#: Cue words and how much each adds to the complexity heuristic.
_COMPLEXITY_CUES: dict[str, float] = {
    "architecture": 0.25,
    "concurrency": 0.25,
    "distributed": 0.25,
    "refactor": 0.2,
    "migrate": 0.2,
    "security": 0.2,
    "prove": 0.2,
    "race": 0.2,
    "bug": 0.15,
    "optimi": 0.15,
    "design": 0.15,
    "crash": 0.15,
    "research": 0.15,
    "analy": 0.1,
    "benchmark": 0.1,
}


class ModelTier(StrEnum):
    """The capability/cost band a model belongs to."""

    FAST = "fast"
    BALANCED = "balanced"
    POWERFUL = "powerful"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A model's routing metadata."""

    name: str
    tier: ModelTier
    context_window: int = 8192
    cost_per_1k: float = 0.0
    strengths: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this profile."""
        return {
            "name": self.name,
            "tier": self.tier.value,
            "context_window": self.context_window,
            "cost_per_1k": self.cost_per_1k,
            "strengths": sorted(self.strengths),
        }


#: A sensible default catalogue (names are illustrative placeholders).
DEFAULT_PROFILES: tuple[ModelProfile, ...] = (
    ModelProfile("fast-mini", ModelTier.FAST, 8192, 0.1, frozenset({"quick", "classify"})),
    ModelProfile(
        "balanced-pro", ModelTier.BALANCED, 32768, 1.0, frozenset({"coding", "analysis"})
    ),
    ModelProfile(
        "powerful-max",
        ModelTier.POWERFUL,
        128000,
        5.0,
        frozenset({"reasoning", "long-context", "coding"}),
    ),
)

#: Complexity → tier cut-offs (lower bound inclusive).
_TIER_CUTOFFS: tuple[tuple[float, ModelTier], ...] = (
    (0.67, ModelTier.POWERFUL),
    (0.34, ModelTier.BALANCED),
)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The full outcome of a routing call (model + why)."""

    model: ModelProfile
    complexity: float
    tier: ModelTier
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this decision."""
        return {
            "model": self.model.to_dict(),
            "complexity": self.complexity,
            "tier": self.tier.value,
            "reason": self.reason,
        }


class ModelRouter:
    """Routes tasks to a model by estimated complexity and required strengths."""

    def __init__(
        self,
        profiles: tuple[ModelProfile, ...] | None = None,
        *,
        default_tier: ModelTier = ModelTier.BALANCED,
    ) -> None:
        self._profiles: list[ModelProfile] = list(
            DEFAULT_PROFILES if profiles is None else profiles
        )
        self._default_tier = default_tier

    # -- catalogue ----------------------------------------------------------
    def register(self, profile: ModelProfile) -> None:
        """Add or replace a profile (matched by name)."""
        self._profiles = [p for p in self._profiles if p.name != profile.name]
        self._profiles.append(profile)

    def profiles(self, *, tier: ModelTier | None = None) -> list[ModelProfile]:
        """Every profile (optionally for one tier), cheapest first."""
        selected = [p for p in self._profiles if tier is None or p.tier == tier]
        return sorted(selected, key=lambda p: p.cost_per_1k)

    def __len__(self) -> int:
        return len(self._profiles)

    # -- heuristic ----------------------------------------------------------
    @staticmethod
    def estimate_complexity(task: str) -> float:
        """A deterministic 0..1 complexity estimate for ``task``.

        Blends task length, cue words (architecture/refactor/security/…), the
        number of questions and whether code is present. Pure and idempotent.
        """
        text = task.lower()
        length_score = min(len(task) / 2000.0, 1.0)
        cue_score = min(sum(weight for cue, weight in _COMPLEXITY_CUES.items() if cue in text), 0.6)
        question_score = min(task.count("?") * 0.05, 0.1)
        code_score = 0.15 if ("```" in task or "def " in text) else 0.0
        raw = 0.3 * length_score + cue_score + question_score + code_score
        return round(min(1.0, raw), 3)

    @staticmethod
    def tier_for(complexity: float) -> ModelTier:
        """Map a complexity score to a :class:`ModelTier`."""
        for cutoff, tier in _TIER_CUTOFFS:
            if complexity >= cutoff:
                return tier
        return ModelTier.FAST

    # -- routing ------------------------------------------------------------
    def route(
        self,
        task: str,
        *,
        complexity: float | None = None,
        tier: ModelTier | None = None,
        strength: str | None = None,
    ) -> RoutingDecision:
        """Choose a model for ``task``.

        An explicit ``complexity`` or ``tier`` overrides the heuristic. A
        ``strength`` (e.g. ``"coding"``) narrows the candidates; if nothing in
        the tier matches, the search widens to the whole catalogue before
        falling back to any model at all.
        """
        if complexity is None:
            complexity = self.estimate_complexity(task)
        if tier is None:
            tier = self.tier_for(complexity)
        model = self._pick(tier, strength)
        strength_note = f", strength={strength}" if strength else ""
        reason = (
            f"complexity {complexity:.2f} -> {tier.value} tier{strength_note}; "
            f"picked cheapest ({model.name})"
        )
        return RoutingDecision(model=model, complexity=complexity, tier=tier, reason=reason)

    def _pick(self, tier: ModelTier, strength: str | None) -> ModelProfile:
        for candidates in (
            self.profiles(tier=tier),
            self.profiles(),
        ):
            matching = [p for p in candidates if strength is None or strength in p.strengths]
            if matching:
                return matching[0]
        # Empty catalogue (all profiles removed): synthesise a safe default.
        return ModelProfile("default", self._default_tier)
