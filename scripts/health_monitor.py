#!/usr/bin/env python
"""Live health monitor for the ZYVO Telegram stack.

Runs forever under runit. Every ``ZYVO_WATCHDOG_INTERVAL`` seconds (default 30)
it checks that the bot's runit service is up and that the ZYVO HTTP server the
bot talks to answers on its port; if not, it asks runit to restart the service.
Every ``ZYVO_HEARTBEAT_INTERVAL`` seconds (default 300 = 5 min) it sends a
Telegram "still alive" heartbeat to the owner.

Usage::

    python scripts/health_monitor.py            # run forever (runit service)
    python scripts/health_monitor.py --once      # one check + one heartbeat, exit
    python scripts/health_monitor.py --status    # print health as JSON, exit

All output goes to a daily-rotating JSON log (``ZYVO_MONITOR_LOG``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.logging_utils import setup_json_logging  # noqa: E402
from src.core.types import utc_now_iso  # noqa: E402
from src.core.watchdog import Heartbeat, Watchdog  # noqa: E402
from src.core.zyvo_client import ZyvoClient  # noqa: E402

SERVICE: str = os.environ.get("ZYVO_SERVICE", "zyvo-telegram")
PORT: int = int(os.environ.get("ZYVO_PORT", "4142"))
CHECK_INTERVAL: float = float(os.environ.get("ZYVO_WATCHDOG_INTERVAL", "30"))
HEARTBEAT_INTERVAL: float = float(os.environ.get("ZYVO_HEARTBEAT_INTERVAL", "300"))
LOG_PATH: Path = Path(
    os.environ.get("ZYVO_MONITOR_LOG", str(Path.home() / ".local/state/zyvo/health_monitor.jsonl"))
)
ENV_FILE: Path = Path.home() / ".config/zyvo/telegram.env"


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` env file (missing file -> empty dict)."""
    env: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def service_up() -> bool:
    """True when runit reports the bot service as running."""
    try:
        result = subprocess.run(
            ["sv", "status", SERVICE], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "run:" in result.stdout


def server_up() -> bool:
    """True when the ZYVO HTTP server accepts a TCP connection."""
    return ZyvoClient(port=PORT, timeout=2.0).health(timeout=2.0)


def healthy() -> bool:
    """Both the service and its HTTP server must be reachable."""
    return service_up() and server_up()


def restart_service() -> None:
    """Ask runit to restart the bot (which respawns the ZYVO server)."""
    subprocess.run(["sv", "restart", SERVICE], capture_output=True, text=True, timeout=30, check=False)


def make_sender(token: str, chat_id: str) -> Callable[[str], None]:
    """Build a callable that posts one message to the owner's Telegram chat."""

    def send(text: str) -> None:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with urllib.request.urlopen(url, data=data, timeout=20) as resp:
            resp.read()

    return send


def _status() -> dict[str, object]:
    return {
        "service": SERVICE,
        "port": PORT,
        "service_up": service_up(),
        "server_up": server_up(),
        "checked_at": utc_now_iso(),
    }


def main(argv: list[str]) -> int:
    """Entry point. ``--once``/``--status`` run a single shot for testing."""
    if "--status" in argv:
        print(json.dumps(_status(), ensure_ascii=False, indent=2))
        return 0

    logger = setup_json_logging(LOG_PATH, logger="zyvo.health", also_stderr=True)
    env = load_env_file()
    token = env.get("TELEGRAM_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = env.get("ZYVO_TELEGRAM_CHAT_ID", os.environ.get("ZYVO_TELEGRAM_CHAT_ID", ""))
    send = make_sender(token, chat_id) if token and chat_id else (lambda _text: None)

    heartbeat = Heartbeat(
        send,
        interval=HEARTBEAT_INTERVAL,
        message=(
            f"ZYVO heartbeat — bot alive | service={SERVICE} port={PORT} at {utc_now_iso()}"
        ),
        logger=logger,
    )

    if "--once" in argv:
        healthy_now = healthy()
        logger.info("single health check", extra={**_status(), "healthy": healthy_now})
        heartbeat.beat(force=True)
        print(json.dumps(_status(), ensure_ascii=False))
        return 0 if healthy_now else 1

    watchdog = Watchdog(healthy, restart_service, interval=CHECK_INTERVAL, logger=logger)
    logger.info(
        "health monitor started",
        extra={
            "service": SERVICE,
            "port": PORT,
            "check_interval": CHECK_INTERVAL,
            "heartbeat_interval": HEARTBEAT_INTERVAL,
        },
    )
    heartbeat.beat(force=True)  # announce startup right away
    while True:
        watchdog.tick()
        heartbeat.beat()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
