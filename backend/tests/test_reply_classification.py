"""Tests for :mod:`app.services.reply_classification`."""

from __future__ import annotations

import pytest

from app.services.reply_classification import (
    ObjectionType,
    ReplyClassification,
    ReplyIntent,
    classify_by_keywords,
    classify_objection,
    is_hard_opt_out,
    parse_llm_label,
)


def test_empty_reply_is_unclear_with_zero_confidence() -> None:
    """No text means nothing to classify."""
    result = classify_by_keywords("")
    assert result.intent is ReplyIntent.UNCLEAR
    assert result.confidence == 0.0


def test_whitespace_only_reply_is_treated_as_empty() -> None:
    """Whitespace carries no signal either."""
    result = classify_by_keywords("   \n\t  ")
    assert result.intent is ReplyIntent.UNCLEAR
    assert result.confidence == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "Not interested, thanks.",
        "please unsubscribe me",
        "Take me off this list",
        "Do not contact me again",
    ],
)
def test_opt_out_phrases_classify_as_not_interested(text: str) -> None:
    """Opt-out language is caught by the highest-priority rule."""
    result = classify_by_keywords(text)
    assert result.intent is ReplyIntent.NOT_INTERESTED
    assert result.confidence == 0.9


def test_decline_combined_with_other_signal_still_classifies_not_interested() -> None:
    """A decline plus a pricing question is still operationally a decline."""
    result = classify_by_keywords("Not interested, but what would it have cost anyway?")
    assert result.intent is ReplyIntent.NOT_INTERESTED


@pytest.mark.parametrize(
    "text",
    ["Can we book a call this week?", "Give me a call tomorrow", "let's talk on the phone"],
)
def test_call_request_phrases_classify_as_book_call(text: str) -> None:
    """Explicit call requests route to book_call."""
    result = classify_by_keywords(text)
    assert result.intent is ReplyIntent.BOOK_CALL
    assert result.confidence == 0.6


@pytest.mark.parametrize("text", ["How much does this cost?", "What's your pricing look like?"])
def test_pricing_phrases_classify_as_pricing(text: str) -> None:
    """Cost questions without a call request route to pricing."""
    result = classify_by_keywords(text)
    assert result.intent is ReplyIntent.PRICING


@pytest.mark.parametrize("text", ["I'm interested, tell me more", "Sounds good, keen to hear more"])
def test_engagement_phrases_classify_as_interested(text: str) -> None:
    """Soft engagement without pricing or a call ask routes to interested."""
    result = classify_by_keywords(text)
    assert result.intent is ReplyIntent.INTERESTED


def test_unmatched_text_classifies_as_unclear_with_low_confidence() -> None:
    """Text matching no rule needs human review, signaled by low confidence."""
    result = classify_by_keywords("Out of office until next Tuesday.")
    assert result.intent is ReplyIntent.UNCLEAR
    assert result.confidence == 0.3


def test_classification_carries_a_reason() -> None:
    """The matched phrase is surfaced for auditability."""
    result = classify_by_keywords("call me whenever works")
    assert result.reasons
    assert "call me" in result.reasons[0]


def test_classification_rejects_out_of_range_confidence() -> None:
    """The dataclass validates its own invariant."""
    with pytest.raises(ValueError, match=r"outside \[0.0, 1.0\]"):
        ReplyClassification(intent=ReplyIntent.UNCLEAR, confidence=1.5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("book_call", ReplyIntent.BOOK_CALL),
        ("Book Call", ReplyIntent.BOOK_CALL),
        ("BOOK-CALL.", ReplyIntent.BOOK_CALL),
        ("  pricing  ", ReplyIntent.PRICING),
        ("not_interested", ReplyIntent.NOT_INTERESTED),
        ("unclear", ReplyIntent.UNCLEAR),
    ],
)
def test_parse_llm_label_normalizes_common_formats(raw: str, expected: ReplyIntent) -> None:
    """The LLM's output is normalized before matching the enum."""
    assert parse_llm_label(raw) is expected


