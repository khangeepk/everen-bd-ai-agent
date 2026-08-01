"""Tests for :mod:`app.services.send_limits`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.send_limits import (
    BounceType,
    QuotaExceededError,
    check_can_send,
    classify_sendgrid_event,
    evaluate_quota,
    next_reset,
    quota_date_for,
    should_suppress,
)

NOW = datetime(2026, 7, 29, 14, 30, tzinfo=timezone.utc)


def test_quota_date_uses_utc() -> None:
    """The quota day is the UTC date, regardless of the input timezone."""
    tokyo = timezone(timedelta(hours=9))
    # 2026-07-30 06:00 in Tokyo is 2026-07-29 21:00 UTC.
    moment = datetime(2026, 7, 30, 6, 0, tzinfo=tokyo)

    assert quota_date_for(moment) == datetime(2026, 7, 29).date()


def test_quota_date_rejects_naive_datetimes() -> None:
    """A naive timestamp would make the quota boundary ambiguous."""
    with pytest.raises(ValueError, match="timezone-aware"):
        quota_date_for(datetime(2026, 7, 29, 14, 30))


def test_next_reset_is_midnight_utc_tomorrow() -> None:
    """The window closes at the next UTC midnight."""
    assert next_reset(NOW) == datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)


def test_next_reset_rejects_naive_datetimes() -> None:
    """Reset calculation also requires an aware timestamp."""
    with pytest.raises(ValueError, match="timezone-aware"):
        next_reset(datetime(2026, 7, 29))


def test_fresh_quota_has_full_remaining() -> None:
    """With nothing sent, the whole limit is available."""
    status = evaluate_quota(limit=50, used=0, moment=NOW)

    assert status.remaining == 50
    assert status.exhausted is False


def test_partially_used_quota() -> None:
    """Remaining is limit minus used."""
    status = evaluate_quota(limit=50, used=20, moment=NOW)
    assert status.remaining == 30


def test_exhausted_quota_reports_zero_remaining() -> None:
    """A fully used quota has nothing left."""
    status = evaluate_quota(limit=50, used=50, moment=NOW)

    assert status.remaining == 0
    assert status.exhausted is True


def test_overused_quota_clamps_at_zero() -> None:
    """Remaining never goes negative, even if usage somehow exceeded the limit."""
    status = evaluate_quota(limit=50, used=60, moment=NOW)

    assert status.remaining == 0
    assert status.exhausted is True


def test_zero_limit_blocks_everything() -> None:
    """A limit of zero disables sending entirely."""
    status = evaluate_quota(limit=0, used=0, moment=NOW)
    assert status.exhausted is True


def test_negative_limit_is_rejected() -> None:
    """A negative limit is a misconfiguration."""
    with pytest.raises(ValueError, match="limit must not be negative"):
        evaluate_quota(limit=-1, used=0, moment=NOW)


def test_negative_usage_is_rejected() -> None:
    """Negative usage indicates a counter bug."""
    with pytest.raises(ValueError, match="used must not be negative"):
        evaluate_quota(limit=50, used=-1, moment=NOW)


def test_check_can_send_passes_within_quota() -> None:
    """A send inside the remaining allowance is permitted."""
    check_can_send(evaluate_quota(limit=50, used=10, moment=NOW), count=1)


def test_check_can_send_permits_the_exact_last_send() -> None:
    """The final available send is allowed, not off-by-one rejected."""
    check_can_send(evaluate_quota(limit=50, used=49, moment=NOW), count=1)


def test_check_can_send_blocks_when_exhausted() -> None:
    """One send past the limit is refused."""
    with pytest.raises(QuotaExceededError, match="Daily send limit reached"):
        check_can_send(evaluate_quota(limit=50, used=50, moment=NOW), count=1)


def test_check_can_send_blocks_an_oversized_batch() -> None:
    """A batch larger than the remaining allowance is refused whole."""
    with pytest.raises(QuotaExceededError):
        check_can_send(evaluate_quota(limit=50, used=45, moment=NOW), count=10)


def test_quota_error_states_when_it_resets() -> None:
    """The error tells the operator when they can send again."""
    with pytest.raises(QuotaExceededError, match="2026-07-30"):
        check_can_send(evaluate_quota(limit=10, used=10, moment=NOW), count=1)


def test_check_can_send_rejects_non_positive_count() -> None:
    """Sending zero or negative messages is a programming error."""
    status = evaluate_quota(limit=50, used=0, moment=NOW)

    for count in (0, -1):
        with pytest.raises(ValueError, match="count must be positive"):
            check_can_send(status, count=count)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("bounce", BounceType.HARD),
        ("dropped", BounceType.HARD),
        ("blocked", BounceType.BLOCKED),
        ("spamreport", BounceType.COMPLAINT),
        ("deferred", BounceType.SOFT),
        ("delivered", BounceType.UNKNOWN),
        ("nonsense", BounceType.UNKNOWN),
    ],
)
def test_sendgrid_event_classification(event: str, expected: BounceType) -> None:
    """Provider event names map to bounce types."""
    assert classify_sendgrid_event(event) is expected


def test_event_classification_is_case_insensitive() -> None:
    """Provider casing does not change classification."""
    assert classify_sendgrid_event("SpamReport") is BounceType.COMPLAINT


def test_transient_smtp_status_downgrades_a_bounce_to_soft() -> None:
    """SendGrid reports both permanent and transient failures as 'bounce'."""
    result = classify_sendgrid_event("bounce", "4.2.2 mailbox full")
    assert result is BounceType.SOFT


def test_permanent_smtp_status_keeps_a_bounce_hard() -> None:
    """A 5.x.x status is a permanent failure."""
    result = classify_sendgrid_event("bounce", "5.1.1 user unknown")
    assert result is BounceType.HARD


def test_bounce_without_a_reason_defaults_to_hard() -> None:
    """Absent an SMTP status, a bounce is treated as permanent."""
    assert classify_sendgrid_event("bounce", None) is BounceType.HARD


@pytest.mark.parametrize("bounce_type", [BounceType.HARD, BounceType.COMPLAINT])
def test_hard_bounces_and_complaints_suppress(bounce_type: BounceType) -> None:
    """Permanent failures and spam reports remove the address permanently."""
    assert should_suppress(bounce_type) is True


@pytest.mark.parametrize(
    "bounce_type", [BounceType.SOFT, BounceType.BLOCKED, BounceType.UNKNOWN]
)
def test_transient_failures_do_not_suppress(bounce_type: BounceType) -> None:
    """A full mailbox or a reputation block is not an opt-out."""
    assert should_suppress(bounce_type) is False
