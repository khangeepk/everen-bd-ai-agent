"""Pure slot-availability math for the calendar booking link.

Standard library only, so the working-hours/lookahead/overlap logic is
testable without a database or a live Google Calendar call. Given the
shared sales calendar's busy intervals -- fetched separately by
:func:`app.services.google_calendar.get_busy_intervals`, which this module
has no dependency on -- and the working-hours settings on
:class:`app.core.config.Settings`, :func:`compute_available_slots` returns
the list of bookable slots a booking link should offer.

Deliberately conservative: only whole ``slot_duration_minutes`` blocks that
fall fully inside a weekday's working hours, start at or after the minimum
lead time from now, and don't overlap any busy interval are offered. There
is no partial-slot splitting and no per-prospect timezone detection --
every slot is computed in ``booking_timezone`` regardless of where the
prospect actually is; see that setting's docstring for the rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: Python's date.weekday(): Monday=0 ... Sunday=6. Saturday and Sunday are
#: never offered, regardless of working_hour_start/end -- there is no
#: separate "weekend hours" configuration.
_WEEKEND_DAYS = frozenset({5, 6})


class SlotConfigError(ValueError):
    """Raised when slot-computation parameters don't make sense."""


@dataclass(frozen=True)
class BusyInterval:
    """One interval the shared calendar is already busy for.

    Attributes:
        start: Interval start, timezone-aware.
        end: Interval end, timezone-aware.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Validate the interval.

        Raises:
            SlotConfigError: If either bound is naive, or start is not
                before end.
        """
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise SlotConfigError("BusyInterval start/end must be timezone-aware")
        if self.start >= self.end:
            raise SlotConfigError("BusyInterval start must be before end")


@dataclass(frozen=True)
class BookingSlot:
    """One offerable slot.

    Attributes:
        start: Slot start, timezone-aware.
        end: Slot end, timezone-aware.
    """

    start: datetime
    end: datetime


@dataclass(frozen=True)
class SlotComputationConfig:
    """Working-hours and lookahead parameters for slot computation.

    Mirrors the ``booking_*`` fields on :class:`app.core.config.Settings`
    directly. Kept as its own dataclass, rather than taking a ``Settings``
    instance, so this module takes no dependency on the pydantic-settings
    machinery -- the same split used between warmup.py (pure) and
    warmup_tracker.py (settings-aware) elsewhere in this codebase.

    Attributes:
        timezone_name: IANA zone name working hours are defined in.
        slot_duration_minutes: Length of each offered slot.
        working_hour_start: Hour (0-24, local to timezone_name) slots may
            start being offered from.
        working_hour_end: Hour (0-24) by which a slot must have already
            ended -- the last offered slot ends at or before this hour.
        lookahead_days: How many calendar days ahead (including today) to
            offer slots for.
        min_lead_time_minutes: Don't offer a slot starting sooner than this
            many minutes from "now" -- gives the rep some notice.
    """

    timezone_name: str
    slot_duration_minutes: int
    working_hour_start: int
    working_hour_end: int
    lookahead_days: int
    min_lead_time_minutes: int

    def __post_init__(self) -> None:
        """Validate the configuration.

        Raises:
            SlotConfigError: If any parameter is out of range or the
                timezone name is unknown.
        """
        if self.slot_duration_minutes < 1:
            raise SlotConfigError("slot_duration_minutes must be at least 1")
        if not (0 <= self.working_hour_start < self.working_hour_end <= 24):
            raise SlotConfigError(
                "working_hour_start must be less than working_hour_end, both within 0-24"
            )
        if self.lookahead_days < 1:
            raise SlotConfigError("lookahead_days must be at least 1")
        if self.min_lead_time_minutes < 0:
            raise SlotConfigError("min_lead_time_minutes must not be negative")
        try:
            ZoneInfo(self.timezone_name)
        except Exception as exc:
            raise SlotConfigError(f"unknown timezone {self.timezone_name!r}") from exc


def compute_available_slots(
    config: SlotComputationConfig,
    busy_intervals: list[BusyInterval],
    *,
    now: datetime | None = None,
) -> list[BookingSlot]:
    """Compute the bookable slots a booking link should offer.

    Args:
        config: Working-hours and lookahead parameters.
        busy_intervals: The calendar's existing busy intervals, in any
            timezone -- compared against candidate slots on absolute time,
            not local wall-clock, so the caller need not convert them.
        now: The current time, used for the lead-time cutoff and as the
            start of the lookahead window. Defaults to the real current
            time -- overridable so tests are deterministic.

    Returns:
        Bookable slots in chronological order, each exactly
        ``config.slot_duration_minutes`` long, timezone-aware in
        ``config.timezone_name``, fully inside a weekday's working hours,
        starting at or after the minimum lead time, and not overlapping
        any busy interval.
    """
    current_time = now if now is not None else datetime.now(timezone.utc)
    zone = ZoneInfo(config.timezone_name)
    duration = timedelta(minutes=config.slot_duration_minutes)
    earliest_start = current_time + timedelta(minutes=config.min_lead_time_minutes)

    local_today = current_time.astimezone(zone).date()
    slots: list[BookingSlot] = []

    for day_offset in range(config.lookahead_days):
        day = local_today + timedelta(days=day_offset)
        if day.weekday() in _WEEKEND_DAYS:
            continue
        slots.extend(_slots_for_day(day, config, zone, duration, earliest_start, busy_intervals))

    logger.info(
        "Computed booking slots",
        extra={"slot_count": len(slots), "lookahead_days": config.lookahead_days},
    )
    return slots


def _slots_for_day(
    day: date,
    config: SlotComputationConfig,
    zone: ZoneInfo,
    duration: timedelta,
    earliest_start: datetime,
    busy_intervals: list[BusyInterval],
) -> list[BookingSlot]:
    """Compute one weekday's bookable slots.

    Args:
        day: The local calendar date to compute slots for.
        config: Working-hours and lookahead parameters.
        zone: The resolved timezone.
        duration: Slot length.
        earliest_start: The absolute lead-time cutoff -- no slot may start
            before this instant.
        busy_intervals: The calendar's existing busy intervals.

    Returns:
        Bookable slots for this one day, in chronological order.
    """
    midnight = datetime.combine(day, time.min, tzinfo=zone)
    day_start = midnight + timedelta(hours=config.working_hour_start)
    day_end = midnight + timedelta(hours=config.working_hour_end)

    slots: list[BookingSlot] = []
    cursor = day_start
    while cursor + duration <= day_end:
        slot_end = cursor + duration
        if cursor >= earliest_start and not _overlaps_any(cursor, slot_end, busy_intervals):
            slots.append(BookingSlot(start=cursor, end=slot_end))
        cursor += duration
    return slots


def _overlaps_any(start: datetime, end: datetime, busy_intervals: list[BusyInterval]) -> bool:
    """Whether the half-open interval [start, end) overlaps any busy interval.

    Args:
        start: Candidate slot start.
        end: Candidate slot end.
        busy_intervals: The calendar's busy intervals.

    Returns:
        True if any busy interval overlaps the candidate slot.
    """
    return any(start < busy.end and end > busy.start for busy in busy_intervals)
