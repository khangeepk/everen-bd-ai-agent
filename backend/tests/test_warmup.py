"""Tests for :mod:`app.services.warmup`."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.warmup import (
    WarmupConfigError,
    WarmupDayStatus,
    WarmupPlan,
    effective_daily_limit,
    is_ramp_complete,
    planned_cap_for_day,
)

_START = date(2026, 8, 1)


def _plan(**overrides: object) -> WarmupPlan:
    """Build a WarmupPlan with sane defaults, overridable per test.

    Args:
        **overrides: Fields to override on top of the defaults
            (start_date=_START, start_volume=10, target_daily_volume=50,
            ramp_days=5).

    Returns:
        The constructed plan.
    """
    fields: dict[str, object] = {
        "start_date": _START,
        "start_volume": 10,
        "target_daily_volume": 50,
        "ramp_days": 5,
    }
    fields.update(overrides)
    return WarmupPlan(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# WarmupPlan validation
# ---------------------------------------------------------------------------


def test_plan_rejects_start_volume_below_one() -> None:
    """A warmup plan must start at at least one send per day."""
    with pytest.raises(WarmupConfigError):
        _plan(start_volume=0)


def test_plan_rejects_target_below_start() -> None:
    """A warmup plan ramps up, never down -- that's a different action."""
    with pytest.raises(WarmupConfigError):
        _plan(start_volume=50, target_daily_volume=10)


def test_plan_rejects_zero_ramp_days() -> None:
    """A ramp needs at least one day to be meaningful."""
    with pytest.raises(WarmupConfigError):
        _plan(ramp_days=0)


def test_plan_allows_equal_start_and_target() -> None:
    """A flat 'ramp' (start == target) is degenerate but not invalid."""
    plan = _plan(start_volume=20, target_daily_volume=20)
    assert plan.start_volume == plan.target_daily_volume


# ---------------------------------------------------------------------------
# planned_cap_for_day
# ---------------------------------------------------------------------------


def test_cap_on_day_zero_is_start_volume() -> None:
    """The first day of the ramp uses the configured start volume."""
    plan = _plan()
    assert planned_cap_for_day(plan, _START) == 10


def test_cap_before_start_date_clamps_to_day_zero() -> None:
    """Dates before the plan started still resolve to a sane value, not negative interpolation."""
    plan = _plan()
    assert planned_cap_for_day(plan, _START - timedelta(days=3)) == 10


def test_cap_on_last_ramp_day_is_target_volume() -> None:
    """Day (ramp_days - 1) is where the curve reaches the target for good."""
    plan = _plan()  # ramp_days=5 -> day index 4
    assert planned_cap_for_day(plan, _START + timedelta(days=4)) == 50


def test_cap_stays_at_target_after_ramp_completes() -> None:
    """Well past the ramp, the cap stays flat at the target -- it doesn't keep climbing."""
    plan = _plan()
    assert planned_cap_for_day(plan, _START + timedelta(days=30)) == 50


def test_cap_interpolates_linearly_mid_ramp() -> None:
    """10 -> 50 over 5 days (day indices 0..4) is +10/day: day 2 should be 30."""
    plan = _plan()
    assert planned_cap_for_day(plan, _START + timedelta(days=1)) == 20
    assert planned_cap_for_day(plan, _START + timedelta(days=2)) == 30
    assert planned_cap_for_day(plan, _START + timedelta(days=3)) == 40


def test_cap_rounds_to_nearest_whole_send() -> None:
    """A ramp that doesn't divide evenly still returns a usable integer cap."""
    plan = _plan(start_volume=10, target_daily_volume=25, ramp_days=4)
    # day indices 0,1,2,3 -> 10, 15, 20, 25
    assert planned_cap_for_day(plan, _START + timedelta(days=1)) == 15
    assert planned_cap_for_day(plan, _START + timedelta(days=2)) == 20


def test_cap_single_day_ramp_jumps_straight_to_target() -> None:
    """ramp_days=1 means day 0 is already the last ramp day."""
    plan = _plan(start_volume=10, target_daily_volume=50, ramp_days=1)
    assert planned_cap_for_day(plan, _START) == 50


# ---------------------------------------------------------------------------
# is_ramp_complete
# ---------------------------------------------------------------------------


def test_ramp_not_complete_on_day_zero() -> None:
    """A multi-day ramp hasn't finished on its first day."""
    plan = _plan()
    assert is_ramp_complete(plan, _START) is False


def test_ramp_complete_on_final_ramp_day() -> None:
    """The ramp is considered complete exactly on the day it hits the target."""
    plan = _plan()
    assert is_ramp_complete(plan, _START + timedelta(days=4)) is True


def test_ramp_complete_well_after_final_day() -> None:
    """Complete stays complete indefinitely afterward."""
    plan = _plan()
    assert is_ramp_complete(plan, _START + timedelta(days=100)) is True


# ---------------------------------------------------------------------------
# effective_daily_limit
# ---------------------------------------------------------------------------


def test_effective_limit_with_no_plan_is_static_limit() -> None:
    """No active warmup plan means the static configured limit applies unchanged."""
    assert effective_daily_limit(None, _START, static_limit=200) == 200


def test_effective_limit_before_plan_start_is_static_limit() -> None:
    """A plan that hasn't started yet doesn't constrain anything yet."""
    plan = _plan()
    assert effective_daily_limit(plan, _START - timedelta(days=1), static_limit=200) == 200


def test_effective_limit_uses_ramp_cap_when_lower_than_static() -> None:
    """The whole point of warmup: the ramp cap gates real sending when it's the tighter limit."""
    plan = _plan()  # day 0 cap is 10
    assert effective_daily_limit(plan, _START, static_limit=200) == 10


def test_effective_limit_never_exceeds_static_limit() -> None:
    """Even if the plan's target is higher than the currently configured static limit, the static limit wins."""
    plan = _plan(start_volume=10, target_daily_volume=500, ramp_days=5)
    assert effective_daily_limit(plan, _START + timedelta(days=4), static_limit=200) == 200


# ---------------------------------------------------------------------------
# WarmupDayStatus.within_cap
# ---------------------------------------------------------------------------


def test_within_cap_true_when_actual_equals_cap() -> None:
    """Sending exactly up to the cap is still within it."""
    status = WarmupDayStatus(check_date=_START, planned_cap=10, actual_sent=10)
    assert status.within_cap is True


def test_within_cap_true_when_actual_below_cap() -> None:
    """Sending less than the cap is within it."""
    status = WarmupDayStatus(check_date=_START, planned_cap=10, actual_sent=3)
    assert status.within_cap is True


def test_within_cap_false_when_actual_exceeds_cap() -> None:
    """Sending more than the ramp allowed for that day is flagged."""
    status = WarmupDayStatus(check_date=_START, planned_cap=10, actual_sent=11)
    assert status.within_cap is False
