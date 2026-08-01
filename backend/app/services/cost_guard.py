"""Daily API-spend budget arithmetic and OpenAI cost estimation.

Standard library only, mirroring the split in app/services/send_limits.py:
the pure formula lives here so budget-exceeded and 80%-alert logic is
testable without a database; the DB-backed spend ledger lives in
app/services/cost_tracking.py.

Two providers are guarded -- Google Places (paid per search) and OpenAI chat
completions (paid per token). PageSpeed Insights is not guarded: it is a free
API (no billing), so a budget cap on it would be pure overhead. OpenAI
embeddings are not guarded either, by explicit scope choice (see the
conversation this was requested in) -- they are two to three orders of
magnitude cheaper per call than a chat completion, so the practical risk of
runaway spend is concentrated in Places and chat completions.

Cost estimation is necessarily approximate:

* Places bills a flat rate per request regardless of what is returned, so its
  per-call cost is knowable in advance and checked BEFORE the call
  (`PLACES_COST_PER_SEARCH_USD`).
* OpenAI bills by token, so the exact cost of a chat completion is only known
  AFTER the response arrives with its `usage` field. This module can only
  block a new call once the day's spend has ALREADY reached the budget
  (`check_budget_before_call`); it cannot pre-emptively refuse the one call
  that would push spend over the top, because the size of that call is
  unknown until it returns. The 80% alert and the post-call budget check
  (`record_and_evaluate`) both run after the fact for this reason.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class CostProvider(str, enum.Enum):
    """An external, billed API this system calls."""

    PLACES = "places"
    OPENAI = "openai"


#: Google Places API (New) Text Search, Essentials SKU, per-request price in
#: USD as of when this was written. Verify against Google's current pricing
#: page before relying on this for real budgeting -- Google revises Places
#: pricing periodically and this client has no way to detect that.
PLACES_COST_PER_SEARCH_USD = 0.017

#: Google Places API (New) Place Details, requesting businessStatus/rating/
#: userRatingCount (app.services.places.GooglePlacesClient.get_place_details,
#: used by the signal scanner -- app/services/signal_scanner.py). Set
#: slightly above the Text Search estimate as a deliberately conservative
#: placeholder pending confirmation of which Places SKU tier these
#: particular fields fall under; verify against Google's current pricing
#: page before relying on this for real budgeting.
PLACE_DETAILS_COST_PER_CALL_USD = 0.02

#: Per-1K-token USD rates for the two OpenAI models this codebase calls
#: (see settings.recommendation_model / settings.embedding_model). Verify
#: against OpenAI's current pricing page before relying on this for real
#: budgeting -- these are not fetched from any live pricing API.
OPENAI_PRICING_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}

#: Used when the active model has no entry in OPENAI_PRICING_PER_1K_TOKENS --
#: a deliberately conservative (i.e. higher than gpt-4o-mini) flat estimate
#: so an unrecognized model errs toward triggering the budget guard sooner
#: rather than silently under-counting spend.
DEFAULT_OPENAI_COST_PER_CALL_USD = 0.02

#: Fraction of the daily budget at which a WARNING alert fires.
DEFAULT_ALERT_THRESHOLD = 0.8


class BudgetExceededError(RuntimeError):
    """Raised when a call would exceed (or already has exceeded) the daily budget."""


@dataclass(frozen=True)
class BudgetStatus:
    """Current standing against one provider's daily budget.

    Attributes:
        provider: Which provider this status describes.
        daily_budget_usd: The configured cap for the day.
        spent_usd: Spend recorded so far today.
        remaining_usd: Budget still available (never negative).
        alert_threshold: Fraction of budget at which to warn (e.g. 0.8).
    """

    provider: CostProvider
    daily_budget_usd: float
    spent_usd: float
    remaining_usd: float
    alert_threshold: float = DEFAULT_ALERT_THRESHOLD

    @property
    def exhausted(self) -> bool:
        """Whether the daily budget has been fully spent.

        Returns:
            True if no budget remains.
        """
        return self.remaining_usd <= 0

    @property
    def fraction_spent(self) -> float:
        """What fraction of the daily budget has been spent.

        Returns:
            0.0 if the budget is 0 or negative (treated as immediately
            exhausted rather than dividing by zero), else spent/budget.
        """
        if self.daily_budget_usd <= 0:
            return 1.0
        return self.spent_usd / self.daily_budget_usd

    @property
    def past_alert_threshold(self) -> bool:
        """Whether spend has crossed the alert threshold.

        Returns:
            True if fraction_spent >= alert_threshold.
        """
        return self.fraction_spent >= self.alert_threshold


def evaluate_budget(
    provider: CostProvider,
    daily_budget_usd: float,
    spent_usd: float,
    *,
    alert_threshold: float = DEFAULT_ALERT_THRESHOLD,
) -> BudgetStatus:
    """Build the current budget standing for a provider.

    Args:
        provider: The provider being evaluated.
        daily_budget_usd: The configured daily cap.
        spent_usd: Spend recorded so far today.
        alert_threshold: Fraction of budget at which to warn.

    Returns:
        The budget status.

    Raises:
        ValueError: If ``daily_budget_usd`` or ``spent_usd`` is negative, or
            ``alert_threshold`` is outside (0.0, 1.0].
    """
    if daily_budget_usd < 0:
        raise ValueError("daily_budget_usd must not be negative")
    if spent_usd < 0:
        raise ValueError("spent_usd must not be negative")
    if not 0.0 < alert_threshold <= 1.0:
        raise ValueError("alert_threshold must be in (0.0, 1.0]")

    return BudgetStatus(
        provider=provider,
        daily_budget_usd=daily_budget_usd,
        spent_usd=spent_usd,
        remaining_usd=max(daily_budget_usd - spent_usd, 0.0),
        alert_threshold=alert_threshold,
    )


def check_budget_before_call(status: BudgetStatus, estimated_cost_usd: float = 0.0) -> None:
    """Refuse to start a new call if the budget is already exhausted.

    For a provider with a known flat per-call cost (Places), pass that cost
    so a call is refused before it would tip spend over budget, not only
    after. For a provider whose cost is unknown until the response arrives
    (OpenAI), the caller should pass 0.0 here -- this then only blocks a call
    from starting once prior calls have already used up the whole budget.

    Args:
        status: The current budget standing.
        estimated_cost_usd: The known or estimated cost of the call about to
            be made, if any.

    Raises:
        ValueError: If ``estimated_cost_usd`` is negative.
        BudgetExceededError: If the budget is already exhausted, or this
            call's known cost would exceed what remains.
    """
    if estimated_cost_usd < 0:
        raise ValueError("estimated_cost_usd must not be negative")

    if status.exhausted:
        raise BudgetExceededError(
            f"{status.provider.value} daily budget exhausted: "
            f"${status.spent_usd:.4f}/${status.daily_budget_usd:.2f} spent today."
        )
    if estimated_cost_usd > status.remaining_usd:
        raise BudgetExceededError(
            f"{status.provider.value} call would exceed the daily budget: "
            f"${status.spent_usd:.4f} spent, ${status.remaining_usd:.4f} remaining, "
            f"${estimated_cost_usd:.4f} requested."
        )


def estimate_openai_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute the actual USD cost of one OpenAI chat completion call.

    Args:
        model: The model name from the response (or the request).
        prompt_tokens: Input tokens billed, from ``response.usage``.
        completion_tokens: Output tokens billed, from ``response.usage``.

    Returns:
        The cost in USD. Falls back to
        :data:`DEFAULT_OPENAI_COST_PER_CALL_USD` for an unrecognized model
        rather than computing a $0.00 cost, which would silently defeat the
        budget guard.

    Raises:
        ValueError: If either token count is negative.
    """
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts must not be negative")

    rates = OPENAI_PRICING_PER_1K_TOKENS.get(model)
    if rates is None:
        logger.warning(
            "No pricing entry for OpenAI model; using conservative flat estimate",
            extra={"model": model},
        )
        return DEFAULT_OPENAI_COST_PER_CALL_USD

    return (prompt_tokens / 1000.0) * rates["input"] + (completion_tokens / 1000.0) * rates[
        "output"
    ]
