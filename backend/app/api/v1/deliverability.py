"""Deliverability checklist routes: SPF/DKIM/DMARC checks, warmup schedules,
and the combined pre-launch readiness report.

Rep-triggered, mirroring every other on-demand check in this codebase
(audits, signal scans, email enrichment) -- nothing here runs on a
schedule. None of these routes send anything; POST /warmup/plans changes
what the send gate will *allow* going forward (see
app/services/warmup_tracker.py, wired into app/api/v1/outreach.py), which is
why it requires an approver, the same bar as approving an outreach draft.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_approver, require_write_access
from app.db.models.deliverability import DeliverabilityCheck
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.deliverability import (
    CreateWarmupScheduleRequest,
    DeliverabilityCheckResponse,
    ReadinessReportResponse,
    ReadinessSectionResponse,
    RunDeliverabilityCheckRequest,
    WarmupDayStatusResponse,
    WarmupScheduleResponse,
    WarmupStatusResponse,
)
from app.services.deliverability_checker import run_deliverability_check
from app.services.outreach_policy import OutreachChannel
from app.services.readiness_report import ReadinessReport, build_readiness_report
from app.services.warmup import WarmupConfigError
from app.services.warmup_tracker import (
    WarmupStatusReport,
    build_warmup_status_report,
    create_warmup_schedule,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["deliverability"])


def _warmup_status_response(report: WarmupStatusReport) -> WarmupStatusResponse:
    """Convert a WarmupStatusReport into its response schema.

    Args:
        report: The status report to convert.

    Returns:
        The response schema.
    """
    return WarmupStatusResponse(
        schedule=(
            WarmupScheduleResponse.model_validate(report.schedule)
            if report.schedule is not None
            else None
        ),
        today=(
            WarmupDayStatusResponse.model_validate(report.today)
            if report.today is not None
            else None
        ),
        ramp_complete=report.ramp_complete,
        history=[WarmupDayStatusResponse.model_validate(day) for day in report.history],
    )


def _readiness_report_response(report: ReadinessReport) -> ReadinessReportResponse:
    """Convert a ReadinessReport into its response schema.

    Args:
        report: The report to convert.

    Returns:
        The response schema.
    """
    return ReadinessReportResponse(
        domain=report.domain,
        deliverability=DeliverabilityCheckResponse.model_validate(report.deliverability),
        warmup=_warmup_status_response(report.warmup),
        sender_identity=ReadinessSectionResponse(
            status=report.sender_identity.status, messages=list(report.sender_identity.messages)
        ),
        sandbox_mode=ReadinessSectionResponse(
            status=report.sandbox_mode.status, messages=list(report.sandbox_mode.messages)
        ),
        overall_status=report.overall_status,
    )


@router.post(
    "/deliverability/checks",
    response_model=DeliverabilityCheckResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a fresh SPF/DKIM/DMARC check",
    description=(
        "Queries DNS live and persists the result. Does not send anything or "
        "change any configuration."
    ),
)
async def run_check(
    payload: RunDeliverabilityCheckRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> DeliverabilityCheckResponse:
    """Run a fresh deliverability check.

    Args:
        payload: The domain/selectors to check, or defaults.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The persisted check.

    Raises:
        HTTPException: 422 if no domain could be determined -- neither a
            request-supplied domain, DELIVERABILITY_CHECK_DOMAIN, nor a real
            OUTREACH_FROM_EMAIL to derive one from.
    """
    try:
        check = await run_deliverability_check(db, payload.domain, payload.dkim_selectors)
    except ValueError as exc:
        logger.exception("Deliverability check request rejected", extra={"user_id": str(user.id)})
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    logger.info(
        "Deliverability check requested via API",
        extra={"user_id": str(user.id), "check_id": str(check.id), "domain": check.domain},
    )
    return DeliverabilityCheckResponse.model_validate(check)


@router.get(
    "/deliverability/checks",
    response_model=list[DeliverabilityCheckResponse],
    summary="List past deliverability checks",
    description="Most recent first.",
)
async def list_checks(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DeliverabilityCheckResponse]:
    """List recent deliverability checks.

    Args:
        limit: Maximum rows to return.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The most recent checks, newest first.
    """
    rows = (
        (
            await db.execute(
                select(DeliverabilityCheck)
                .order_by(DeliverabilityCheck.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [DeliverabilityCheckResponse.model_validate(row) for row in rows]


@router.get(
    "/deliverability/readiness",
    response_model=ReadinessReportResponse,
    summary="Get the combined pre-launch readiness report",
    description=(
        "Runs a fresh SPF/DKIM/DMARC check and combines it with the email "
        "channel's warmup standing, whether CAN-SPAM sender identity is "
        "configured, and whether SendGrid sandbox mode is on, into one overall "
        "verdict. Computed live on every call -- nothing here is cached, since "
        "a stale readiness report right before a real launch would be worse "
        "than no report at all."
    ),
)
async def get_readiness(
    domain: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReadinessReportResponse:
    """Build the pre-launch readiness report.

    Args:
        domain: Domain to check. Defaults to the configured domain.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The combined report.
    """
    report = await build_readiness_report(db, domain)
    return _readiness_report_response(report)


@router.post(
    "/warmup/plans",
    response_model=WarmupScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create (and activate) a warmup schedule",
    description=(
        "Deactivates any existing active schedule for the same channel first -- "
        "exactly one schedule per channel is ever live. Once active, the send "
        "gate (POST /outreach/drafts/{id}/send) enforces this schedule's daily "
        "cap in addition to OUTREACH_DAILY_SEND_LIMIT, using whichever is lower. "
        "Restricted to approver roles, the same bar as approving an outreach "
        "draft, since this changes what real sending is allowed to do."
    ),
)
async def create_plan(
    payload: CreateWarmupScheduleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_approver),
) -> WarmupScheduleResponse:
    """Create and activate a new warmup schedule.

    Args:
        payload: The schedule parameters.
        db: Active database session.
        user: The authenticated approver.

    Returns:
        The newly created, active schedule.

    Raises:
        HTTPException: 422 if the parameters don't form a valid ramp (e.g.
            target below start, or a non-positive volume/day count).
    """
    try:
        schedule = await create_warmup_schedule(
            db,
            payload.channel,
            payload.start_date,
            payload.start_volume,
            payload.target_daily_volume,
            payload.ramp_days,
            created_by_id=user.id,
        )
    except WarmupConfigError as exc:
        logger.exception("Warmup schedule request rejected", extra={"user_id": str(user.id)})
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    logger.info(
        "Warmup schedule created via API",
        extra={
            "user_id": str(user.id),
            "schedule_id": str(schedule.id),
            "channel": schedule.channel.value,
        },
    )
    return WarmupScheduleResponse.model_validate(schedule)


@router.get(
    "/warmup/status",
    response_model=WarmupStatusResponse,
    summary="Get a channel's current warmup standing",
    description="Planned-vs-actual sends, day by day, for the active schedule.",
)
async def get_warmup_status(
    channel: OutreachChannel = Query(default=OutreachChannel.EMAIL),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WarmupStatusResponse:
    """Report a channel's current warmup standing.

    Args:
        channel: Which channel to report on.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The status report. Every field is null/empty if no schedule is
        configured for this channel.
    """
    report = await build_warmup_status_report(db, channel)
    return _warmup_status_response(report)
