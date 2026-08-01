"""Tests for :mod:`app.services.booking_slots`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.booking_slots import (
    BusyInterval,
    SlotComputationConfig,
    SlotConfigError,
    compute_available_slots,
)

_ZONE = ZoneInfo("America/Chicago")


def _config(**overrides: object) -> SlotComputationConfig:
    """Build a SlotComputationConfig with sane defaults, overridable per test.

    Args:
        **overrides: Fields to override on top of the defaults (Chicago
            timezone, 30-minute slots, 9-17 working hours, 10-day lookahead,
            60-minute minimum lead time).

    Returns:
        The constructed config.
    """
    fields: dict[str, object] = {
        "timezone_name": "America/Chicago",
        "slot_duration_minutes": 30,
        "working_hour_start": 9,
        "working_hour_end": 17,
        "lookahead_days": 10,
        "min_lead_time_minutes": 60,
    }
    fields.update(overrides)
    return SlotComputationConfig(**fields)  # type: ignore[arg-type]


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a Chicago-local, timezone-aware datetime for test fixtures.

    Args:
        year: Calendar year.
        month: Calendar month.
        day: Calendar day.
        hour: Local hour (0-23).
        minute: Local minute.

    Returns:
        The timezone-aware datetime, in America/Chicago.
    """
    return datetime(year, month, day, hour, minute, tzinfo=_ZONE)


# Friday 2026-07-31, 10:00 local -- a weekday with plenty of lookahead room.
_FRIDAY_MORNING = _local(2026, 7, 31, 10, 0)


# ---------------------------------------------------------------------------
# SlotComputationConfig validation
# ---------------------------------------------------------------------------


def test_config_rejects_zero_duration() -> None:
    """A slot must have positive length."""
    with pytest.raises(SlotConfigError):
        _config(slot_duration_minutes=0)


def test_config_rejects_start_not_before_end() -> None:
    """Working hours must span a real, non-empty window."""
    with pytest.raises(SlotConfigError):
        _config(working_hour_start=17, working_hour_end=9)


def test_config_rejects_zero_lookahead() -> None:
    """A booking link must offer at least one day of slots."""
    with pytest.raises(SlotConfigError):
        _config(lookahead_days=0)


def test_config_rejects_negative_lead_time() -> None:
    """A negative lead time doesn't mean anything."""
    with pytest.raises(SlotConfigError):
        _config(min_lead_time_minutes=-1)


def test_config_rejects_unknown_timezone() -> None:
    """An invalid IANA zone name must fail fast, not at slot-computation time."""
    with pytest.raises(SlotConfigError):
        _config(timezone_name="Not/ARealZone")


def test_config_allows_working_hour_end_of_24() -> None:
    """working_hour_end=24 (midnight) is a valid upper bound, e.g. a 24-hour business."""
    config = _config(working_hour_end=24)
    assert config.working_hour_end == 24


# ---------------------------------------------------------------------------
# compute_available_slots -- basic shape
# ---------------------------------------------------------------------------


def test_empty_calendar_produces_slots_every_lookahead_weekday() -> None:
    """With nothing on the calendar, every weekday in the lookahead window has slots."""
    config = _config()
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    weekdays_with_slots = {s.start.astimezone(_ZONE).date() for s in slots}
    # 10 lookahead days from Fri 2026-07-31 covers two weekends (4 weekend
    # days) and 6 weekdays.
    assert len(weekdays_with_slots) == 6


def test_no_slot_falls_on_a_weekend() -> None:
    """Saturday and Sunday never appear, regardless of working hours."""
    config = _config()
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    assert all(s.start.astimezone(_ZONE).weekday() < 5 for s in slots)


def test_every_slot_is_exactly_the_configured_duration() -> None:
    """Every returned slot is exactly slot_duration_minutes long, no more, no less."""
    config = _config(slot_duration_minutes=45)
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    assert slots  # sanity: the fixture window does produce slots
    assert all((s.end - s.start) == timedelta(minutes=45) for s in slots)


def test_no_slot_starts_before_working_hours_or_ends_after() -> None:
    """Every slot fits fully inside the configured working-hours window."""
    config = _config(working_hour_start=9, working_hour_end=17)
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    for slot in slots:
        local_start = slot.start.astimezone(_ZONE)
        local_end = slot.end.astimezone(_ZONE)
        assert local_start.hour >= 9
        assert (local_end.hour, local_end.minute) <= (17, 0)


# ---------------------------------------------------------------------------
# Lead time
# ---------------------------------------------------------------------------


