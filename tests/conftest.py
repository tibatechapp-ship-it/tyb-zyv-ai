"""Shared pytest fixtures for the tyb-zyv-ai test-suite.

The two assistants are single-file *scripts* whose names contain a hyphen
(``zyvo-telegram.py``), so they cannot be imported with a normal ``import``.
We load them here with :mod:`importlib` and expose them as fixtures.

Both modules are pure standard-library and, at import time, do not touch the
network or spawn processes (only ``os.makedirs`` on a temp work dir), so loading
them is safe and fast.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str) -> types.ModuleType:
    """Import a hyphenated script by path under a valid module name."""
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # runs top-level defs only (__main__ guarded)
    return module


@pytest.fixture(scope="session")
def tg() -> types.ModuleType:
    """The loaded ``zyvo-telegram.py`` module."""
    return _load("zyvo_telegram_under_test", "zyvo-telegram.py")


@pytest.fixture(scope="session")
def voice() -> types.ModuleType:
    """The loaded ``zyvo-voice.py`` module."""
    return _load("zyvo_voice_under_test", "zyvo-voice.py")


@pytest.fixture(autouse=True)
def _reset_module_state(tg: types.ModuleType, voice: types.ModuleType) -> None:
    """Reset mutable module globals before each test for isolation."""
    # telegram
    with tg.PARTS_LOCK:
        tg.PARTS.clear()
    tg.PAUSED = False
    tg.CURRENT_SID = None
    tg.RUNNING_TURN = False
    tg._PROC = None
    # voice
    voice.ACTION = "always"
    voice._PROC = None


class FakePopen:
    """Minimal stand-in for ``subprocess.Popen`` used by the server launcher.

    ``start_new_session=True`` is used by the real code (for killpg), so we
    mimic the small surface the scripts rely on: ``pid``, ``poll``, ``terminate``.
    """

    last: "FakePopen | None" = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = 424242
        self.returncode: int | None = None
        FakePopen.last = self

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode or 0


class FakeResponse:
    """Context-manager stand-in for ``urllib.request.urlopen``'s return value."""

    def __init__(self, body: bytes = b"{}", status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def __iter__(self) -> object:
        # urlopen() on the SSE stream returns an iterable of lines.
        return iter(self._body.splitlines(keepends=True))
