"""Tests for :mod:`app.services.campaign_cadence`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.campaign_cadence import (
    is_cadence_exhausted,
    is_follow_up_due,
    max_follow_ups,
    next_follow_up_due_at,
)
from app.services.outreach_policy import CampaignType

_SENT_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# max_follow_ups / is_cadence_exhausted
# ---------------------------------------------------------------------------


def test_max_follow_ups_cold_is_three() -> None:
    """Cold cadence has three scheduled touches (3, 7, 14 day offsets)."""
    assert max_follow_ups(CampaignType.COLD) == 3


def test_max_follow_ups_warm_is_two() -> None:
    """Warm cadence is shorter than cold -- already some rapport/context."""
    assert max_follow_ups(CampaignType.WARM) == 2


def test_max_follow_ups_re_engagement_is_two() -> None:
    """Re-engagement cadence is two touches, spaced far apart."""
    assert max_follow_ups(CampaignType.RE_ENGAGEMENT) == 2


@pytest.mark.parametrize(
    "campaign_type,sequence,expected",
    [
        (CampaignType.COLD, 0, False),
        (CampaignType.COLD, 2, False),
        (CampaignType.COLD, 3, True),
        (CampaignType.COLD, 5, True),
        (CampaignType.WARM, 1, False),
        (CampaignType.WARM, 2, True),
    ],
)
def test_is_cadence_exhausted(
    campaign_type: CampaignType, sequence: int, expected: bool
) -> None:
    """Exhausted exactly once follow_up_sequence reaches max_follow_ups."""
    assert is_cadence_exhausted(campaign_type, sequence) is expected


# ---------------------------------------------------------------------------
# next_follow_up_due_at
# ---------------------------------------------------------------------------


def test_next_due_at_none_when_exhausted() -> None:
    """No next due date once every scheduled follow-up has been sent."""
    assert next_follow_up_due_at(CampaignType.WARM, 2, _SENT_AT) is None


def test_next_due_at_cold_first_follow_up_is_three_days_out() -> None:
    """Cold's first offset is 3 days from the initial send."""
    due = next_follow_up_due_at(CampaignType.COLD, 0, _SENT_AT)
    assert due == _SENT_AT + timedelta(days=3)


def test_next_due_at_cold_second_follow_up_is_seven_days_from_first() -> None:
    """Offsets are relative to the immediately preceding send, not the original."""
    due = next_follow_up_due_at(CampaignType.COLD, 1, _SENT_AT)
    assert due == _SENT_AT + timedelta(days=7)


def test_next_due_at_cold_third_follow_up_is_fourteen_days_out() -> None:
    """Cold's final scheduled offset is 14 days from the second follow-up."""
    due = next_follow_up_due_at(CampaignType.COLD, 2, _SENT_AT)
    assert due == _SENT_AT + timedelta(days=14)


def test_next_due_at_warm_is_shorter_than_cold() -> None:
    """Warm's first offset (2 days) is shorter than cold's (3 days)."""
    due = next_follow_up_due_at(CampaignType.WARM, 0, _SENT_AT)
    assert due == _SENT_AT + timedelta(days=2)


def test_next_due_at_re_engagement_is_longest() -> None:
    """Re-engagement's first offset (7 days) is the slowest of the three types."""
    due = next_follow_up_due_at(CampaignType.RE_ENGAGEMENT, 0, _SENT_AT)
    assert due == _SENT_AT + timedelta(days=7)


# ---------------------------------------------------------------------------
# is_follow_up_due
# ---------------------------------------------------------------------------


def test_not_due_before_offset_elapses() -> None:
    """A follow-up is not due before its scheduled offset has passed."""
    now = _SENT_AT + timedelta(days=2)
    assert is_follow_up_due(CampaignType.COLD, 0, _SENT_AT, now) is False


def test_due_exactly_at_offset() -> None:
    """A follow-up becomes due at exactly its offset, not strictly after."""
    now = _SENT_AT + timedelta(days=3)
    assert is_follow_up_due(CampaignType.COLD, 0, _SENT_AT, now) is True


def test_due_well_after_offset() -> None:
    """A follow-up stays due if the scan didn't run exactly on time."""
    now = _SENT_AT + timedelta(days=10)
    assert is_follow_up_due(CampaignType.COLD, 0, _SENT_AT, now) is True


def test_never_due_once_cadence_exhausted() -> None:
    """No amount of elapsed time makes a follow-up due once the cadence ends."""
    now = _SENT_AT + timedelta(days=365)
    assert is_follow_up_due(CampaignType.WARM, 2, _SENT_AT, now) is False


def test_full_cold_cadence_walkthrough() -> None:
    """Walk a lead through all three cold follow-ups end to end."""
    # Initial send at day 0 (follow_up_sequence=0). First follow-up due day 3.
    assert is_follow_up_due(CampaignType.COLD, 0, _SENT_AT, _SENT_AT + timedelta(days=3))
    # First follow-up sent on day 3 (follow_up_sequence=1). Second due 7 days later, i.e. day 10.
    first_sent = _SENT_AT + timedelta(days=3)
    assert not is_follow_up_due(CampaignType.COLD, 1, first_sent, first_sent + timedelta(days=6))
    assert is_follow_up_due(CampaignType.COLD, 1, first_sent, first_sent + timedelta(days=7))
    # Second follow-up sent on day 10 (follow_up_sequence=2). Third due 14 days later, i.e. day 24.
    second_sent = first_sent + timedelta(days=7)
    assert is_follow_up_due(CampaignType.COLD, 2, second_sent, second_sent + timedelta(days=14))
    # Third follow-up sent (follow_up_sequence=3): cadence exhausted, never due again.
    third_sent = second_sent + timedelta(days=14)
    assert not is_follow_up_due(
        CampaignType.COLD, 3, third_sent, third_sent + timedelta(days=1000)
    )
