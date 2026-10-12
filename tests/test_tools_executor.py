"""Tests for the framework-side tool executor and the tool-using run loop.

Covers the parser, each read-only tool, workspace confinement, the permission
gate, output clamping, and the bounded ReAct loop (`BaseAgent.run_with_tools`)
driven by a scripted backend — all offline.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from src.agents.coder import CoderAgent
from src.core.types import Message
from src.tools.executor import (
    SAFE_TOOL_NAMES,
    LocalToolExecutor,
    ToolCall,
    ToolOutcome,
    ToolRun,
    parse_tool_calls,
)


class ScriptedBackend:
    """A backend that returns a fixed script of replies, one per call."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
        del model
        self.calls.append(list(messages))
        if not self._replies:
            return "done"
        return self._replies.pop(0)


# ── parser ───────────────────────────────────────────────────────────────
def test_parse_single_call() -> None:
    calls = parse_tool_calls('<tool-call name="read" args=\'{"path": "a.py"}\'/>')
    assert calls == [ToolCall(name="read", arguments={"path": "a.py"})]


def test_parse_multiple_calls_in_order() -> None:
    text = (
        'pre <tool-call name="read" args=\'{"path": "a"}\'/> '
        'mid <tool-call name="list" args=\'{"path": "."}\'/> post'
    )
    assert [call.name for call in parse_tool_calls(text)] == ["read", "list"]


def test_parse_double_quoted_args() -> None:
    calls = parse_tool_calls('<tool-call name="grep" args=\'{"pattern": "x"}\'/>')
    assert calls[0].arguments == {"pattern": "x"}


def test_parse_bad_json_yields_empty_args() -> None:
    calls = parse_tool_calls('<tool-call name="read" args=\'not json\'/>')
    assert calls == [ToolCall(name="read", arguments={})]


def test_parse_no_calls_returns_empty() -> None:
    assert parse_tool_calls("just a plain answer, no tools") == []


