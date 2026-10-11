"""Category 6 — Comprehension attacks.

Inputs that are hard to *understand*: ambiguous, contradictory, paradoxical,
nonsensical, sarcastic, code-switched, typo-ridden or missing context. The
framework makes no claim about a model's comprehension; its guarantee is
structural — every such input is delivered **verbatim and exactly once**, never
rewritten, never interpreted as control flow, never corrupting non-Latin text.
"""

from __future__ import annotations

import pytest

from src.agents.coder import CoderAgent
from src.core.types import AgentResult, Message

from deep_helpers import RecordingBackend

CATEGORY = "6-comprehension"


def _deliver(payload: str) -> tuple[str, AgentResult]:
    """Run ``payload`` through the coder and assert it was delivered verbatim."""
    backend = RecordingBackend(reply="ok")
    result = CoderAgent(backend).run(payload)
    assert result.ok
    assert backend.attempt_count == 1
    return backend.last_user_message(), result


@pytest.mark.adversarial
def test_ambiguous_task_is_delivered_verbatim() -> None:
    """A vague task reaches the model untouched — the framework never guesses.

    Scenario: 'do the thing ... the usual'. Ambiguity must be the model's problem,
    not silently resolved or rewritten by the framework.
    """
    payload = "Do the thing. You know, the usual — just make it work somehow."
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_contradiction_is_passed_through_unchanged() -> None:
    """A self-contradicting instruction is delivered as-is.

    Scenario: 'write it. do not write it.' The framework neither picks a side nor
    rejects the input; it stays a single, unmodified user turn.
    """
    payload = "Write the report. Do not write the report. Do both at once."
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_paradox_is_not_evaluated() -> None:
    """A logical paradox is inert data, not something the framework resolves.

    Scenario: 'this sentence is false'. There is no evaluator to hang on it; the
    string is delivered once and the run completes.
    """
    payload = "This sentence is false. If it is true then it is false, so fix the bug."
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_non_sequitur_is_inert() -> None:
    """A chain of non-sequiturs cannot turn into control flow.

    Scenario: unrelated nonsense ('the moon is cheese therefore patch the
    router'). It stays text — no command, no tool call is synthesised here.
    """
    payload = "The moon is cheese; therefore the invoice is blue, so patch the router."
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_sarcasm_is_treated_as_plain_text() -> None:
    """Sarcasm carries no special meaning to the framework.

    Scenario: 'oh GREAT, another bug, wonderful'. It is delivered as bytes like
    any other input; no sentiment is acted upon.
    """
    payload = "Oh great, ANOTHER bug. Wonderful. Please fix it immediately, obviously."
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_code_switching_preserves_arabic_exactly() -> None:
    """Arabic + English in one task must survive byte-for-byte (no mojibake).

    Scenario: a bilingual instruction (Arabic words inside an English sentence).
    The framework must not normalise, reshape or corrupt the non-Latin codepoints
    — critical for a user whose language is Arabic.
    """
    payload = "Fix this bug من فضلك، ثم اعمل commit و push للتغييرات."
    received, result = _deliver(payload)

    assert received == payload
    assert [ord(ch) for ch in received] == [ord(ch) for ch in payload]
    assert received.encode("utf-8").decode("utf-8") == payload
    assert Message("user", payload).as_dict()["content"] == payload
    assert result.output == "ok"


@pytest.mark.adversarial
def test_typos_do_not_change_the_payload() -> None:
    """A typo-ridden task is not auto-corrected by the framework.

    Scenario: 'plesae fx the dangr ...'. Silently rewriting user text could change
    intent; the framework leaves the bytes alone.
    """
    payload = "plesae fx the dangr abt the null poiner excepion in prodction"
    received, _ = _deliver(payload)
    assert received == payload


@pytest.mark.adversarial
def test_missing_context_is_handled() -> None:
    """An empty/missing context leaves no dangling marker in the prompt.

    Scenario: 'complete this' with no context. The builder must not append an
    empty ``--- context ---`` block (which would confuse the model) and the task
    must be delivered intact.
    """
    payload = "Complete this."
    empty, _ = _deliver(payload)  # context defaults to ""

    backend = RecordingBackend(reply="ok")
    result = CoderAgent(backend).run(payload, context="")
    messages, _ = backend.calls[-1]

    assert result.ok
    assert empty == payload
    assert messages[-1].content == payload
    assert "--- context ---" not in messages[-1].content
