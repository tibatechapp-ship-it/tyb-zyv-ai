"""System metrics read straight from the kernel — no third-party deps.

``psutil`` cannot be built on Termux/Python 3.14 (needs a C toolchain), so we
read the same numbers from ``/proc`` and ``/sys``. Everything degrades
gracefully to ``None`` when a source is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

__all__ = ["memory", "load_average", "cpu_count", "uptime", "battery", "snapshot"]

_PROC = Path("/proc")
_POWER = Path("/sys/class/power_supply")


def cpu_count() -> int:
    return os.cpu_count() or 1


def load_average() -> tuple[float, float, float] | None:
    """1/5/15-minute load averages, or ``None`` if the OS does not provide them."""
    try:
        return os.getloadavg()
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        return None


def memory() -> dict[str, int | float]:
    """Memory in kB from ``/proc/meminfo`` (empty dict if unavailable)."""
    info: dict[str, int] = {}
    try:
        with (_PROC / "meminfo").open(encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                value = rest.strip().split()[0] if rest.strip() else "0"
                try:
                    info[key.strip()] = int(value)
                except ValueError:  # pragma: no cover - malformed line
                    continue
    except OSError:  # pragma: no cover - platform dependent
        return {}
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(total - available, 0)
    return {
        "total_kb": total,
        "available_kb": available,
        "used_kb": used,
        "used_percent": round(used / total * 100, 1) if total else 0.0,
    }


def uptime() -> float:
    """Seconds since boot (0.0 if unavailable)."""
    try:
        return float((_PROC / "uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):  # pragma: no cover - platform dependent
        return 0.0


def battery() -> dict[str, Any] | None:
    """Battery percentage + status from sysfs, or ``None`` when absent."""
    if not _POWER.is_dir():
        return None
    try:
        supplies = sorted(_POWER.iterdir())
    except OSError:  # pragma: no cover - Android restricts /sys on some devices
        return None
    for supply in supplies:
        capacity = supply / "capacity"
        if not capacity.is_file():
            continue
        try:
            percent = int(capacity.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):  # pragma: no cover - platform dependent
            continue
        status = "unknown"
        status_file = supply / "status"
        if status_file.is_file():
            status = status_file.read_text(encoding="utf-8").strip()
        return {"percent": percent, "status": status, "name": supply.name}
    return None


def snapshot() -> dict[str, Any]:
    """A single dictionary with everything the Monitor view needs."""
    return {
        "cpu_count": cpu_count(),
        "load_average": load_average(),
        "memory": memory(),
        "uptime": uptime(),
        "battery": battery(),
    }
