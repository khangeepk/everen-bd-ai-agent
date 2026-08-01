"""Reply intent classification: category model and a deterministic fallback.

Standard library only. The LLM-calling wrapper lives in
:mod:`app.agents.reply_classifier`; this module holds the keyword-based
classifier used when the LLM is unavailable, and the label-parsing logic
shared by both paths, so classification quality can be tested without a
network call.

The fallback is intentionally conservative: it is a keyword matcher, not a
model, so its accuracy is lower than the LLM's. It exists so an LLM outage
degrades to "still routes correctly on unambiguous replies" rather than
"blocks every inbound message from being processed."
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ObjectionType(str, enum.Enum):
    """The kind of objection a reply raises, when it raises one at all.

    A second, additive classification layered on top of :class:`ReplyIntent`
    rather than a new top-level intent -- adding a value to ``ReplyIntent``
    would change the LLM's category contract (see
    :mod:`app.agents.reply_classifier`'s system prompt) and the pipeline
    stage mapping (:mod:`app.services.pipeline`), neither of which this
    feature needs to touch. Objection type only decides which rebuttal angle
    :func:`app.agents.outreach.OutreachDraftAgent.generate_objection_response`
    writes; it never affects pipeline routing.
    """

    PRICE = "price"
    TIMING = "timing"
    NOT_INTERESTED_YET = "not_interested_yet"


class ReplyIntent(str, enum.Enum):
    """What an inbound reply is asking for."""

    BOOK_CALL = "book_call"
    INTERESTED = "interested"
    PRICING = "pricing"
    NOT_INTERESTED = "not_interested"
    #: The reply does not clearly fit another category -- an out-of-office
    #: auto-reply, a wrong-person response, or something ambiguous. Routed to
    #: a human rather than guessed at.
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class ReplyClassification:
    """The result of classifying one inbound reply.

    Attributes:
        intent: The classified intent.
        confidence: How confident the classifier is, in ``[0.0, 1.0]``.
        reasons: Short evidence for the classification, e.g. the matched
            keyword or the LLM's stated rationale.
        used_fallback: True when the deterministic keyword classifier was
            used because the LLM was unavailable.
    """

    intent: ReplyIntent
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    used_fallback: bool = False

    def __post_init__(self) -> None:
        """Validate the confidence score is normalized.

        Raises:
            ValueError: If ``confidence`` is outside ``[0.0, 1.0]``.
        """
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside [0.0, 1.0]")


#: Checked in this order -- opt-outs first (compliance-sensitive, must not be
#: missed), then the strongest buying signal, then softer signals. A reply
#: matching multiple categories is classified by whichever is checked first,
#: e.g. "not interested but what would it cost anyway" classifies as
#: NOT_INTERESTED, correctly treating the decline as the operative signal.
_KEYWORD_RULES: tuple[tuple[ReplyIntent, tuple[str, ...]], ...] = (
    (
        ReplyIntent.NOT_INTERESTED,
        (
            "not interested",
            "no longer interested",
            "unsubscribe",
            "remove me",
            "stop contacting",
            "stop emailing",
            "please stop",
            "do not contact",
            "don't contact",
            "take me off",
            "no thanks",
            "not right now and please stop",
        ),
    ),
    (
        ReplyIntent.BOOK_CALL,
        (
            "book a call",
            "schedule a call",
            "set up a call",
            "set up a time",
            "hop on a call",
            "jump on a call",
            "let's talk",
            "lets talk",
            "can we talk",
            "give me a call",
            "call me",
            "phone call",
            "available to chat",
            "calendly",
            "book a time",
        ),
    ),
    (
        ReplyIntent.PRICING,
        (
            "how much",
            "pricing",
            "price",
            "cost",
            "quote",
            "budget",
            "what would it cost",
            "rates",
        ),
    ),
    (
        ReplyIntent.INTERESTED,
        (
            "interested",
            "tell me more",
            "sounds good",
            "sounds interesting",
            "would like to know more",
            "sign me up",
            "keen to",
            "let's do it",
            "lets do it",
        ),
    ),
)


def classify_by_keywords(text: str) -> ReplyClassification:
    """Classify a reply using deterministic keyword matching.

    Args:
        text: The raw reply text.

    Returns:
        The classification. Confidence is fixed per category rather than
        computed, since keyword presence is a binary signal -- 0.9 for an
        opt-out (acting on a false negative here is worse than a false
        positive), 0.6 for the others, and 0.3 for UNCLEAR, low enough that a
        caller can choose to hold unclear replies for human review.
    """
    normalized = text.strip().lower()
    if not normalized:
        return ReplyClassification(
            intent=ReplyIntent.UNCLEAR, confidence=0.0, reasons=("Reply is empty.",)
        )

    for intent, phrases in _KEYWORD_RULES:
        for phrase in phrases:
            if phrase in normalized:
                confidence = 0.9 if intent is ReplyIntent.NOT_INTERESTED else 0.6
                logger.info(
                    "Reply classified by keyword", extra={"intent": intent.value}
                )
                return ReplyClassification(
                    intent=intent,
                    confidence=confidence,
                    reasons=(f"Matched phrase: '{phrase}'",),
                    used_fallback=True,
                )

    return ReplyClassification(
        intent=ReplyIntent.UNCLEAR,
        confidence=0.3,
        reasons=("No keyword rule matched; needs human review.",),
        used_fallback=True,
    )


#: LLM output is expected as a bare category name, possibly with surrounding
#: whitespace or punctuation, e.g. "book_call" or "Book Call.".
_LLM_LABEL_PATTERN = re.compile(r"[a-z_]+")


#: Explicit compliance opt-out language -- a subset of the phrases already
#: checked for :attr:`ReplyIntent.NOT_INTERESTED` above, isolated here so a
#: reply containing one of these can be distinguished from a merely soft
#: decline. A reply matching one of these must never receive a generated
#: objection-response draft, no matter how respectful -- see
#: :func:`classify_objection`.
_HARD_OPT_OUT_PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "remove me",
    "stop contacting",
    "stop emailing",
    "please stop",
    "do not contact",
    "don't contact",
    "take me off",
    "not right now and please stop",
)

#: Phrases suggesting the objection is about timing rather than a decline on
#: the merits -- checked only for a reply already classified
#: :attr:`ReplyIntent.NOT_INTERESTED` that is not a hard opt-out.
_TIMING_PHRASES: tuple[str, ...] = (
    "not the right time",
    "not a good time",
    "bad timing",
    "too busy right now",
    "too busy at the moment",
    "check back",
    "call me later",
    "reach out later",
    "circle back",
    "touch base later",
    "maybe next quarter",
    "later this year",
    "next year",
    "in a few months",
)


def is_hard_opt_out(text: str) -> bool:
    """Whether a reply contains explicit compliance opt-out language.

    Args:
        text: The raw reply text.

    Returns:
        True if the text matches one of :data:`_HARD_OPT_OUT_PHRASES`.
    """
    normalized = text.strip().lower()
    return any(phrase in normalized for phrase in _HARD_OPT_OUT_PHRASES)


def classify_objection(text: str, intent: ReplyIntent) -> ObjectionType | None:
    """Sub-classify a reply's objection type, if it raises one at all.

    Deliberately conservative in one direction: a hard opt-out never yields
    an objection type, so no caller can generate a suggested response draft
    for it. Drafting a rebuttal -- however polite, however gated behind human
    approval -- to someone who explicitly asked to stop being contacted is
    wrong regardless of who reviews it before it could be sent.

    Args:
        text: The raw reply text.
        intent: The reply's already-classified :class:`ReplyIntent`.

    Returns:
        - :attr:`ObjectionType.PRICE` for any :attr:`ReplyIntent.PRICING` reply.
        - :attr:`ObjectionType.TIMING` for a :attr:`ReplyIntent.NOT_INTERESTED`
          reply that reads as a timing concern rather than an opt-out.
        - :attr:`ObjectionType.NOT_INTERESTED_YET` for any other
          :attr:`ReplyIntent.NOT_INTERESTED` reply that is not a hard opt-out.
        - None for a hard opt-out, or any other intent (BOOK_CALL,
          INTERESTED, UNCLEAR) -- those are not objections to rebut.
    """
    if intent is ReplyIntent.PRICING:
        return ObjectionType.PRICE

    if intent is ReplyIntent.NOT_INTERESTED:
        if is_hard_opt_out(text):
            logger.info("Reply is a hard opt-out; no objection response will be drafted")
            return None
        normalized = text.strip().lower()
        if any(phrase in normalized for phrase in _TIMING_PHRASES):
            return ObjectionType.TIMING
        return ObjectionType.NOT_INTERESTED_YET

    return None


def parse_llm_label(raw_label: str) -> ReplyIntent | None:
    """Parse an LLM's category output into a :class:`ReplyIntent`.

    Args:
        raw_label: The raw text returned by the model.

    Returns:
        The matched intent, or None if the output doesn't correspond to a
        known category -- the caller should fall back to keyword
        classification rather than guessing.
    """
    normalized = raw_label.strip().lower().replace(" ", "_").replace("-", "_")
    match = _LLM_LABEL_PATTERN.search(normalized)
    if match is None:
        return None

    candidate = match.group(0)
    try:
        return ReplyIntent(candidate)
    except ValueError:
        return None