def test_no_slot_starts_before_the_minimum_lead_time() -> None:
    """A slot starting sooner than min_lead_time_minutes from now is never offered."""
    config = _config(min_lead_time_minutes=60)
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    earliest_allowed = _FRIDAY_MORNING + timedelta(minutes=60)
    assert all(s.start >= earliest_allowed for s in slots)


def test_first_slot_is_the_first_one_at_or_after_the_lead_time_cutoff() -> None:
    """The very first offered slot should be no later than one slot-length after the cutoff."""
    config = _config(min_lead_time_minutes=60, slot_duration_minutes=30)
    slots = compute_available_slots(config, [], now=_FRIDAY_MORNING)
    earliest_allowed = _FRIDAY_MORNING + timedelta(minutes=60)
    assert slots[0].start - earliest_allowed < timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Busy-interval overlap
# ---------------------------------------------------------------------------


def test_a_busy_interval_blocks_only_the_overlapping_slots() -> None:
    """A 9:00-10:00 busy block removes exactly the 9:00 and 9:30 slots, not 10:00."""
    config = _config()
    monday = _local(2026, 8, 3, 9, 0)
    busy = [BusyInterval(start=monday, end=monday + timedelta(hours=1))]
    slots = compute_available_slots(config, busy, now=_FRIDAY_MORNING)

    monday_slots = {
        s.start.astimezone(_ZONE)
        for s in slots
        if s.start.astimezone(_ZONE).date() == monday.date()
    }
    assert _local(2026, 8, 3, 9, 0) not in monday_slots
    assert _local(2026, 8, 3, 9, 30) not in monday_slots
    assert _local(2026, 8, 3, 10, 0) in monday_slots


def test_a_busy_interval_partially_overlapping_a_slot_still_blocks_it() -> None:
    """A busy block from 9:15-9:45 overlaps both the 9:00 and 9:30 slots -- both are blocked."""
    config = _config()
    monday = _local(2026, 8, 3, 9, 15)
    busy = [BusyInterval(start=monday, end=monday + timedelta(minutes=30))]
    slots = compute_available_slots(config, busy, now=_FRIDAY_MORNING)

    monday_slots = {
        s.start.astimezone(_ZONE)
        for s in slots
        if s.start.astimezone(_ZONE).date() == monday.date()
    }
    assert _local(2026, 8, 3, 9, 0) not in monday_slots
    assert _local(2026, 8, 3, 9, 30) not in monday_slots
    assert _local(2026, 8, 3, 10, 0) in monday_slots


def test_a_fully_booked_day_offers_no_slots_that_day_but_others_remain() -> None:
    """Blocking an entire day's working hours removes only that day's slots."""
    config = _config()
    monday_start = _local(2026, 8, 3, 9, 0)
    monday_end = _local(2026, 8, 3, 17, 0)
    busy = [BusyInterval(start=monday_start, end=monday_end)]
    slots = compute_available_slots(config, busy, now=_FRIDAY_MORNING)

    monday_slots = [s for s in slots if s.start.astimezone(_ZONE).date() == monday_start.date()]
    assert monday_slots == []
    assert len(slots) > 0


def test_busy_interval_in_a_different_timezone_still_blocks_correctly() -> None:
    """Busy intervals are compared on absolute time -- a UTC interval overlaps correctly."""
    config = _config()
    # 14:00 UTC on 2026-08-03 is 09:00 America/Chicago (UTC-5 in August, DST).
    busy_utc = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    busy = [BusyInterval(start=busy_utc, end=busy_utc + timedelta(hours=1))]
    slots = compute_available_slots(config, busy, now=_FRIDAY_MORNING)

    monday_slots = {
        s.start.astimezone(_ZONE)
        for s in slots
        if s.start.astimezone(_ZONE).date() == datetime(2026, 8, 3).date()
    }
    assert _local(2026, 8, 3, 9, 0) not in monday_slots


# ---------------------------------------------------------------------------
# BusyInterval validation
# ---------------------------------------------------------------------------


def test_busy_interval_rejects_naive_datetimes() -> None:
    """A BusyInterval without timezone info is rejected -- absolute-time comparison requires it."""
    with pytest.raises(SlotConfigError):
        BusyInterval(start=datetime(2026, 8, 3, 9, 0), end=datetime(2026, 8, 3, 10, 0))


def test_busy_interval_rejects_start_after_end() -> None:
    """A busy interval must have a positive duration."""
    start = _local(2026, 8, 3, 10, 0)
    end = _local(2026, 8, 3, 9, 0)
    with pytest.raises(SlotConfigError):
        BusyInterval(start=start, end=end)
