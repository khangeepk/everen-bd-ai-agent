"""DB-aware orchestration for warmup schedules.

Bridges the persisted `WarmupSchedule` configuration
(app/db/models/warmup.py) and the existing `DailySendCounter` (already
tracking real per-day sends for quota enforcement, app/db/models/outreach.py)
to the pure ramp math in app.services.warmup. The one function every other
part of this codebase actually needs is
:func:`resolve_effective_daily_limit` -- called from
app/api/v1/outreach.py's GET /outreach/quota and POST /drafts/{id}/send so
an active warmup schedule genuinely gates how many messages can go out each
day, not just displays a target.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.outreach import DailySendCounter
from app.db.models.warmup import WarmupSchedule
from app.services.outreach_policy import OutreachChannel
from app.services.warmup import (
    WarmupDayStatus,
    WarmupPlan,
    effective_daily_limit,
    is_ramp_complete,
    planned_cap_for_day,
)

logger = logging.getLogger(__name__)

#: Bound on how many days of history build_warmup_status_report returns, so
#: an old, long-forgotten schedule doesn't force an unbounded scan/response.
_MAX_HISTORY_DAYS = 30


async def get_active_warmup_schedule(
    db: AsyncSession, channel: OutreachChannel
) -> WarmupSchedule | None:
    """Fetch the current active warmup schedule for a channel, if any.

    Args:
        db: Active database session.
        channel: The channel to look up.

    Returns:
        The active schedule, or None if none is configured. If more than
        one row is somehow active at once (a data-entry mistake, since
        :func:`create_warmup_schedule` always deactivates the prior one
        first), the most recently created wins -- same defensive tie-break
        already used for `PromptVersion` in
        `app/agents/outreach.py`'s `_resolve_prompt`.
    """
    rows = (
        (
            await db.execute(
                select(WarmupSchedule)
                .where(
                    WarmupSchedule.channel == channel,
                    WarmupSchedule.is_active.is_(True),
                )
                .order_by(WarmupSchedule.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    if len(rows) > 1:
        logger.warning(
            "Multiple active warmup schedules for one channel; using the most recent",
            extra={"channel": channel.value, "count": len(rows)},
        )
    return rows[0]


async def create_warmup_schedule(
    db: AsyncSession,
    channel: OutreachChannel,
    start_date: date,
    start_volume: int,
    target_daily_volume: int,
    ramp_days: int,
    created_by_id: uuid.UUID | None,
) -> WarmupSchedule:
    """Create and activate a new warmup schedule for a channel.

    Deactivates any existing active schedule for the same channel first, so
    exactly one schedule per channel is ever live -- creating a new one is
    how a rep replaces or restarts a ramp, not a way to run two at once.

    Args:
        db: Active database session. Caller is responsible for committing.
        channel: The channel this schedule ramps.
        start_date: First day the ramp is in effect.
        start_volume: Sends permitted on day 0.
        target_daily_volume: Sends permitted once the ramp completes.
        ramp_days: How many days the ramp spans.
        created_by_id: The user creating this schedule.

    Returns:
        The newly created, active schedule.

    Raises:
        app.services.warmup.WarmupConfigError: If the parameters don't form
            a valid ramp (validated by constructing a WarmupPlan below,
            before anything is written).
    """
    # Validate via the pure dataclass first -- fail before writing anything.
    WarmupPlan(
        start_date=start_date,
        start_volume=start_volume,
        target_daily_volume=target_daily_volume,
        ramp_days=ramp_days,
    )

    previous = await get_active_warmup_schedule(db, channel)
    if previous is not None:
        previous.is_active = False

    schedule = WarmupSchedule(
        channel=channel,
        start_date=start_date,
        start_volume=start_volume,
        target_daily_volume=target_daily_volume,
        ramp_days=ramp_days,
        is_active=True,
        created_by_id=created_by_id,
    )
    db.add(schedule)
    await db.flush()

    logger.info(
        "Warmup schedule created",
        extra={
            "schedule_id": str(schedule.id),
            "channel": channel.value,
            "start_date": start_date.isoformat(),
            "start_volume": start_volume,
            "target_daily_volume": target_daily_volume,
            "ramp_days": ramp_days,
        },
    )
    return schedule


def _to_plan(schedule: WarmupSchedule) -> WarmupPlan:
    """Convert a persisted schedule into the pure ramp-math dataclass.

    Args:
        schedule: The DB row.

    Returns:
        The equivalent WarmupPlan.
    """
    return WarmupPlan(
        start_date=schedule.start_date,
        start_volume=schedule.start_volume,
        target_daily_volume=schedule.target_daily_volume,
        ramp_days=schedule.ramp_days,
    )


async def resolve_effective_daily_limit(
    db: AsyncSession,
    channel: OutreachChannel,
    static_limit: int,
    moment: datetime | None = None,
) -> int:
    """The real daily send limit for right now, honoring an active warmup schedule.

    This is the enforcement point: app/api/v1/outreach.py calls this instead
    of using settings.outreach_daily_send_limit directly, both when
    reporting quota standing and immediately before dispatching a send.

    Args:
        db: Active database session.
        channel: The channel about to send.
        static_limit: The configured, non-warmup daily limit.
        moment: The instant to evaluate at. Defaults to now.

    Returns:
        ``static_limit`` if no active warmup schedule exists (or the
        schedule hasn't started yet); otherwise the smaller of
        ``static_limit`` and today's planned ramp cap.
    """
    schedule = await get_active_warmup_schedule(db, channel)
    if schedule is None:
        return static_limit

    check_date = (moment or utcnow()).date()
    plan = _to_plan(schedule)
    limit = effective_daily_limit(plan, check_date, static_limit)
    if limit < static_limit:
        logger.info(
            "Active warmup schedule is capping today's send limit",
            extra={
                "channel": channel.value,
                "schedule_id": str(schedule.id),
                "static_limit": static_limit,
                "warmup_limit": limit,
            },
        )
    return limit


@dataclass
class WarmupStatusReport:
    """A warmup schedule's current standing, for the tracker endpoint.

    Attributes:
        schedule: The active schedule, or None if none is configured.
        today: Today's planned-vs-actual status, or None if no schedule.
        ramp_complete: Whether the ramp period has finished.
        history: Planned-vs-actual for each day from the schedule's start
            through today (capped at _MAX_HISTORY_DAYS most recent days).
    """

    schedule: WarmupSchedule | None
    today: WarmupDayStatus | None
    ramp_complete: bool
    history: list[WarmupDayStatus]


async def _actual_sent_on(db: AsyncSession, channel: OutreachChannel, day: date) -> int:
    """Look up how many sends were actually made on one date.

    Args:
        db: Active database session.
        channel: The channel to look up.
        day: The date to look up.

    Returns:
        The count, or 0 if no counter row exists for that date yet.
    """
    counter = (
        await db.execute(
            select(DailySendCounter).where(
                DailySendCounter.quota_date == day, DailySendCounter.channel == channel
            )
        )
    ).scalar_one_or_none()
    return counter.sent_count if counter is not None else 0


async def build_warmup_status_report(
    db: AsyncSession, channel: OutreachChannel, moment: datetime | None = None
) -> WarmupStatusReport:
    """Build the current warmup standing for a channel.

    Args:
        db: Active database session.
        channel: The channel to report on.
        moment: The instant to evaluate at. Defaults to now.

    Returns:
        The status report. All fields are None/empty if no active schedule
        is configured -- not an error, just nothing to report yet.
    """
    schedule = await get_active_warmup_schedule(db, channel)
    if schedule is None:
        return WarmupStatusReport(schedule=None, today=None, ramp_complete=False, history=[])

    plan = _to_plan(schedule)
    today = (moment or utcnow()).date()

    today_status: WarmupDayStatus | None = None
    history: list[WarmupDayStatus] = []
    if today >= schedule.start_date:
        # Cap how far back history goes so a long-forgotten schedule doesn't
        # force an unbounded day-by-day scan.
        earliest_allowed = today - timedelta(days=_MAX_HISTORY_DAYS - 1)
        history_start = max(schedule.start_date, earliest_allowed)

        day = history_start
        while day <= today:
            actual = await _actual_sent_on(db, channel, day)
            status = WarmupDayStatus(
                check_date=day, planned_cap=planned_cap_for_day(plan, day), actual_sent=actual
            )
            history.append(status)
            if day == today:
                today_status = status
            day += timedelta(days=1)

    return WarmupStatusReport(
        schedule=schedule,
        today=today_status,
        ramp_complete=is_ramp_complete(plan, today),
        history=history,
    )
