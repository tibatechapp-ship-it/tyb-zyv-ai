"""Tests for src.core.watchdog — the self-healing guard and liveness heartbeat."""

from __future__ import annotations

import logging

import pytest

from src.core.watchdog import Heartbeat, Watchdog


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


# ── Watchdog ─────────────────────────────────────────────────────────────
def test_watchdog_interval_validation() -> None:
    with pytest.raises(ValueError):
        Watchdog(lambda: True, lambda: None, interval=0)


def test_watchdog_healthy_does_not_restart() -> None:
    restarts: list[int] = []
    guard = Watchdog(lambda: True, lambda: restarts.append(1), interval=30.0)
    assert guard.interval == 30.0
    assert guard.tick() is False
    assert guard.checks == 1
    assert guard.restarts == 0
    assert restarts == []
    assert guard.last_check_iso is not None


def test_watchdog_unhealthy_restarts() -> None:
    restarts: list[int] = []
    guard = Watchdog(lambda: False, lambda: restarts.append(1), interval=30.0)
    assert guard.tick() is True
    assert guard.restarts == 1
    assert restarts == [1]


def test_watchdog_check_exception_counts_as_down() -> None:
    def broken() -> bool:
        raise RuntimeError("check exploded")

    guard = Watchdog(broken, lambda: None, interval=30.0)
    assert guard.tick() is True
    assert guard.restarts == 1


def test_watchdog_restart_failure_is_swallowed() -> None:
    def bad_restart() -> None:
        raise OSError("cannot restart")

    guard = Watchdog(lambda: False, bad_restart, interval=30.0)
    assert guard.tick() is False
    assert guard.restarts == 0


def test_watchdog_run_respects_max_cycles() -> None:
    clock = _Clock()
    guard = Watchdog(lambda: True, lambda: None, interval=5.0, clock=clock.now, sleep=clock.sleep)
    assert guard.run(max_cycles=3) == 3
    assert guard.checks == 3


def test_watchdog_run_respects_stop_predicate() -> None:
    calls: list[int] = []

    def should_stop() -> bool:
        calls.append(1)
        return len(calls) > 2

    guard = Watchdog(lambda: True, lambda: None, interval=1.0, sleep=lambda _s: None)
    assert guard.run(should_stop=should_stop) == 2


# ── Heartbeat ────────────────────────────────────────────────────────────
def test_heartbeat_interval_validation() -> None:
    with pytest.raises(ValueError):
        Heartbeat(lambda _m: None, interval=0)


def test_heartbeat_not_due_until_interval_elapses() -> None:
    clock = _Clock()
    sent: list[str] = []
    beat = Heartbeat(sent.append, interval=300.0, message="alive", clock=clock.now, sleep=clock.sleep)
    assert beat.interval == 300.0
    assert beat.due() is False
    assert beat.beat() is False
    assert sent == []
    clock.t = 300.0
    assert beat.due() is True
    assert beat.beat() is True
    assert sent == ["alive"]
    assert beat.beats == 1
    assert beat.last_beat_iso is not None


def test_heartbeat_force_sends_even_when_not_due() -> None:
    sent: list[str] = []
    beat = Heartbeat(sent.append, interval=300.0, message="hi")
    assert beat.beat(force=True) is True
    assert sent == ["hi"]
    assert beat.beats == 1


def test_heartbeat_send_failure_is_swallowed() -> None:
    clock = _Clock()

    def boom(_message: str) -> None:
        raise OSError("telegram down")

    beat = Heartbeat(boom, interval=300.0, clock=clock.now, sleep=clock.sleep)
    clock.t = 1000.0  # a full interval has elapsed since construction
    assert beat.beat() is False
    assert beat.beats == 0


def test_heartbeat_run_respects_max_cycles() -> None:
    clock = _Clock()
    sent: list[str] = []
    beat = Heartbeat(sent.append, interval=10.0, clock=clock.now, sleep=clock.sleep)
    assert beat.run(max_cycles=3) == 3
    # First tick is not due (t=0); the next two are after sleeping 10s each.
    assert beat.beats == 2


def test_heartbeat_run_respects_stop_predicate() -> None:
    checks: list[int] = []

    def should_stop() -> bool:
        checks.append(1)
        return len(checks) > 1

    beat = Heartbeat(lambda _m: None, interval=1.0, sleep=lambda _s: None)
    assert beat.run(should_stop=should_stop) == 1


def test_heartbeat_uses_default_logger_when_none() -> None:
    beat = Heartbeat(lambda _m: None, logger=None)
    assert isinstance(beat._logger, logging.Logger)  # type: ignore[attr-defined]
