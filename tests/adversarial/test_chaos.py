"""Bonus category 8 — Chaos engineering.

Injects genuinely random (but seeded) faults — exceptions, empty answers,
delays — into the backend and asserts the framework's *containment contract*:
fan-outs and pipelines always return well-formed ``AgentResult`` objects, never
raise, and keep their accounting consistent (``ok`` ⟺ ``error is None``), and
retried chaos stays bounded. Seeds are fixed for reproducibility.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from src.agents.coder import CoderAgent
from src.core.resilience import RetryPolicy, retry_call
from src.core.types import AgentResult, Message
from src.patterns.fanout import Fanout
from src.patterns.pipeline import Pipeline

CATEGORY = "8-chaos"


class ChaosBackend:
    """A backend that fails, stalls or answers emptily at random."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.calls = 0

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.calls += 1
        roll = self.rng.random()
        if roll < 0.25:
            raise RuntimeError("chaos: transient failure")
        if roll < 0.40:
            return ""
        return "ok:" + messages[-1].content


def _assert_wellformed(result: AgentResult) -> None:
    assert isinstance(result, AgentResult)
    assert isinstance(result.ok, bool)
    assert isinstance(result.output, str)
    if result.ok:
        assert result.error is None
    else:
        assert result.error


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_chaos_fanout_stays_wellformed(seed: int) -> None:
    """Twelve chaotic agents fan out; every result is well-formed and accounted."""
    agents = [CoderAgent(ChaosBackend(random.Random(seed * 100 + i))) for i in range(12)]
    results = Fanout(agents, max_workers=4).run("work")

    assert len(results) == 12
    for result in results:
        _assert_wellformed(result)


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [10, 11, 12])
def test_chaos_pipeline_returns_one_result(seed: int) -> None:
    """A chaotic pipeline yields exactly one well-formed result, never a raise."""
    pipeline = Pipeline(
        [CoderAgent(ChaosBackend(random.Random(seed + i))) for i in range(4)]
    )
    result = pipeline.run("do the thing")
    _assert_wellformed(result)


@pytest.mark.adversarial
@pytest.mark.parametrize("seed", [20, 21, 22])
def test_chaos_with_retry_stays_bounded(seed: int) -> None:
    """Wrapping chaos in retry either succeeds or fails — within the attempt cap."""
    backend = ChaosBackend(random.Random(seed))
    policy = RetryPolicy(max_attempts=4, base_delay=0.0)

    try:
        output = retry_call(
            lambda: backend.generate([Message("user", "x")]),
            policy,
            sleep=lambda _seconds: None,
        )
    except RuntimeError:
        pass
    else:
        assert isinstance(output, str)

    assert backend.calls <= 4
