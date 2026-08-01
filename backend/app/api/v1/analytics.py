"""Analytics dashboard routes and the prompt version log.

Read endpoints (overview, top industries/services, prompt performance) are
open to any authenticated user -- a dashboard is only useful if the whole
team can see it. Writing a new `PromptVersion` changes what prompt live
outreach generation uses, so that's restricted to approver roles, mirroring
the outreach approval gate elsewhere in this API.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin, require_approver
from app.core.config import settings
from app.db.models.analytics import PromptVersion
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.analytics import (
    CampaignPerformanceListResponse,
    CostStatusResponse,
    CreatePromptVersionRequest,
    OverviewResponse,
    ProviderBudgetResponse,
    PromptVersionPerformanceListResponse,
    PromptVersionResponse,
    RankedItemResponse,
    TopIndustriesResponse,
    TopServicesResponse,
    VariantPerformanceResponse,
    LanguagePerformanceResponse,
)
from app.services.analytics import (
    get_campaign_performance,
    get_overview,
    get_prompt_version_performance,
    get_top_industries,
    get_top_services,
    get_language_performance,
)
from app.services.cost_guard import CostProvider
from app.services.cost_tracking import get_budget_status
from app.services.outreach_policy import OutreachChannel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _variant_to_response(variant) -> VariantPerformanceResponse:  # noqa: ANN001
    """Convert a `VariantPerformance` dataclass into its API response shape.

    Args:
        variant: The computed performance record.

    Returns:
        The API response.
    """
    return VariantPerformanceResponse(
        variant_id=variant.variant_id,
        label=variant.label,
        sent=variant.sent,
        opened=variant.opened,
        replied=variant.replied,
        meetings_booked=variant.meetings_booked,
        deals_won=variant.deals_won,
        open_rate=variant.open_rate,
        reply_rate=variant.reply_rate,
        meeting_rate=variant.meeting_rate,
        win_rate=variant.win_rate,
    )


@router.get(
    "/overview",
    response_model=OverviewResponse,
    summary="Top-line BD performance metrics",
    description=(
        "Emails sent, open rate, reply rate, meetings booked, and deals won for an "
        "optional date range. Open rate reads 0 until outreach email is sent as HTML "
        "-- see app/services/analytics.py for full metric definitions."
    ),
)
async def overview(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OverviewResponse:
    """Fetch the top-line dashboard metrics.

    Args:
        start: Inclusive start of the reporting window, or omit for all time.
        end: Exclusive end of the reporting window, or omit for all time.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The computed overview.
    """
    result = await get_overview(db, start=start, end=end)
    return OverviewResponse(
        period_start=result.period_start,
        period_end=result.period_end,
        emails_sent=result.emails_sent,
        opens=result.opens,
        open_rate=result.open_rate,
        replies=result.replies,
        reply_rate=result.reply_rate,
        meetings_booked=result.meetings_booked,
        deals_won=result.deals_won,
    )


@router.get(
    "/top-industries",
    response_model=TopIndustriesResponse,
    summary="Top industries by won deals",
)
async def top_industries(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    n: int = Query(default=5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TopIndustriesResponse:
    """Fetch the top industries ranked by count of won deals.

    Args:
        start: Inclusive start of the window, or omit for all time.
        end: Exclusive end of the window, or omit for all time.
        n: How many entries to return.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The ranked industries.
    """
    items = await get_top_industries(db, start=start, end=end, n=n)
    return TopIndustriesResponse(
        period_start=start,
        period_end=end,
        items=[RankedItemResponse(label=i.label, count=i.count) for i in items],
    )


@router.get(
    "/top-services",
    response_model=TopServicesResponse,
    summary="Top services by won deals",
)
async def top_services(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    n: int = Query(default=5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TopServicesResponse:
    """Fetch the top services ranked by count of won deals.

    Args:
        start: Inclusive start of the window, or omit for all time.
        end: Exclusive end of the window, or omit for all time.
        n: How many entries to return.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The ranked services.
    """
    items = await get_top_services(db, start=start, end=end, n=n)
    return TopServicesResponse(
        period_start=start,
        period_end=end,
        items=[RankedItemResponse(label=i.label, count=i.count) for i in items],
    )


@router.get(
    "/prompt-performance",
    response_model=PromptVersionPerformanceListResponse,
    summary="Performance by prompt version and A/B variant",
    description=(
        "Rolls up sends/opens/replies/meetings/deals per prompt version used to "
        "generate sent emails, so an old prompt can be compared against a new one "
        "-- or two variants of an active A/B test against each other."
    ),
)
async def prompt_performance(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PromptVersionPerformanceListResponse:
    """Fetch performance broken down by prompt version / A/B variant.

    Args:
        start: Inclusive start of the window, or omit for all time.
        end: Exclusive end of the window, or omit for all time.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        One entry per prompt version / variant with at least one sent email.
    """
    variants = await get_prompt_version_performance(db, start=start, end=end)
    return PromptVersionPerformanceListResponse(
        period_start=start,
        period_end=end,
        variants=[_variant_to_response(v) for v in variants],
    )


@router.get(
    "/campaign-performance",
    response_model=CampaignPerformanceListResponse,
    summary="Performance by campaign type (cold/warm/re-engagement)",
    description=(
        "Rolls up sends/opens/replies/meetings/deals per campaign type on sent "
        "emails, using each draft's own snapshotted campaign_type -- so a lead's "
        "campaign_type changing later never rewrites which bucket an already-sent "
        "draft counted toward. See app/services/analytics.py for the full metric "
        "definitions this shares with prompt-performance."
    ),
)
async def campaign_performance(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignPerformanceListResponse:
    """Fetch performance broken down by campaign type.

    Args:
        start: Inclusive start of the window, or omit for all time.
        end: Exclusive end of the window, or omit for all time.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        One entry per campaign type with at least one sent email.
    """
    campaigns = await get_campaign_performance(db, start=start, end=end)
    return CampaignPerformanceListResponse(
        period_start=start,
        period_end=end,
        campaigns=[_variant_to_response(c) for c in campaigns],
    )


@router.get(
    "/prompt-versions",
    response_model=list[PromptVersionResponse],
    summary="List logged prompt versions",
)
async def list_prompt_versions(
    agent_name: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PromptVersionResponse]:
    """List logged prompt versions, newest first.

    Args:
        agent_name: Optional filter to one agent, e.g. "outreach-draft-agent-v1".
        active_only: Only return currently active versions.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The matching prompt versions.
    """
    filters = []
    if agent_name is not None:
        filters.append(PromptVersion.agent_name == agent_name)
    if active_only:
        filters.append(PromptVersion.is_active.is_(True))

    rows = (
        (
            await db.execute(
                select(PromptVersion).where(*filters).order_by(PromptVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        PromptVersionResponse(
            id=str(row.id),
            agent_name=row.agent_name,
            channel=row.channel.value if row.channel else None,
            label=row.label,
            prompt_text=row.prompt_text,
            is_active=row.is_active,
            experiment_group=row.experiment_group,
            notes=row.notes,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/prompt-versions",
    response_model=PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a new prompt version",
    description=(
        "Restricted to approver roles -- this changes what prompt live outreach "
        "generation uses going forward. Set the same experiment_group on two "
        "active versions for the same agent+channel to run an A/B test between "
        "them; leaving an older version active alongside a new one with a shared "
        "group starts the split immediately."
    ),
)
async def create_prompt_version(
    body: CreatePromptVersionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_approver),
) -> PromptVersionResponse:
    """Log a new prompt version.

    Args:
        body: The prompt version to create.
        db: Active database session.
        user: The authenticated approver.

    Returns:
        The created prompt version.

    Raises:
        HTTPException: 422 if ``channel`` is set but not a recognized channel.
    """
    channel: OutreachChannel | None = None
    if body.channel is not None:
        try:
            channel = OutreachChannel(body.channel)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unrecognized channel '{body.channel}'. Expected one of: "
                f"{[c.value for c in OutreachChannel]}",
            ) from exc

    row = PromptVersion(
        agent_name=body.agent_name,
        channel=channel,
        label=body.label,
        prompt_text=body.prompt_text,
        is_active=body.is_active,
        experiment_group=body.experiment_group,
        notes=body.notes,
        created_by_id=user.id,
    )
    db.add(row)
    await db.flush()

    logger.info(
        "Prompt version created",
        extra={
            "prompt_version_id": str(row.id),
            "agent_name": row.agent_name,
            "channel": channel.value if channel else None,
            "experiment_group": row.experiment_group,
            "created_by": str(user.id),
        },
    )
    return PromptVersionResponse(
        id=str(row.id),
        agent_name=row.agent_name,
        channel=row.channel.value if row.channel else None,
        label=row.label,
        prompt_text=row.prompt_text,
        is_active=row.is_active,
        experiment_group=row.experiment_group,
        notes=row.notes,
        created_at=row.created_at,
    )


@router.get(
    "/cost-status",
    response_model=CostStatusResponse,
    summary="Today's API cost-guard standing",
    description=(
        "Admin-only. Reports today's spend, budget, and remaining headroom for every "
        "cost-guarded provider (Places, OpenAI chat completions). See "
        "app/services/cost_guard.py for what is and isn't guarded and why."
    ),
)
async def get_cost_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> CostStatusResponse:
    """Report today's budget standing for every guarded provider.

    Args:
        db: Active database session.
        user: The authenticated admin caller.

    Returns:
        Today's spend/budget/remaining per provider.
    """
    places_status = await get_budget_status(
        db, CostProvider.PLACES, settings.cost_guard_daily_budget_places_usd
    )
    openai_status = await get_budget_status(
        db, CostProvider.OPENAI, settings.cost_guard_daily_budget_openai_usd
    )

    def _to_response(s) -> ProviderBudgetResponse:
        return ProviderBudgetResponse(
            provider=s.provider.value,
            daily_budget_usd=s.daily_budget_usd,
            spent_usd=s.spent_usd,
            remaining_usd=s.remaining_usd,
            fraction_spent=round(s.fraction_spent, 4),
            past_alert_threshold=s.past_alert_threshold,
            exhausted=s.exhausted,
        )

    return CostStatusResponse(providers=[_to_response(places_status), _to_response(openai_status)])


@router.get(
    "/languages",
    response_model=list[LanguagePerformanceResponse],
    summary="Per-language outreach performance",
    description="Returns sent volume, open rate, and reply rate broken down by draft language.",
)
async def get_language_analytics(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LanguagePerformanceResponse]:
    """Report performance by draft language.

    Args:
        start: Inclusive window start.
        end: Exclusive window end.
        db: Active database session.
        user: Authenticated caller.

    Returns:
        List of language performance records.
    """
    records = await get_language_performance(db, start=start, end=end)
    return [
        LanguagePerformanceResponse(
            language=r.language,
            drafts_sent=r.drafts_sent,
            opens=r.opens,
            open_rate=r.open_rate,
            replies=r.replies,
            reply_rate=r.reply_rate,
        )
        for r in records
    ]
