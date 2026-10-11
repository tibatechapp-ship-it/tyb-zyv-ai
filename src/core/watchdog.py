"""Self-healing process guard + liveness heartbeat.

:class:`Watchdog` runs a health ``check`` on a fixed interval and calls a
``restart`` hook whenever the check reports the service is down.
:class:`Heartbeat` fires a periodic message (e.g. a Telegram "still alive") on
its own interval. Both take an injectable clock/sleeper and a stop predicate, so
they loop forever in production and run deterministically under test.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from src.core.types import utc_now_iso

__all__ = ["Watchdog", "Heartbeat"]


class Watchdog:
    """Restart a service whenever a health check reports it is down."""

    def __init__(
        self,
        check: Callable[[], bool],
        restart: Callable[[], None],
        *,
        interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        self._check = check
        self._restart = restart
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._logger = logger or logging.getLogger("src.core.watchdog")
        self.checks = 0
        self.restarts = 0
        self.last_check_iso: str | None = None

    @property
    def interval(self) -> float:
        return self._interval

    def tick(self) -> bool:
        """Run one check/restart cycle; return True when a restart was issued."""
        self.checks += 1
        self.last_check_iso = utc_now_iso()
        try:
            healthy = bool(self._check())
        except Exception as exc:  # noqa: BLE001 - a broken check means "down"
            self._logger.warning("watchdog check raised", extra={"error": str(exc)})
            healthy = False

        if healthy:
            return False

        self._logger.warning("service unhealthy; restarting")
        try:
            self._restart()
        except Exception as exc:  # noqa: BLE001 - a failed restart must not kill the loop
            self._logger.error("restart failed", extra={"error": str(exc)})
            return False
        self.restarts += 1
        self._logger.info("service restarted", extra={"restarts": self.restarts})
        return True

    def run(
        self,
        *,
        should_stop: Callable[[], bool] | None = None,
        max_cycles: int | None = None,
    ) -> int:
        """Loop :meth:`tick` every interval; stop on predicate/cycle cap.

        Returns the number of cycles executed.
        """
        cycles = 0
        while not (should_stop is not None and should_stop()):
            self.tick()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            self._sleep(self._interval)
        return cycles


class Heartbeat:
    """Send a periodic "I'm alive" signal on its own interval."""

    def __init__(
        self,
        send: Callable[[str], None],
        *,
        interval: float = 300.0,
        message: str = "zyvo heartbeat: still running",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        self._send = send
        self._interval = interval
        self._message = message
        self._clock = clock
        self._sleep = sleep
        self._logger = logger or logging.getLogger("src.core.watchdog")
        self._last = clock()
        self.beats = 0
        self.last_beat_iso: str | None = None

    @property
    def interval(self) -> float:
        return self._interval

    def due(self) -> bool:
        """True when at least one interval has elapsed since the last beat."""
        return self._clock() - self._last >= self._interval

    def beat(self, *, force: bool = False) -> bool:
        """Send one heart-beat if due (or ``force``); return whether sent.

        A failed send is logged and swallowed so the loop keeps running.
        """
        if not force and not self.due():
            return False
        try:
            self._send(self._message)
        except Exception as exc:  # noqa: BLE001 - a failed ping must not stop the loop
            self._logger.error("heartbeat send failed", extra={"error": str(exc)})
            return False
        self._last = self._clock()
        self.beats += 1
        self.last_beat_iso = utc_now_iso()
        self._logger.debug("heartbeat sent", extra={"beats": self.beats})
        return True

    def run(
        self,
        *,
        should_stop: Callable[[], bool] | None = None,
        max_cycles: int | None = None,
    ) -> int:
        """Loop :meth:`beat` every interval; stop on predicate/cycle cap."""
        cycles = 0
        while not (should_stop is not None and should_stop()):
            self.beat()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            self._sleep(self._interval)
        return cycles
