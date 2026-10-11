"""Blackboard pattern — a shared, typed key/value workspace for agents."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

__all__ = ["Blackboard"]


class Blackboard:
    """A shared workspace agents read from and write to.

    Values are kept in insertion order so the TUI can show a stable history;
    ``append`` accumulates lists under a key without clobbering prior entries.
    """

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def has(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def append(self, key: str, value: Any) -> list[Any]:
        """Append to a list stored under ``key`` (creating it if needed)."""
        bucket = self._data.get(key)
        if not isinstance(bucket, list):
            bucket = []
            self._data[key] = bucket
        bucket.append(value)
        return bucket

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def clear(self) -> None:
        self._data.clear()

    def snapshot(self) -> dict[str, Any]:
        """A shallow copy of the whole board."""
        return dict(self._data)

    def keys(self) -> Iterator[str]:
        return iter(self._data.keys())

    def items(self) -> Iterator[tuple[str, Any]]:
        return iter(self._data.items())
