"""Daily send quota arithmetic and bounce classification.

Standard library only. The DB-backed counter and suppression writes live in
:mod:`app.services.suppression`; this module holds the pure logic so quota
edge cases and bounce classification can be tested exhaustively.

Why a daily limit exists at all: sending reputation degrades sharply when a
cold domain ramps volume quickly, and mailbox providers treat sudden spikes
as a spam signal. The cap is a deliverability control first and a blast-radius
control second -- if a draft-generation bug produces bad copy, the limit
bounds how many prospects receive it before a human notices.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Quota resets at this hour UTC. Fixed rather than rolling so the counter is
#: a simple per-date row and "today's usage" is unambiguous across timezones.
QUOTA_RESET_HOUR_UTC = 0


class BounceType(str, enum.Enum):
    """How a delivery failure should be treated."""

    #: Permanent failure -- address does not exist, domain invalid. The
    #: address must be suppressed; retrying damages sender reputation.
    HARD = "hard"
    #: Temporary failure -- mailbox full, server busy. May be retried.
    SOFT = "soft"
    #: Recipient marked the message as spam. Treated as a permanent opt-out,
    #: more severe than a hard bounce.
    COMPLAINT = "complaint"
    #: Provider blocked the message, often reputation-based. Needs
    #: investigation rather than retry or suppression.
    BLOCKED = "blocked"
    #: Could not be classified from the provider payload.
    UNKNOWN = "unknown"


#: Bounce types that permanently suppress an address.
SUPPRESSING_BOUNCE_TYPES: frozenset[BounceType] = frozenset(
    {BounceType.HARD, BounceType.COMPLAINT}
)

#: SendGrid event names mapped to bounce types.
_SENDGRID_EVENT_MAP: dict[str, BounceType] = {
    "bounce": BounceType.HARD,
    "blocked": BounceType.BLOCKED,
    "dropped": BounceType.HARD,
    "spamreport": BounceType.COMPLAINT,
    "deferred": BounceType.SOFT,
}

#: SMTP status patterns indicating a permanent failure (5.x.x) versus a
#: transient one (4.x.x).
_PERMANENT_SMTP = re.compile(r"\b5\.\d\.\d\b")
_TRANSIENT_SMTP = re.compile(r"\b4\.\d\.\d\b")


class QuotaExceededError(RuntimeError):
    """Raised when a send would exceed the configured daily limit."""


@dataclass(frozen=True)
class QuotaStatus:
    """Current standing against the daily send limit.

    Attributes:
        quota_date: The UTC date this quota covers.
        limit: Maximum sends permitted for the day.
        used: Sends already made today.
        remaining: Sends still available.
        resets_at: When the quota next resets.
    """

    quota_date: date
    limit: int
    used: int
    remaining: int
    resets_at: datetime

    @property
    def exhausted(self) -> bool:
        """Whether the daily limit has been reached.

        Returns:
            True if no sends remain.
        """
        return self.remaining <= 0


def quota_date_for(moment: datetime) -> date:
    """Determine which quota day a moment falls in.

    Args:
        moment: The instant to classify. Must be timezone-aware.

    Returns:
        The UTC date of the quota window.

    Raises:
        ValueError: If ``moment`` is naive. A naive timestamp would make the
            quota boundary ambiguous across deployments.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.astimezone(timezone.utc).date()


def next_reset(moment: datetime) -> datetime:
    """Compute when the current quota window ends.

    Args:
        moment: The instant to measure from. Must be timezone-aware.

    Returns:
        The UTC instant of the next quota reset.

    Raises:
        ValueError: If ``moment`` is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")

    utc_moment = moment.astimezone(timezone.utc)
    tomorrow = utc_moment.date() + timedelta(days=1)
    return datetime.combine(
        tomorrow, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=QUOTA_RESET_HOUR_UTC)


def evaluate_quota(limit: int, used: int, moment: datetime) -> QuotaStatus:
    """Build the current quota standing.

    Args:
        limit: Configured daily send limit.
        used: Sends already made in the current window.
        moment: The instant to evaluate at. Must be timezone-aware.

    Returns:
        The quota status.

    Raises:
        ValueError: If ``limit`` is negative, ``used`` is negative, or
            ``moment`` is naive.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    if used < 0:
        raise ValueError("used must not be negative")

    return QuotaStatus(
        quota_date=quota_date_for(moment),
        limit=limit,
        used=used,
        remaining=max(limit - used, 0),
        resets_at=next_reset(moment),
    )


def check_can_send(status: QuotaStatus, count: int = 1) -> None:
    """Verify a send of ``count`` messages fits within the remaining quota.

    Args:
        status: The current quota standing.
        count: How many messages are about to be sent.

    Raises:
        ValueError: If ``count`` is not positive.
        QuotaExceededError: If the send would exceed the daily limit.
    """
    if count <= 0:
        raise ValueError("count must be positive")

    if count > status.remaining:
        raise QuotaExceededError(
            f"Daily send limit reached: {status.used}/{status.limit} used, "
            f"{status.remaining} remaining, {count} requested. "
            f"Quota resets at {status.resets_at.isoformat()}."
        )


def classify_sendgrid_event(event: str, reason: str | None = None) -> BounceType:
    """Classify a SendGrid webhook event into a bounce type.

    SendGrid reports both permanent and transient failures under the
    ``bounce`` event, distinguished only by the SMTP status in ``reason``, so
    the reason string is inspected before falling back to the event name.

    Args:
        event: The SendGrid event name, e.g. ``"bounce"``.
        reason: The provider's failure reason, if supplied.

    Returns:
        The classified bounce type.
    """
    normalized = event.strip().lower()

    if normalized == "bounce" and reason:
        if _TRANSIENT_SMTP.search(reason):
            return BounceType.SOFT
        if _PERMANENT_SMTP.search(reason):
            return BounceType.HARD

    classified = _SENDGRID_EVENT_MAP.get(normalized, BounceType.UNKNOWN)
    if classified is BounceType.UNKNOWN:
        logger.warning("Unrecognized delivery event", extra={"event": event})
    return classified


def should_suppress(bounce_type: BounceType) -> bool:
    """Whether a bounce type permanently suppresses the address.

    Args:
        bounce_type: The classified bounce.

    Returns:
        True for hard bounces and spam complaints.
    """
    return bounce_type in SUPPRESSING_BOUNCE_TYPES
