"""Pure ramp-math for a sending-domain warmup schedule.

Standard library only, so the ramp curve is testable without a database.
The persisted plan (`app.db.models.warmup.WarmupPlan`) and the DB-aware
lookups (actual sends via the existing `DailySendCounter`, resolving which
plan is active) live in :mod:`app.services.warmup_tracker`.

Why warmup exists at all: mailbox providers build sending-reputation
gradually and treat a sudden volume spike from a domain/IP with no sending
history as a spam signal, the same underlying reason
:mod:`app.services.send_limits` enforces a flat daily cap. A warmup schedule
is a *time-varying* version of that same cap -- low on day one, ramping up
to the steady-state daily limit over a configured number of days -- rather
than a second, independent limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class WarmupConfigError(ValueError):
    """Raised when a warmup plan's parameters don't make sense."""


@dataclass(frozen=True)
class WarmupPlan:
    """A sending-domain warmup ramp, in pure data form.

    Attributes:
        start_date: The first day the ramp is in effect.
        start_volume: Sends permitted on ``start_date`` (day 0).
        target_daily_volume: Sends permitted once the ramp completes -- the
            eventual steady-state cap this plan is ramping up to.
        ramp_days: How many days the ramp spans. Day 0 is ``start_volume``,
            day ``ramp_days - 1`` is ``target_daily_volume``; every day in
            between is linearly interpolated. Day ``ramp_days`` and beyond
            stay at ``target_daily_volume``.
    """

    start_date: date
    start_volume: int
    target_daily_volume: int
    ramp_days: int

    def __post_init__(self) -> None:
        """Validate the plan's parameters.

        Raises:
            WarmupConfigError: If any parameter is out of range, or the
                target is below the start (a warmup plan ramps up, never
                down -- a deliberate volume reduction is a different action,
                just lowering OUTREACH_DAILY_SEND_LIMIT directly).
        """
        if self.start_volume < 1:
            raise WarmupConfigError("start_volume must be at least 1")
        if self.target_daily_volume < self.start_volume:
            raise WarmupConfigError(
                "target_daily_volume must be greater than or equal to start_volume "
                "-- a warmup plan ramps up, it does not ramp down"
            )
        if self.ramp_days < 1:
            raise WarmupConfigError("ramp_days must be at least 1")


def planned_cap_for_day(plan: WarmupPlan, check_date: date) -> int:
    """The planned send cap for one day under a warmup plan.

    Args:
        plan: The warmup plan.
        check_date: The date to evaluate. Dates before ``plan.start_date``
            are clamped to day 0's cap -- callers should treat the plan as
            not yet active before its start date (see
            app.services.warmup_tracker.resolve_effective_daily_limit)
            rather than relying on this function for that distinction.

    Returns:
        The planned cap, rounded to the nearest whole send.
    """
    day_index = (check_date - plan.start_date).days
    # Check the "ramp already finished" bound first: for a single-day ramp
    # (ramp_days == 1), day 0 is simultaneously day 0 and day
    # (ramp_days - 1), and the docstring's contract for day (ramp_days - 1)
    # -- target_daily_volume -- must win over the "day 0 -> start_volume"
    # default below.
    if day_index >= plan.ramp_days - 1:
        return plan.target_daily_volume
    if day_index <= 0:
        return plan.start_volume

    progress = day_index / (plan.ramp_days - 1)
    span = plan.target_daily_volume - plan.start_volume
    return round(plan.start_volume + span * progress)


def is_ramp_complete(plan: WarmupPlan, check_date: date) -> bool:
    """Whether a warmup plan's ramp period has finished by a given date.

    Args:
        plan: The warmup plan.
        check_date: The date to evaluate.

    Returns:
        True once ``check_date`` reaches the day the ramp curve hits
        ``target_daily_volume`` for good.
    """
    day_index = (check_date - plan.start_date).days
    return day_index >= plan.ramp_days - 1


def effective_daily_limit(plan: WarmupPlan | None, check_date: date, static_limit: int) -> int:
    """The real daily send limit for a date, honoring an active warmup plan.

    Args:
        plan: The active warmup plan, or None if none is configured.
        check_date: The date to evaluate.
        static_limit: The configured, non-warmup daily limit
            (``settings.outreach_daily_send_limit``) -- the ceiling this
            function never exceeds, even if a plan's ramp curve would
            otherwise permit more (e.g. a plan created with a
            higher-than-currently-configured target).

    Returns:
        ``static_limit`` if there is no plan, or the date is before the
        plan's start date. Otherwise the smaller of ``static_limit`` and
        the plan's planned cap for that date.
    """
    if plan is None or check_date < plan.start_date:
        return static_limit
    return min(static_limit, planned_cap_for_day(plan, check_date))


@dataclass(frozen=True)
class WarmupDayStatus:
    """One day's planned-vs-actual standing under a warmup plan.

    Attributes:
        check_date: The date this status covers.
        planned_cap: The plan's cap for this date.
        actual_sent: Sends actually made on this date.
        within_cap: Whether actual sends stayed at or under the planned cap.
    """

    check_date: date
    planned_cap: int
    actual_sent: int

    @property
    def within_cap(self) -> bool:
        """Whether actual sends stayed at or under the planned cap.

        Returns:
            True if ``actual_sent <= planned_cap``.
        """
        return self.actual_sent <= self.planned_cap
