"""Structured JSON logging with daily rotation (standard library only).

The server emits one JSON object per line so logs can be consumed by ``jq``,
piped into the TUI's Logs view, or shipped anywhere without a parser. Rotation
is by calendar day (``TimedRotatingFileHandler(when="midnight")``) keeping a
configurable number of days of history.

Any keyword passed via ``logging``'s ``extra=`` is merged into the JSON object,
which is how the reliability layer records fields such as ``model``, ``error``
and ``restarts``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["JsonFormatter", "setup_json_logging"]

# Keys every LogRecord already carries; anything else is a caller-supplied extra.
_RESERVED: frozenset[str] = frozenset(
    logging.LogRecord("", logging.NOTSET, "", 0, "", None, None).__dict__
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single JSON line."""

    def __init__(self, *, include_extras: bool = True) -> None:
        super().__init__()
        self._include_extras = include_extras

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as compact JSON (one line, UTF-8 friendly)."""
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self._include_extras:
            for key, value in record.__dict__.items():
                if key not in _RESERVED and not key.startswith("_"):
                    payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_json_logging(
    path: str | Path,
    *,
    level: int = logging.INFO,
    logger: str | None = None,
    backup_count: int = 7,
    also_stderr: bool = False,
) -> logging.Logger:
    """Configure a logger that writes JSON lines to ``path``, rotating daily.

    ``logger=None`` configures the root logger. ``backup_count`` is how many
    previous days of logs to keep (``0`` keeps none). Existing handlers on the
    target logger are replaced so the call is idempotent.
    """
    target = logging.getLogger(logger)
    target.setLevel(level)
    target.handlers.clear()
    target.propagate = False

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        dest, when="midnight", backupCount=backup_count, encoding="utf-8", utc=True
    )
    handler.setFormatter(JsonFormatter())
    target.addHandler(handler)

    if also_stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        target.addHandler(stream)

    return target
