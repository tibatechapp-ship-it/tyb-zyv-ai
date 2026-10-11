"""Tests for the ModelRouter — deterministic model selection by complexity."""

from __future__ import annotations

from src.core import (
    DEFAULT_PROFILES,
    ModelProfile,
    ModelRouter,
    ModelTier,
    RoutingDecision,
)


# -- heuristic --------------------------------------------------------------
def test_estimate_complexity_deterministic_and_bounded() -> None:
    router = ModelRouter()
    simple = router.estimate_complexity("say hello")
    complex_ = router.estimate_complexity(
        "Architecture review: the distributed concurrency layer races when the "
        "cache refactors, prove the migration strategy and benchmark it. "
        "```\ndef worker():\n    pass\n```" * 3
    )
    assert 0.0 <= simple <= 1.0
    assert 0.0 <= complex_ <= 1.0
    assert router.estimate_complexity("say hello") == simple  # idempotent


def test_estimate_complexity_blank_task() -> None:
    assert ModelRouter.estimate_complexity("") == 0.0


def test_estimate_complexity_cue_words_push_score_up() -> None:
    assert ModelRouter.estimate_complexity("refactor the architecture") > ModelRouter.estimate_complexity("say hello")


def test_tier_for_cutoffs() -> None:
    assert ModelRouter.tier_for(0.10) is ModelTier.FAST
    assert ModelRouter.tier_for(0.34) is ModelTier.BALANCED
    assert ModelRouter.tier_for(0.66) is ModelTier.BALANCED
    assert ModelRouter.tier_for(0.67) is ModelTier.POWERFUL
    assert ModelRouter.tier_for(1.0) is ModelTier.POWERFUL


# -- routing ----------------------------------------------------------------
def test_route_picks_cheapest_in_computed_tier() -> None:
    router = ModelRouter()
    decision = router.route("refactor the concurrency layer")
    assert isinstance(decision, RoutingDecision)
    assert decision.tier is ModelTier.BALANCED
    assert decision.model.name == "balanced-pro"
    assert "complexity" in decision.reason


def test_route_explicit_tier_override() -> None:
    router = ModelRouter()
    decision = router.route("say hello", tier=ModelTier.POWERFUL)
    assert decision.tier is ModelTier.POWERFUL
    assert decision.model.name == "powerful-max"


def test_route_explicit_complexity_override() -> None:
    router = ModelRouter()
    decision = router.route("anything", complexity=0.9)
    assert decision.tier is ModelTier.POWERFUL
    assert decision.complexity == 0.9


def test_route_strength_filter_and_widening() -> None:
    router = ModelRouter()
    decision = router.route("write some code", strength="coding", tier=ModelTier.POWERFUL)
    assert "coding" in decision.model.strengths
    # a strength nobody in the tier has widens to the whole catalogue
    widened = router.route("write some code", strength="reasoning", tier=ModelTier.FAST)
    assert "reasoning" in widened.model.strengths


def test_route_never_picks_a_model_without_matching_strength_when_possible() -> None:
    router = ModelRouter()
    decision = router.route("x", strength="reasoning")
    assert "reasoning" in decision.model.strengths


def test_route_on_empty_catalogue_falls_back() -> None:
    router = ModelRouter(profiles=())
    decision = router.route("whatever")
    assert decision.model.name == "default"


# -- catalogue --------------------------------------------------------------
def test_register_replaces_by_name() -> None:
    router = ModelRouter()
    router.register(ModelProfile("custom", ModelTier.FAST, cost_per_1k=0.01))
    router.register(ModelProfile("custom", ModelTier.BALANCED, cost_per_1k=0.5))
    assert len(router) == 4
    assert router.profiles(tier=ModelTier.BALANCED)[0].name == "custom"


def test_profiles_sorted_cheapest_first() -> None:
    router = ModelRouter()
    costs = [p.cost_per_1k for p in router.profiles()]
    assert costs == sorted(costs)


def test_default_profiles_readable() -> None:
    assert len(DEFAULT_PROFILES) == 3
    assert {p.tier for p in DEFAULT_PROFILES} == set(ModelTier)


def test_profile_to_dict() -> None:
    data = DEFAULT_PROFILES[0].to_dict()
    assert data["tier"] == "fast"
    assert data["cost_per_1k"] == 0.1
    assert data["strengths"] == sorted(DEFAULT_PROFILES[0].strengths)


def test_routing_decision_to_dict() -> None:
    data = ModelRouter().route("just a quick task").to_dict()
    assert data["model"]["name"]
    assert "tier" in data
    assert "reason" in data