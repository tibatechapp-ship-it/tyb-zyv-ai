"""Tests for Phase 5 emotional memory — tone, preferences, satisfaction, auto-adjust."""

from __future__ import annotations

import pytest

from src.memory.base import connect
from src.memory.emotional import EmotionalMemory, Tone, analyze_tone, classify


@pytest.fixture
def emotional():
    conn = connect(":memory:")
    store = EmotionalMemory(conn)
    try:
        yield store
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# tone analysis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "label", "score"),
    [
        ("this is great, shukran", "positive", 1.0),
        ("bad and broken", "negative", -1.0),
        ("just a plain sentence", "neutral", 0.0),
        ("", "neutral", 0.0),
        ("good but also bad", "neutral", 0.0),
        ("not good", "negative", -1.0),
        ("mesh bad", "positive", 1.0),
    ],
)
def test_analyze_tone(text, label, score):
    assert analyze_tone(text) == (label, score)


def test_classify_returns_tone_object():
    tone = classify("awesome work, thank you")
    assert isinstance(tone, Tone)
    assert tone.is_positive and not tone.is_negative


# --------------------------------------------------------------------------- #
# preferences
# --------------------------------------------------------------------------- #
def test_set_and_get_preference(emotional):
    emotional.set_preference("tone", "arabizi")
    assert emotional.preference("tone") == "arabizi"
    assert emotional.preference("missing") is None


def test_preference_update_and_listing(emotional):
    emotional.set_preference("b", "1")
    emotional.set_preference("a", "2", confidence=0.5)
    emotional.set_preference("b", "9")
    assert [r.data["key"] for r in emotional.preferences()] == ["a", "b"]
    assert emotional.preference("b") == "9"
    assert emotional.count() == 2


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #
def test_record_signal_autoclassifies(emotional):
    emotional.record_signal("awesome work", session_id="s1")
    signal = emotional.signals()[0]
    assert signal.data["sentiment"] == "positive"
    assert signal.data["session_id"] == "s1"


def test_record_signal_accepts_explicit_label(emotional):
    emotional.record_signal("??", sentiment="negative", score=-0.5)
    assert emotional.signals()[0].data["sentiment"] == "negative"


def test_observe_logs_and_returns(emotional):
    assert emotional.observe("thanks, perfect") == ("positive", 1.0)
    assert emotional.signals()[0].data["score"] == 1.0


def test_dominant_tone_picks_majority(emotional):
    emotional.observe("bad")
    emotional.observe("broken")
    emotional.observe("great")
    assert emotional.dominant_tone() == "negative"


def test_dominant_tone_empty_is_neutral(emotional):
    assert emotional.dominant_tone() == "neutral"


# --------------------------------------------------------------------------- #
# adjust_reply
# --------------------------------------------------------------------------- #
def test_adjust_reply_softens_negative_default(emotional):
    reply = emotional.adjust_reply("Here is the fix.", user_text="this is broken")
    assert reply.startswith("Sorry about that — ")
    assert reply.endswith("Here is the fix.")


def test_adjust_reply_leaves_positive_and_neutral_alone(emotional):
    assert emotional.adjust_reply("Done!", user_text="great, thanks") == "Done!"
    assert emotional.adjust_reply("Ok.", user_text="please continue") == "Ok."


def test_adjust_reply_accepts_label_string(emotional):
    assert emotional.adjust_reply("x", user_tone="negative").startswith("Sorry")


def test_adjust_reply_accepts_tone_object(emotional):
    assert emotional.adjust_reply("x", user_tone=Tone("negative", -1.0)).startswith("Sorry")


def test_adjust_reply_preference_overrides_prefix(emotional):
    emotional.set_preference("reply_prefix_negative", "Ya 3ammi, ")
    assert emotional.adjust_reply("x", user_tone="negative") == "Ya 3ammi, x"


def test_adjust_reply_preference_adds_suffix(emotional):
    emotional.set_preference("reply_suffix_positive", " — welcome!")
    assert emotional.adjust_reply("Done", user_text="great thanks") == "Done — welcome!"


def test_adjust_reply_without_any_tone_is_unchanged(emotional):
    assert emotional.adjust_reply("plain") == "plain"


# --------------------------------------------------------------------------- #
# satisfaction
# --------------------------------------------------------------------------- #
def test_satisfaction_is_clamped(emotional):
    emotional.record_satisfaction(5.0)
    emotional.record_satisfaction(-3.0)
    scores = sorted(r.data["score"] for r in emotional.satisfaction())
    assert scores == [0.0, 1.0]


def test_latest_and_average_satisfaction(emotional):
    assert emotional.latest_satisfaction() is None
    assert emotional.average_satisfaction() is None
    emotional.record_satisfaction(0.5)
    emotional.record_satisfaction(1.0)
    assert emotional.latest_satisfaction() == 1.0
    assert emotional.average_satisfaction() == pytest.approx(0.75)


def test_all_satisfaction_oldest_first(emotional):
    emotional.record_satisfaction(0.1, timestamp=100.0)
    emotional.record_satisfaction(0.9, timestamp=200.0)
    assert [r.data["score"] for r in emotional.all_satisfaction()] == [0.1, 0.9]


def test_all_signals_oldest_first(emotional):
    emotional.record_signal("bad", timestamp=1.0)
    emotional.record_signal("good", timestamp=2.0)
    assert [r.data["sentiment"] for r in emotional.all_signals()] == ["negative", "positive"]
