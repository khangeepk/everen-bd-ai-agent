"""Tests for PII encryption closure and suppression blind indexing (Phase 25).

Coverage:
* test_call_center_card_encrypted_fields_round_trip — Encrypted contact_email, contact_phone, contact_name on CallCenterCard
* test_suppression_entry_encrypted_round_trip       — Encrypted identifier and blind index matching on SuppressionEntry
* test_is_suppressed_case_and_whitespace_matching   — Normalization and blind index matching
* test_previously_suppressed_address_remains_blocked— Regression test proving suppression gate works after encryption closure
"""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.pipeline import CallCenterCard
from app.db.models.outreach import SuppressionEntry, SuppressionReason
from app.services.outreach_policy import OutreachChannel
from app.services.pii import blind_index
from app.services.suppression import is_suppressed, suppress


@pytest.mark.asyncio
async def test_call_center_card_encrypted_fields_round_trip(db_session: AsyncSession) -> None:
    """CallCenterCard contact_name, contact_email, and contact_phone should be encrypted at rest and decrypted on read."""
    from sqlalchemy import select

    card = CallCenterCard(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        contact_name="Alice Smith",
        contact_email="alice@company.com",
        contact_phone="+15551234567",
        problems_summary="Lacks SSL and contact form",
        message_history_markdown="Initial outreach sent",
        call_script="Hello Alice...",
        generated_by_agent="call-center-agent-v1",
    )
    db_session.add(card)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(CallCenterCard).where(CallCenterCard.id == card.id))
    ).scalar_one()

    assert fetched is not None
    assert fetched.contact_name == "Alice Smith"
    assert fetched.contact_email == "alice@company.com"
    assert fetched.contact_phone == "+15551234567"


@pytest.mark.asyncio
async def test_suppression_entry_encrypted_round_trip(db_session: AsyncSession) -> None:
    """SuppressionEntry identifier should be encrypted at rest and queryable via identifier_hash blind index."""
    from sqlalchemy import select

    raw_email = "optout-user@example.com"
    entry = await suppress(
        db_session,
        identifier=raw_email,
        channel=OutreachChannel.EMAIL,
        reason=SuppressionReason.UNSUBSCRIBED,
        detail="User clicked unsubscribe link",
    )
    assert entry is not None
    await db_session.flush()

    fetched = (
        await db_session.execute(select(SuppressionEntry).where(SuppressionEntry.id == entry.id))
    ).scalar_one()

    assert fetched is not None
    assert fetched.identifier == "optout-user@example.com"
    assert fetched.identifier_hash == blind_index("optout-user@example.com", purpose="suppression_identifier")



@pytest.mark.asyncio
async def test_is_suppressed_case_and_whitespace_matching(db_session: AsyncSession) -> None:
    """is_suppressed should match regardless of casing or surrounding whitespace."""
    raw_email = "  SpamTrap@Domain.COM  "
    await suppress(
        db_session,
        identifier=raw_email,
        channel=OutreachChannel.EMAIL,
        reason=SuppressionReason.SPAM_COMPLAINT,
    )
    await db_session.flush()

    # Test variations
    assert await is_suppressed(db_session, "spamtrap@domain.com", OutreachChannel.EMAIL) is True
    assert await is_suppressed(db_session, "SPAMTRAP@DOMAIN.COM", OutreachChannel.EMAIL) is True
    assert await is_suppressed(db_session, "  spamtrap@domain.com  ", OutreachChannel.EMAIL) is True
    assert await is_suppressed(db_session, "other@domain.com", OutreachChannel.EMAIL) is False


@pytest.mark.asyncio
async def test_previously_suppressed_address_remains_blocked(db_session: AsyncSession) -> None:
    """Regression test: previously suppressed address must remain blocked by is_suppressed."""
    email = "blocked-prospect@target-corp.com"
    await suppress(
        db_session,
        identifier=email,
        channel=OutreachChannel.EMAIL,
        reason=SuppressionReason.HARD_BOUNCE,
    )
    await db_session.flush()

    # Verify lookups against the blind index hash return True
    blocked = await is_suppressed(db_session, email, OutreachChannel.EMAIL)
    assert blocked is True
