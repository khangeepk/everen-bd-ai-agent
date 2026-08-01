"""Suppression list and daily send quota persistence.

Suppression is permanent. CAN-SPAM requires opt-out requests be honoured
indefinitely -- an unsubscribe never expires unless the recipient explicitly
opts back in -- so this module offers :func:`suppress` and :func:`is_suppressed`
but deliberately provides no unsuppress or bulk-clear function. Removing an
entry requires a manual, deliberate database operation.

See AGENTS.md section 8.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.outreach import (
    BounceEvent,
    DailySendCounter,
    SuppressionEntry,
    SuppressionReason,
)
from app.services.outreach_policy import OutreachChannel
from app.services.pii import blind_index
from app.services.send_limits import BounceType, QuotaStatus, evaluate_quota, should_suppress

logger = logging.getLogger(__name__)

_NON_PHONE_CHARS = re.compile(r"[^0-9+]")


def normalize_identifier(identifier: str, channel: OutreachChannel) -> str:
    """Normalize an address or phone number for suppression matching.

    Args:
        identifier: Raw email address or phone number.
        channel: Which channel the identifier belongs to.

    Returns:
        The normalized identifier -- lowercased for email, digits and a
        leading ``+`` only for phone numbers.

    Raises:
        ValueError: If nothing usable remains after normalization.
    """
    trimmed = identifier.strip()
    if channel is OutreachChannel.EMAIL:
        normalized = trimmed.lower()
    else:
        normalized = _NON_PHONE_CHARS.sub("", trimmed)

    if not normalized:
        raise ValueError(f"identifier {identifier!r} normalizes to nothing usable")
    return normalized


async def is_suppressed(db: AsyncSession, identifier: str, channel: OutreachChannel) -> bool:
    """Check whether an identifier is on the suppression list.

    Args:
        db: Active database session.
        identifier: Email address or phone number.
        channel: Which channel to check.

    Returns:
        True if the identifier is suppressed.
    """
    try:
        normalized = normalize_identifier(identifier, channel)
        ident_hash = blind_index(normalized, purpose="suppression_identifier")
    except ValueError:
        return False

    result = await db.execute(
        select(SuppressionEntry.id).where(SuppressionEntry.identifier_hash == ident_hash).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def suppress(
    db: AsyncSession,
    identifier: str,
    channel: OutreachChannel,
    reason: SuppressionReason,
    detail: str | None = None,
    source_draft_id=None,
) -> SuppressionEntry | None:
    """Add an identifier to the permanent suppression list.

    Idempotent -- suppressing an already-suppressed identifier is a no-op
    rather than an error, since bounce webhooks can be delivered more than
    once.

    Args:
        db: Active database session.
        identifier: Email address or phone number.
        channel: Which channel the identifier belongs to.
        reason: Why suppression is being applied.
        detail: Optional free-text context.
        source_draft_id: The draft that triggered suppression, if any.

    Returns:
        The created entry, or None if the identifier was already suppressed.

    Raises:
        ValueError: If the identifier normalizes to nothing usable.
    """
    normalized = normalize_identifier(identifier, channel)
    ident_hash = blind_index(normalized, purpose="suppression_identifier")

    existing = (
        await db.execute(
            select(SuppressionEntry).where(SuppressionEntry.identifier_hash == ident_hash)
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("Identifier already suppressed", extra={"reason": reason.value})
        return None

    entry = SuppressionEntry(
        identifier=normalized,
        identifier_hash=ident_hash,
        channel=channel,
        reason=reason,
        detail=detail,
        source_draft_id=source_draft_id,
        suppressed_at=utcnow(),
    )
    db.add(entry)
    await db.flush()

    logger.info(
        "Identifier suppressed",
        extra={"channel": channel.value, "reason": reason.value},
    )
    return entry


async def suppress_lead(
    db: AsyncSession, lead: Lead, reason: SuppressionReason, detail: str | None = None
) -> None:
    """Suppress a lead's contact channels and set its do-not-contact flag.

    Setting ``do_not_contact`` here is what makes suppression flow through to
    the scoring engine's compliance gate, so a suppressed lead can never
    surface as Hot afterwards.

    Args:
        db: Active database session.
        lead: The lead to suppress.
        reason: Why suppression is being applied.
        detail: Optional free-text context.
    """
    if lead.contact_email:
        await suppress(db, lead.contact_email, OutreachChannel.EMAIL, reason, detail)
    if lead.contact_phone:
        await suppress(db, lead.contact_phone, OutreachChannel.WHATSAPP, reason, detail)

    lead.do_not_contact = True
    lead.do_not_contact_reason = detail or f"Suppressed: {reason.value}"
    await db.flush()

    logger.info(
        "Lead suppressed and flagged do-not-contact",
        extra={"lead_id": str(lead.id), "reason": reason.value},
    )


async def has_hard_bounced(db: AsyncSession, identifier: str) -> bool:
    """Check whether an address has previously hard-bounced.

    Args:
        db: Active database session.
        identifier: Email address.

    Returns:
        True if a hard bounce or spam complaint is on record.
    """
    try:
        normalized = normalize_identifier(identifier, OutreachChannel.EMAIL)
        ident_hash = blind_index(normalized, purpose="suppression_identifier")
    except ValueError:
        return False

    result = await db.execute(
        select(BounceEvent.id)
        .where(
            BounceEvent.identifier_hash == ident_hash,
            BounceEvent.bounce_type.in_([BounceType.HARD, BounceType.COMPLAINT]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def record_bounce(
    db: AsyncSession,
    identifier: str,
    bounce_type: BounceType,
    provider_event: str | None = None,
    provider_message_id: str | None = None,
    reason: str | None = None,
    draft_id=None,
    occurred_at: datetime | None = None,
) -> BounceEvent:
    """Record a delivery failure, suppressing the address when warranted.

    Args:
        db: Active database session.
        identifier: The bouncing email address.
        bounce_type: The classified bounce type.
        provider_event: Raw provider event name.
        provider_message_id: Provider message id, for correlation.
        reason: Provider failure reason.
        draft_id: The draft that bounced, if known.
        occurred_at: When the bounce occurred. Defaults to now.

    Returns:
        The recorded event.
    """
    normalized = normalize_identifier(identifier, OutreachChannel.EMAIL)
    ident_hash = blind_index(normalized, purpose="suppression_identifier")
    suppressing = should_suppress(bounce_type)

    event = BounceEvent(
        draft_id=draft_id,
        identifier=normalized,
        identifier_hash=ident_hash,
        bounce_type=bounce_type,
        provider_event=provider_event,
        provider_message_id=provider_message_id,
        reason=reason,
        occurred_at=occurred_at or utcnow(),
        suppressed=suppressing,
    )

    db.add(event)

    if suppressing:
        suppression_reason = (
            SuppressionReason.SPAM_COMPLAINT
            if bounce_type is BounceType.COMPLAINT
            else SuppressionReason.HARD_BOUNCE
        )
        await suppress(
            db,
            normalized,
            OutreachChannel.EMAIL,
            suppression_reason,
            detail=reason,
            source_draft_id=draft_id,
        )

    await db.flush()
    logger.info(
        "Bounce recorded",
        extra={"bounce_type": bounce_type.value, "suppressed": suppressing},
    )
    return event


async def get_quota_status(
    db: AsyncSession, channel: OutreachChannel, limit: int, moment: datetime | None = None
) -> QuotaStatus:
    """Read the current daily send standing for a channel.

    Args:
        db: Active database session.
        channel: The channel to check.
        limit: Configured daily limit.
        moment: The instant to evaluate at. Defaults to now.

    Returns:
        The quota status.
    """
    now = moment or utcnow()
    quota_day = now.date()

    counter = (
        await db.execute(
            select(DailySendCounter).where(
                DailySendCounter.quota_date == quota_day,
                DailySendCounter.channel == channel,
            )
        )
    ).scalar_one_or_none()

    return evaluate_quota(limit=limit, used=counter.sent_count if counter else 0, moment=now)


async def increment_send_counter(
    db: AsyncSession, channel: OutreachChannel, moment: datetime | None = None
) -> None:
    """Record one send against today's quota.

    Uses an atomic upsert so concurrent sends cannot both read a stale count
    and each write ``count + 1``, which would let the daily limit be exceeded
    under load.

    Args:
        db: Active database session.
        channel: The channel sent on.
        moment: The instant of the send. Defaults to now.
    """
    now = moment or utcnow()
    quota_day = now.date()

    statement = pg_insert(DailySendCounter).values(
        quota_date=quota_day, channel=channel, sent_count=1
    )
    statement = statement.on_conflict_do_update(
        index_elements=[DailySendCounter.quota_date, DailySendCounter.channel],
        set_={"sent_count": DailySendCounter.sent_count + 1},
    )
    await db.execute(statement)
    await db.flush()

    logger.info("Send counter incremented", extra={"channel": channel.value})