# ── read ─────────────────────────────────────────────────────────────────
def test_read_returns_file_contents(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("bonjour\n", encoding="utf-8")
    executor = LocalToolExecutor(root=tmp_path)
    outcome = executor.execute(ToolCall("read", {"path": "hello.txt"}))
    assert outcome.ok and outcome.output == "bonjour\n"


def test_read_missing_file_is_error(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("read", {"path": "nope.txt"}))
    assert not outcome.ok and outcome.error


def test_read_directory_is_error(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("read", {"path": "."}))
    assert not outcome.ok


def test_read_missing_argument_is_error(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("read", {}))
    assert not outcome.ok


def test_read_clamps_large_output(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
    executor = LocalToolExecutor(root=tmp_path, max_output_chars=10)
    outcome = executor.execute(ToolCall("read", {"path": "big.txt"}))
    assert outcome.ok and "truncated" in outcome.output


# ── list ─────────────────────────────────────────────────────────────────
def test_list_marks_dirs_and_sizes(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    output = LocalToolExecutor(root=tmp_path).execute(ToolCall("list", {"path": "."})).output
    assert "sub/" in output and "a.txt  (2 bytes)" in output


def test_list_empty_directory(tmp_path: Path) -> None:
    output = LocalToolExecutor(root=tmp_path).execute(ToolCall("list", {"path": "."})).output
    assert output == "(empty directory)"


def test_list_default_path_is_root(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("list", {}))
    assert outcome.ok and "a.txt" in outcome.output


def test_list_on_file_is_error(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("list", {"path": "a.txt"}))
    assert not outcome.ok


# ── grep ─────────────────────────────────────────────────────────────────
def test_grep_finds_matches_with_lines(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("alpha\nneedle here\nomega\n", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("grep", {"pattern": "needle"}))
    assert outcome.ok and "code.py:2: needle here" in outcome.output


def test_grep_no_matches(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("alpha\n", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("grep", {"pattern": "zzz"}))
    assert outcome.output == "(no matches)"


def test_grep_invalid_regex_is_error(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("grep", {"pattern": "("}))
    assert not outcome.ok


def test_grep_missing_pattern_is_error(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(ToolCall("grep", {}))
    assert not outcome.ok


def test_grep_respects_max_matches(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("\n".join("hit" for _ in range(50)), encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("grep", {"pattern": "hit", "max_matches": 3})
    )
    assert len(outcome.output.splitlines()) == 3


def test_grep_on_single_file(tmp_path: Path) -> None:
    target = tmp_path / "one.py"
    target.write_text("find me\n", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("grep", {"pattern": "find", "path": "one.py"})
    )
    assert outcome.ok and "find me" in outcome.output


def test_grep_skips_dirs_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "note.py").write_text("hit here\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("hit hidden\n", encoding="utf-8")
    (tmp_path / "big.py").write_text("hit" * 400_000, encoding="utf-8")  # > 1 MB
    output = LocalToolExecutor(root=tmp_path).execute(ToolCall("grep", {"pattern": "hit"})).output
    assert "note.py" in output
    assert ".git" not in output and "big.py" not in output


# ── confinement & permission gate ────────────────────────────────────────
def test_read_outside_root_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    outcome = LocalToolExecutor(root=workspace).execute(
        ToolCall("read", {"path": "../secret.txt"})
    )
    assert not outcome.ok and "escapes" in (outcome.error or "")


def test_absolute_path_outside_root_is_refused(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("read", {"path": "/etc/hostname"})
    )
    assert not outcome.ok


def test_absolute_path_inside_root_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "in.txt").write_text("ok", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("read", {"path": str(tmp_path / "in.txt")})
    )
    assert outcome.ok and outcome.output == "ok"


def test_server_side_tool_is_refused(tmp_path: Path) -> None:
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("bash", {"cmd": "rm -rf /"}), allowed_tools=frozenset({"bash"})
    )
    assert not outcome.ok and "server" in (outcome.error or "")


def test_tool_not_permitted_for_agent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    outcome = LocalToolExecutor(root=tmp_path).execute(
        ToolCall("read", {"path": "a.txt"}), allowed_tools=frozenset({"list"})
    )
    assert not outcome.ok and "not permitted" in (outcome.error or "")


def test_available_for_intersects_declared_tools() -> None:
    executor = LocalToolExecutor()
    assert executor.available_for({"read", "bash", "grep"}) == frozenset({"read", "grep"})
    assert executor.available == SAFE_TOOL_NAMES


# ── outcomes ─────────────────────────────────────────────────────────────
def test_outcome_prompt_rendering() -> None:
    ok = ToolOutcome("read", True, output="data")
    bad = ToolOutcome("read", False, error="boom")
    assert "<tool-result name=\"read\">" in ok.as_prompt() and "data" in ok.as_prompt()
    assert "ERROR: boom" in bad.as_prompt()


# ── the run loop ─────────────────────────────────────────────────────────
def test_run_with_tools_executes_and_feeds_back(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("42\n", encoding="utf-8")
    backend = ScriptedBackend(
        [
            '<tool-call name="read" args=\'{"path": "answer.txt"}\'/>',
            "the answer is 42",
        ]
    )
    run = CoderAgent(backend).run_with_tools("read the answer", root=tmp_path)
    assert isinstance(run, ToolRun)
    assert run.result.ok and run.result.output == "the answer is 42"
    assert run.steps == 2
    assert len(run.outcomes) == 1 and run.outcomes[0].output == "42\n"
    # the tool result was fed back as a user turn on the second call
    assert any("<tool-result" in message.content for message in backend.calls[1])


def test_run_with_tools_stops_at_max_steps(tmp_path: Path) -> None:
    call = '<tool-call name="list" args=\'{"path": "."}\'/>'
    backend = ScriptedBackend([call, call, call, call, call])
    run = CoderAgent(backend).run_with_tools("loop forever", root=tmp_path, max_steps=3)
    assert run.steps == 3
    assert len(run.outcomes) == 3


def test_run_with_tools_no_calls_is_single_step(tmp_path: Path) -> None:
    backend = ScriptedBackend(["just an answer"])
    run = CoderAgent(backend).run_with_tools("hello", root=tmp_path)
    assert run.steps == 1 and run.outcomes == () and run.result.output == "just an answer"


def test_run_with_tools_caps_calls_per_step(tmp_path: Path) -> None:
    many = "".join('<tool-call name="list" args=\'{"path": "."}\'/>' for _ in range(10))
    backend = ScriptedBackend([many, "done"])
    run = CoderAgent(backend).run_with_tools("spam", root=tmp_path, max_calls_per_step=2)
    assert len(run.outcomes) == 2


def test_run_with_tools_backend_error_is_captured(tmp_path: Path) -> None:
    class BoomBackend:
        def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
            del messages, model
            raise RuntimeError("backend down")

    run = CoderAgent(BoomBackend()).run_with_tools("x", root=tmp_path)
    assert not run.result.ok and "backend down" in (run.result.error or "")


def test_run_with_tools_refuses_server_tool(tmp_path: Path) -> None:
    backend = ScriptedBackend(
        ['<tool-call name="bash" args=\'{"cmd": "ls"}\'/>', "could not run it"]
    )
    run = CoderAgent(backend).run_with_tools("run ls", root=tmp_path)
    assert run.outcomes[0].ok is False


def test_tool_run_to_dict() -> None:
    from src.core.types import AgentResult

    run = ToolRun(
        result=AgentResult(agent="coder", ok=True, output="hi"),
        outcomes=(ToolOutcome("read", True, output="x"),),
        steps=2,
    )
    payload = run.to_dict()
    assert payload["steps"] == 2
    assert payload["outcomes"][0]["name"] == "read"


def test_execute_never_raises_on_weird_args(tmp_path: Path) -> None:
    executor = LocalToolExecutor(root=tmp_path)
    outcome = executor.execute(ToolCall("read", {"path": 123}))
    assert isinstance(outcome, ToolOutcome) and not outcome.ok


def test_tool_call_to_dict() -> None:
    assert ToolCall("read", {"path": "a"}).to_dict() == {
        "name": "read",
        "arguments": {"path": "a"},
    }


def test_grep_ignores_non_int_max_matches(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text("\n".join("hit" for _ in range(5)), encoding="utf-8")
    executor = LocalToolExecutor(root=tmp_path)
    for bad in (True, "5", 0):
        outcome = executor.execute(ToolCall("grep", {"pattern": "hit", "max_matches": bad}))
        assert outcome.ok and len(outcome.output.splitlines()) == 5  # fell back to default


def test_run_with_tools_injects_lessons(tmp_path: Path) -> None:
    backend = ScriptedBackend(["answer"])
    CoderAgent(backend).run_with_tools("task", lessons="always be terse", root=tmp_path)
    assert "--- lessons ---" in backend.calls[0][-1].content


def test_run_with_tools_non_str_backend_is_captured(tmp_path: Path) -> None:
    class BadBackend:
        def generate(self, messages: Sequence[Message], *, model: str | None = None) -> str:
            del messages, model
            return 123  # type: ignore[return-value]

    run = CoderAgent(BadBackend()).run_with_tools("x", root=tmp_path)
    assert not run.result.ok and "expected str" in (run.result.error or "")


@pytest.mark.parametrize("name", sorted(SAFE_TOOL_NAMES))
def test_every_safe_tool_is_dispatchable(tmp_path: Path, name: str) -> None:
    executor = LocalToolExecutor(root=tmp_path)
    outcome = executor.execute(ToolCall(name, {"path": ".", "pattern": "x"}))
    assert outcome.name == name  # dispatched without raising
