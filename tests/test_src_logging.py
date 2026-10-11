"""Tests for src.core.logging_utils — JSON formatting and daily-rotating setup."""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from src.core.logging_utils import JsonFormatter, setup_json_logging


def _record(msg: str = "hello", args: object = None, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord("demo", logging.INFO, __file__, 1, msg, args, exc_info)  # type: ignore[arg-type]


def test_json_formatter_basic_fields() -> None:
    data = json.loads(JsonFormatter().format(_record("hi %s", ("there",))))
    assert data["message"] == "hi there"
    assert data["level"] == "INFO"
    assert data["logger"] == "demo"
    assert "time" in data


def test_json_formatter_merges_extras() -> None:
    record = _record()
    record.__dict__["model"] = "big-pickle"
    record.__dict__["count"] = 3
    data = json.loads(JsonFormatter().format(record))
    assert data["model"] == "big-pickle"
    assert data["count"] == 3


def test_json_formatter_can_drop_extras() -> None:
    record = _record()
    record.__dict__["model"] = "x"
    data = json.loads(JsonFormatter(include_extras=False).format(record))
    assert "model" not in data


def test_json_formatter_renders_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record(exc_info=sys.exc_info())
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError" in data["exc_info"]


def test_setup_json_logging_writes_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "app.jsonl"
    logger = setup_json_logging(path, logger="logtest.write")
    try:
        logger.info("started", extra={"port": 4142})
        for handler in logger.handlers:
            handler.flush()
        line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
        data = json.loads(line)
        assert data["message"] == "started"
        assert data["port"] == 4142
        assert logger.propagate is False
        assert any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers)
    finally:
        logger.handlers.clear()


def test_setup_json_logging_is_idempotent_and_optional_stderr(tmp_path: Path) -> None:
    path = tmp_path / "app.jsonl"
    logger = setup_json_logging(path, logger="logtest.idem", also_stderr=True)
    try:
        assert len(logger.handlers) == 2
        setup_json_logging(path, logger="logtest.idem")
        assert len(logger.handlers) == 1
    finally:
        logger.handlers.clear()


def test_setup_json_logging_configures_root(tmp_path: Path) -> None:
    root = logging.getLogger(None)
    original = list(root.handlers)
    original_propagate = root.propagate
    try:
        configured = setup_json_logging(tmp_path / "root.jsonl")
        assert configured is root
    finally:
        root.handlers.clear()
        root.handlers.extend(original)
        root.propagate = original_propagate
