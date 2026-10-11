"""Reusable mock backends for the deep test-suite (phases 1-5).

Everything here is standard-library only. No backend in this module ever talks
to a real LLM: they record, shape, delay, fail or block on demand so the whole
framework can be driven deterministically.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Sequence

from src.core.types import Message

#: Matches the persona preamble every agent ships, e.g. "You are the Coder agent".
AGENT_WORD = re.compile(r"You are the (\w+) agent")

__all__ = [
    "AGENT_WORD",
    "BlockingBackend",
    "DirectiveBackend",
    "ModelBackend",
    "RecordingBackend",
]


class DirectiveBackend:
    """Mime a role-shaped reply, keyed off the persona in the system message.

    Proves which persona actually reached the backend: the ``<Name>`` word is
    parsed from the leading system prompt and echoed back in the reply tag.
    ``fail_for`` makes the named agents (e.g. ``["Reviewer"]``) raise instead,
    which is how the collaboration tests inject a mid-chain failure.
    """

    def __init__(self, fail_for: Sequence[str] = ()) -> None:
        self.fail_for = set(fail_for)
        self.signatures: list[str] = []
        self.calls: list[tuple[list[Message], str | None]] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.calls.append((list(messages), model))
        system = messages[0].content if messages else ""
        match = AGENT_WORD.search(system)
        name = match.group(1) if match else "Unknown"
        self.signatures.append(name)
        if name in self.fail_for:
            raise RuntimeError(f"{name} failed by script")
        user = messages[-1].content if messages else ""
        return f"[{name}-ANSWER] {user}"

    def last_signature(self) -> str | None:
        """The agent name parsed from the most recent call."""
        return self.signatures[-1] if self.signatures else None


class RecordingBackend:
    """Records every call; optionally fails ``failures`` times, then replies.

    Used both as a spy (assert on the exact messages sent) and as a flaky/atomic
    backend for retry and fault-injection tests.
    """

    def __init__(self, reply: str = "ok", *, failures: int = 0, delay: float = 0.0) -> None:
        self.reply = reply
        self.failures = failures
        self.delay = delay
        self.calls: list[tuple[list[Message], str | None]] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.calls.append((list(messages), model))
        if self.delay:
            time.sleep(self.delay)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("scripted failure")
        return self.reply

    @property
    def attempt_count(self) -> int:
        """How many times ``generate`` was entered."""
        return len(self.calls)

    def last_user_message(self) -> str:
        """The content of the final message of the most recent call."""
        messages, _ = self.calls[-1]
        return messages[-1].content


class ModelBackend:
    """Succeeds only for the listed models — drives fallback tests.

    Every candidate model is recorded in :attr:`tried`, in call order, so a test
    can assert the exact fallback chain that was walked.
    """

    def __init__(self, ok_models: Sequence[str | None]) -> None:
        self.ok_models = set(ok_models)
        self.tried: list[str | None] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.tried.append(model)
        if model not in self.ok_models:
            raise RuntimeError(f"model {model!r} unavailable")
        return f"reply-from-{model}"


class BlockingBackend:
    """Blocks inside ``generate`` until released — for timeout/cancel tests.

    ``entered`` is set the moment a call starts; ``release`` unblocks it. This
    lets a test cancel a long call externally and then cleanly wind it down.
    """

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        self.entered.set()
        self.release.wait(timeout=30)
        return "after unblock"
