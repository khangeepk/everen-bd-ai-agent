"""DB-aware glue for the API cost guard.

Reads/writes app.db.models.cost.ApiCostEvent and applies the pure budget
logic in app.services.cost_guard against it. Split out the same way
app.services.suppression sits on top of app.services.send_limits.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.cost import ApiCostEvent
from app.db.models.cost import CostProvider as DBCostProvider
from app.services.cost_guard import (
    BudgetExceededError,
    BudgetStatus,
    CostProvider,
    check_budget_before_call,
    evaluate_budget,
)

logger = logging.getLogger(__name__)


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """Compute the UTC start/end instants for the calendar day containing ``day``.

    Args:
        day: Any timezone-aware instant within the target day.

    Returns:
        A ``(start, end)`` pair spanning that UTC calendar day.
    """
    start = datetime.combine(day.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


async def get_daily_spend(
    db: AsyncSession, provider: CostProvider, *, day: datetime | None = None
) -> float:
    """Sum recorded spend for a provider on a given UTC day.

    Args:
        db: Active database session.
        provider: Which provider to sum.
        day: Any instant within the target day. Defaults to now.

    Returns:
        Total USD spent, 0.0 if no events are recorded.
    """
    start, end = _day_bounds(day or utcnow())
    total = (
        await db.execute(
            select(func.coalesce(func.sum(ApiCostEvent.cost_usd), 0.0)).where(
                ApiCostEvent.provider == DBCostProvider(provider.value),
                ApiCostEvent.occurred_at >= start,
                ApiCostEvent.occurred_at <= end,
            )
        )
    ).scalar_one()
    return float(total)


async def get_budget_status(
    db: AsyncSession, provider: CostProvider, daily_budget_usd: float
) -> BudgetStatus:
    """Build today's budget status for a provider.

    Args:
        db: Active database session.
        provider: Which provider to evaluate.
        daily_budget_usd: The configured daily cap for this provider.

    Returns:
        The current budget standing.
    """
    spent = await get_daily_spend(db, provider)
    return evaluate_budget(provider, daily_budget_usd, spent)


async def enforce_budget_before_call(
    db: AsyncSession,
    provider: CostProvider,
    daily_budget_usd: float,
    *,
    estimated_cost_usd: float = 0.0,
) -> BudgetStatus:
    """Check today's spend against budget before making a billed call.

    Args:
        db: Active database session.
        provider: Which provider is about to be called.
        daily_budget_usd: The configured daily cap for this provider.
        estimated_cost_usd: The known/estimated cost of the call about to be
            made (see app.services.cost_guard.check_budget_before_call for
            why this is 0.0 for OpenAI and a flat known rate for Places).

    Returns:
        The budget status at the time of the check.

    Raises:
        BudgetExceededError: If the budget is already exhausted or this
            call's cost would exceed what remains.
    """
    status = await get_budget_status(db, provider, daily_budget_usd)
    try:
        check_budget_before_call(status, estimated_cost_usd)
    except BudgetExceededError:
        logger.error(
            "API call blocked: daily budget exceeded",
            extra={
                "provider": provider.value,
                "spent_usd": status.spent_usd,
                "daily_budget_usd": status.daily_budget_usd,
            },
        )
        raise
    return status


async def record_spend(
    db: AsyncSession,
    provider: CostProvider,
    endpoint: str,
    cost_usd: float,
    *,
    daily_budget_usd: float | None = None,
) -> ApiCostEvent:
    """Record one billed call and, if a budget is given, alert on threshold crossings.

    Args:
        db: Active database session.
        provider: Which provider was called.
        endpoint: Free-text call site (e.g. "places.discover").
        cost_usd: The actual (or, for Places, flat-rate) cost of the call.
        daily_budget_usd: If given, checked after recording to log a WARNING
            the moment cumulative spend crosses the 80% alert threshold, and
            an ERROR if it has now exceeded the budget entirely (the call
            already happened -- this cannot undo it, only surface that the
            NEXT one should be blocked).

    Returns:
        The recorded event.

    Raises:
        ValueError: If ``cost_usd`` is negative.
    """
    if cost_usd < 0:
        raise ValueError("cost_usd must not be negative")

    before = (
        await get_budget_status(db, provider, daily_budget_usd)
        if daily_budget_usd is not None
        else None
    )

    event = ApiCostEvent(
        provider=DBCostProvider(provider.value),
        endpoint=endpoint,
        cost_usd=cost_usd,
        occurred_at=utcnow(),
    )
    db.add(event)
    await db.flush()

    logger.info(
        "API spend recorded",
        extra={
            "provider": provider.value,
            "endpoint": endpoint,
            "cost_usd": cost_usd,
        },
    )

    if before is not None and daily_budget_usd is not None:
        after = await get_budget_status(db, provider, daily_budget_usd)
        if after.exhausted:
            logger.error(
                "Daily API budget fully spent",
                extra={
                    "provider": provider.value,
                    "spent_usd": after.spent_usd,
                    "daily_budget_usd": after.daily_budget_usd,
                },
            )
        elif after.past_alert_threshold and not before.past_alert_threshold:
            logger.warning(
                "Daily API budget crossed the alert threshold",
                extra={
                    "provider": provider.value,
                    "spent_usd": after.spent_usd,
                    "daily_budget_usd": after.daily_budget_usd,
                    "fraction_spent": round(after.fraction_spent, 3),
                    "alert_threshold": after.alert_threshold,
                },
            )

    return event
