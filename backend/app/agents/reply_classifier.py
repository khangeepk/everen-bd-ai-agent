"""Reply classification agent.

Classifies an inbound reply into one of the categories in
:mod:`app.services.reply_classification` using the LLM, falling back to the
deterministic keyword classifier on any failure. This agent only classifies --
it does not decide what happens next; pipeline advancement is a separate,
auditable decision made in :mod:`app.services.pipeline_transitions` so the
"what does this reply mean" question and the "what do we do about it"
question can be reasoned about, tested, and changed independently.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.cost_guard import BudgetExceededError, CostProvider, estimate_openai_cost
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.reply_classification import (
    ReplyClassification,
    ReplyIntent,
    classify_by_keywords,
    parse_llm_label,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "reply-classifier-agent-v1"

_SYSTEM_PROMPT = """You classify a prospect's reply to a business development email or \
WhatsApp message into exactly one category.

Categories:
- book_call: they want to schedule a call or speak directly (e.g. "call me", "let's talk")
- pricing: they are asking about cost, pricing, or a quote, without yet asking for a call
- interested: they are engaged and want to know more, but haven't asked for a call or price
- not_interested: they are declining, asking to stop contact, or unsubscribing
- unclear: none of the above clearly applies (out-of-office, wrong person, ambiguous, spam)

If a reply combines a decline with anything else (e.g. "not interested, what would it \
have cost anyway"), classify it as not_interested -- the decline is what matters operationally.

Respond with ONLY the category name, nothing else.
"""


class ReplyClassifierAgent:
    """Classifies inbound replies, preferring the LLM with a keyword fallback."""

    async def classify(self, text: str, db: AsyncSession | None = None) -> ReplyClassification:
        """Classify one reply.

        Args:
            text: The raw reply text.
            db: Active database session, used only to enforce and record
                against the daily OpenAI cost budget (see
                app.services.cost_guard). Optional and defaults to None so
                existing offline unit tests that construct this agent without
                a database keep working -- passing None simply skips the
                budget guard rather than the classification.

        Returns:
            The classification. Falls back to
            :func:`app.services.reply_classification.classify_by_keywords` on
            any LLM failure, an unparseable response, or the daily OpenAI
            budget being exhausted.
        """
        if not text.strip():
            return ReplyClassification(
                intent=ReplyIntent.UNCLEAR, confidence=0.0, reasons=("Reply is empty.",)
            )

        try:
            if db is not None:
                await enforce_budget_before_call(
                    db, CostProvider.OPENAI, settings.cost_guard_daily_budget_openai_usd
                )

            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model=settings.recommendation_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text.strip()},
                ],
                temperature=0.0,
            )
            raw_label = (response.choices[0].message.content or "").strip()

            if db is not None:
                usage = getattr(response, "usage", None)
                cost = estimate_openai_cost(
                    settings.recommendation_model,
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                )
                await record_spend(
                    db,
                    CostProvider.OPENAI,
                    "reply_classifier.classify",
                    cost,
                    daily_budget_usd=settings.cost_guard_daily_budget_openai_usd,
                )
        except BudgetExceededError:
            logger.warning("LLM reply classification skipped: daily OpenAI budget exhausted")
            return classify_by_keywords(text)
        except Exception:
            logger.exception("LLM reply classification failed; using keyword fallback")
            return classify_by_keywords(text)

        intent = parse_llm_label(raw_label)
        if intent is None:
            logger.warning(
                "LLM returned an unparseable label; using keyword fallback",
                extra={"raw_label": raw_label},
            )
            return classify_by_keywords(text)

        logger.info("Reply classified by LLM", extra={"intent": intent.value})
        return ReplyClassification(
            intent=intent,
            confidence=0.85,
            reasons=(f"LLM classified as '{intent.value}'.",),
            used_fallback=False,
        )