def test_parse_llm_label_returns_none_for_unparseable_output() -> None:
    """An unrecognized label signals the caller to fall back to keywords."""
    assert parse_llm_label("I am not sure how to categorize this") is None


def test_parse_llm_label_returns_none_for_empty_string() -> None:
    """Empty output is unparseable, not a default category."""
    assert parse_llm_label("") is None


# --- is_hard_opt_out ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "please unsubscribe me",
        "Take me off this list",
        "Do not contact me again",
        "don't contact me anymore",
        "Please stop emailing me",
        "stop contacting our office",
        "remove me from your list",
    ],
)
def test_is_hard_opt_out_recognizes_explicit_compliance_language(text: str) -> None:
    """Explicit stop-contacting language is flagged, regardless of tone."""
    assert is_hard_opt_out(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "not interested, thanks",
        "no thanks, not for us right now",
        "we're not interested at this time",
        "not the right time for us",
    ],
)
def test_is_hard_opt_out_does_not_flag_a_soft_decline(text: str) -> None:
    """A soft decline is not a compliance opt-out -- it must remain draftable."""
    assert is_hard_opt_out(text) is False


# --- classify_objection -------------------------------------------------------


def test_pricing_intent_always_yields_price_objection() -> None:
    """Every PRICING reply is a price objection -- that is what the intent means."""
    result = classify_objection("how much does this cost?", ReplyIntent.PRICING)
    assert result is ObjectionType.PRICE


@pytest.mark.parametrize(
    "text",
    [
        "please unsubscribe me",
        "do not contact me again",
        "stop emailing me, thanks",
        "take me off this list",
    ],
)
def test_hard_opt_out_never_yields_an_objection_type(text: str) -> None:
    """The core compliance guarantee: a hard opt-out is never draftable.

    No caller can generate a suggested response for a reply matching this,
    since classify_objection returns None -- not a fallback objection type
    a caller might use anyway.
    """
    assert classify_objection(text, ReplyIntent.NOT_INTERESTED) is None


@pytest.mark.parametrize(
    "text",
    [
        "not the right time for us",
        "kind of busy right now, can you check back later?",
        "bad timing, maybe next quarter",
        "can you circle back in a few months",
    ],
)
def test_timing_language_yields_timing_objection(text: str) -> None:
    """A soft decline citing timing is sub-classified as a timing objection."""
    assert classify_objection(text, ReplyIntent.NOT_INTERESTED) is ObjectionType.TIMING


@pytest.mark.parametrize(
    "text",
    ["not interested, thanks", "no thanks, this isn't for us", "we'll pass on this"],
)
def test_generic_decline_yields_not_interested_yet(text: str) -> None:
    """A decline that is neither an opt-out nor timing-specific is the general bucket."""
    assert classify_objection(text, ReplyIntent.NOT_INTERESTED) is ObjectionType.NOT_INTERESTED_YET


@pytest.mark.parametrize(
    "intent", [ReplyIntent.BOOK_CALL, ReplyIntent.INTERESTED, ReplyIntent.UNCLEAR]
)
def test_non_objection_intents_never_yield_an_objection_type(intent: ReplyIntent) -> None:
    """Only PRICING and NOT_INTERESTED can ever imply an objection to rebut."""
    assert classify_objection("anything at all", intent) is None


def test_hard_opt_out_takes_precedence_over_timing_language() -> None:
    """A reply combining opt-out language with a timing phrase is still refused.

    Mirrors classify_by_keywords' existing precedence rule for the base
    intent (decline-plus-anything-else classifies as the decline): here,
    opt-out-plus-anything-else must never classify as an objection to draft.
    """
    result = classify_objection(
        "Not a good time, and please stop emailing me.", ReplyIntent.NOT_INTERESTED
    )
    assert result is None
