"""PHASE 1 - contextual-understanding deep tests.

Goal: prove that each of the 20 agents (a) carries a role-specific **bilingual**
persona, (b) actually hands that persona plus the user's task to the backend,
(c) fuses injected context into the user turn without loss, and (d) keeps
per-turn integrity and isolation across multi-turn usage.

All backends are mocks (``tests/deep_helpers.py``) - no real LLM is called.
"""

from __future__ import annotations

import pytest

from src.agents import AGENT_CLASSES
from src.agents.analyst import AnalystAgent
from src.agents.base import BaseAgent
from src.agents.coder import CoderAgent
from src.agents.state import AgentStatus

from deep_helpers import DirectiveBackend, RecordingBackend

#: role keyword (Arabic, English) that MUST appear in each agent's persona.
KEYWORDS: dict[str, tuple[str, str]] = {
    "architect": ("بنية", "architect"),
    "coder": ("كود", "code"),
    "reviewer": ("مراجعة", "review"),
    "tester": ("اختبار", "tests"),
    "debugger": ("تشخيص", "diagnos"),
    "planner": ("تقسيم", "plan"),
    "analyst": ("تحليل", "analys"),
    "researcher": ("مصادر", "source"),
    "designer": ("تصميم", "design"),
    "estimator": ("تقدير", "estimate"),
    "security_auditor": ("ثغرات", "vulnerab"),
    "performance": ("أداء", "performance"),
    "documentation": ("وثائق", "documentation"),
    "refactor": ("هيكلة", "restructure"),
    "compliance": ("تراخيص", "licenc"),
    "orchestrator": ("تنسيق", "coordinat"),
    "devops": ("نشر", "deploy"),
    "monitor": ("مراقبة", "monitor"),
    "recovery": ("استعادة", "restore"),
    "reporter": ("تلخيص", "summaris"),
}


# ── 1.1 System-prompt (persona) test ─────────────────────────────────────
@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.__name__)
def test_system_prompt_carries_bilingual_role_keywords(cls: type[BaseAgent]) -> None:
    """Every agent's persona names its role in both Arabic and English.

    This is the contract that makes an agent *specialised*: if the Arabic or the
    English role keyword disappears, the persona no longer identifies the job.
    """
    arabic, english = KEYWORDS[cls.name]
    prompt = cls.system_prompt
    assert arabic in prompt, f"{cls.name}: persona lost its Arabic role keyword {arabic!r}"
    assert english in prompt.lower(), f"{cls.name}: persona lost its English role keyword {english!r}"
    assert cls.description and cls.description.strip()


def test_keyword_table_covers_every_agent() -> None:
    """The keyword table is exhaustive - a new agent cannot slip through."""
    assert {c.name for c in AGENT_CLASSES} == set(KEYWORDS)


# ── 1.2 Role-response test ───────────────────────────────────────────────
@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.__name__)
def test_persona_and_task_reach_the_backend_and_reply_flows_back(cls: type[BaseAgent]) -> None:
    """The right persona + the user's question reach the model, reply returns.

    A real LLM's semantic relevance cannot be judged offline, so this test pins
    the *framework* contract instead: the backend sees exactly the agent's own
    persona, the task arrives intact, and a role-tagged answer is returned by
    ``run`` with ``ok=True`` and the agent's own name.
    """
    backend = DirectiveBackend()
    agent = cls(backend)
    result = agent.run("how should I proceed?")

    expected_tag = cls.__name__.removesuffix("Agent")
    assert backend.last_signature() == expected_tag
    assert result.ok
    assert result.agent == cls.name
    assert result.output == f"[{expected_tag}-ANSWER] how should I proceed?"


# ── 1.3 Context-injection test ───────────────────────────────────────────
def test_context_is_fused_into_the_user_turn_without_loss() -> None:
    """Inject a complex context and prove it round-trips byte-for-byte.

    The framework must append context to the *user* message (never the persona)
    and must not truncate, normalise or reorder it - even with Arabic, quotes,
    backslashes, newlines and 1 000+ characters.
    """
    task = "Refactor the authentication flow."
    context = (
        "سياق مُعقَّد:\n"
        'notes: "quoted" \\ backslash \\n literal\n'
        "files: src/auth.py:1-120, src/tokens.py\n"
        + ("padding line — رمز عربي\n" * 50)
    )
    backend = RecordingBackend(reply="done")
    result = CoderAgent(backend).run(task, context=context)

    assert result.ok
    system_msg, user_msg = backend.calls[0][0]
    assert system_msg.content == CoderAgent.system_prompt
    assert user_msg.content == f"{task}\n\n--- context ---\n{context}"


def test_context_never_leaks_into_the_system_prompt() -> None:
    """Injected context must stay in the user turn - the persona stays pure."""
    marker = "SECRET-CTX-MARKER-42"
    backend = RecordingBackend()
    AnalystAgent(backend).run("decide", context=marker)

    system_msg, user_msg = backend.calls[0][0]
    assert marker not in system_msg.content
    assert marker in user_msg.content


# ── 1.4 Multi-turn test ──────────────────────────────────────────────────
def test_multiturn_integrity_isolation_and_bookkeeping() -> None:
    """Two turns keep their own task, share one persona, and update state.

    The framework is *stateless* between turns - session memory is the server's
    job - so this test pins the properties that must hold regardless: each turn
    carries only its own task, the persona is identical every turn, metrics
    accumulate, and the live state tracks the latest task.
    """
    backend = RecordingBackend(reply="ok")
    agent = CoderAgent(backend)

    agent.run("compile the module")
    agent.run("run the tests")

    assert backend.attempt_count == 2
    assert backend.calls[0][0][1].content == "compile the module"
    assert backend.calls[1][0][1].content == "run the tests"
    assert backend.calls[0][0][0].content == CoderAgent.system_prompt
    assert backend.calls[1][0][0].content == CoderAgent.system_prompt

    assert agent.metrics.tasks_run == 2
    assert agent.metrics.tasks_ok == 2
    assert agent.state.status is AgentStatus.IDLE
    assert agent.state.last_task == "run the tests"


def test_multiturn_does_not_accumulate_previous_turns() -> None:
    """Turn N must not carry turn N-1's user text - no accidental transcript."""
    backend = RecordingBackend()
    agent = AnalystAgent(backend)
    agent.run("turn one")
    agent.run("turn two")

    # Each call is exactly [system, user]; the older task is absent.
    for messages, _ in backend.calls:
        assert len(messages) == 2
    assert "turn one" not in backend.calls[1][0][1].content


def test_multiturn_error_then_recovery_keeps_context() -> None:
    """A failed turn marks state as error; the next turn recovers to idle."""
    backend = RecordingBackend(reply="ok", failures=1)
    agent = AnalystAgent(backend)

    first = agent.run("will fail")
    assert not first.ok
    assert agent.state.status is AgentStatus.ERROR

    second = agent.run("will succeed", context="recovered")
    assert second.ok
    assert agent.state.status is AgentStatus.IDLE
    assert agent.state.last_error is None
    assert backend.calls[1][0][1].content.endswith("recovered")
