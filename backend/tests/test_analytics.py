"""Tests for :mod:`app.services.analytics`.

Requires `db_session` (in-memory SQLite, see tests/conftest.py) and
`pytest-asyncio` -- written in full but not executed in this sandbox (no
SQLAlchemy available offline; see master.md for the recurring constraint).
Run with `pytest` locally to confirm.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import EmailOpenEvent, PromptVersion
from app.db.models.knowledge_base import PricingModel, Service
from app.db.models.lead import Lead, LeadSource
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import InboundChannel, InboundMessage
from app.services.analytics import (
    get_overview,
    get_prompt_version_performance,
    get_top_industries,
    get_top_services,
)
from app.services.outreach_policy import OutreachChannel
from app.services.pipeline import PipelineStage, PipelineTransitionReason
from app.services.pipeline_transitions import apply_stage_change

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _lead(**overrides: object) -> Lead:
    """Build a valid, unsaved Lead with optional overrides.

    Args:
        **overrides: Attributes to replace.

    Returns:
        An unsaved :class:`Lead`.
    """
    defaults = {
        "name": "Example Bakery",
        "category": "food_service",
        "contact_email": "jamie@examplebakery.test",
        "source": LeadSource.MANUAL,
        "confidence_score": 0.8,
    }
    defaults.update(overrides)
    return Lead(**defaults)


def _sent_draft(lead: Lead, sent_at: datetime, **overrides: object) -> OutreachDraft:
    """Build a sent email draft.

    Args:
        lead: The lead the draft belongs to.
        sent_at: When the draft was sent.
        **overrides: Attributes to replace.

    Returns:
        An unsaved :class:`OutreachDraft`.
    """
    defaults = {
        "lead_id": lead.id,
        "channel": OutreachChannel.EMAIL,
        "status": DraftStatus.SENT,
        "body": "Hello there.",
        "created_by_agent": "outreach-draft-agent-v1",
        "approved_by_id": uuid.uuid4(),
        "sent_at": sent_at,
    }
    defaults.update(overrides)
    return OutreachDraft(**defaults)


@pytest.mark.asyncio
async def test_overview_counts_sent_emails(db_session: AsyncSession) -> None:
    """Emails sent counts only SENT email drafts with a sent_at."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    db_session.add(_sent_draft(lead, NOW))
    db_session.add(
        OutreachDraft(
            lead_id=lead.id,
            channel=OutreachChannel.EMAIL,
            status=DraftStatus.PENDING_REVIEW,
            body="Not sent yet.",
            created_by_agent="outreach-draft-agent-v1",
        )
    )
    await db_session.flush()

    overview = await get_overview(db_session)
    assert overview.emails_sent == 1


@pytest.mark.asyncio
async def test_overview_respects_date_range(db_session: AsyncSession) -> None:
    """Sends outside the requested window are excluded."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    db_session.add(_sent_draft(lead, NOW - timedelta(days=10)))
    db_session.add(_sent_draft(lead, NOW))
    await db_session.flush()

    overview = await get_overview(
        db_session, start=NOW - timedelta(days=1), end=NOW + timedelta(days=1)
    )
    assert overview.emails_sent == 1


@pytest.mark.asyncio
async def test_overview_open_rate_reflects_open_events(db_session: AsyncSession) -> None:
    """A draft with a logged open counts toward the open rate."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    opened_draft = _sent_draft(lead, NOW)
    unopened_draft = _sent_draft(lead, NOW)
    db_session.add(opened_draft)
    db_session.add(unopened_draft)
    await db_session.flush()

    db_session.add(EmailOpenEvent(draft_id=opened_draft.id, opened_at=NOW))
    await db_session.flush()

    overview = await get_overview(db_session)
    assert overview.emails_sent == 2
    assert overview.opens == 1
    assert overview.open_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_overview_reply_rate_uses_distinct_leads(db_session: AsyncSession) -> None:
    """Reply rate is distinct replied leads over distinct emailed leads."""
    replied_lead = _lead(contact_email="replied@test.example")
    silent_lead = _lead(name="Silent Co", contact_email="silent@test.example")
    db_session.add_all([replied_lead, silent_lead])
    await db_session.flush()

    db_session.add(_sent_draft(replied_lead, NOW))
    db_session.add(_sent_draft(silent_lead, NOW))
    db_session.add(
        InboundMessage(
            lead_id=replied_lead.id,
            channel=InboundChannel.EMAIL,
            body="Tell me more",
            received_at=NOW,
        )
    )
    await db_session.flush()

    overview = await get_overview(db_session)
    assert overview.replies == 1
    assert overview.reply_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_overview_counts_meetings_and_deals(db_session: AsyncSession) -> None:
    """Meetings booked and deals won come from pipeline transitions."""
    hot_lead = _lead(contact_email="hot@test.example", pipeline_stage=PipelineStage.INTERESTED)
    won_lead = _lead(
        name="Won Co", contact_email="won@test.example", pipeline_stage=PipelineStage.HOT
    )
    db_session.add_all([hot_lead, won_lead])
    await db_session.flush()

    await apply_stage_change(
        db_session,
        hot_lead,
        PipelineStage.MEETING_BOOKED,
        PipelineTransitionReason.REPLY_CLASSIFIED,
    )
    await apply_stage_change(
        db_session, won_lead, PipelineStage.CONVERTED, PipelineTransitionReason.MANUAL
    )

    overview = await get_overview(db_session)
    assert overview.meetings_booked == 1
    assert overview.deals_won == 1


@pytest.mark.asyncio
async def test_top_industries_ranks_by_won_deals(db_session: AsyncSession) -> None:
    """Only converted leads' categories count toward the ranking."""
    won = _lead(
        category="retail",
        contact_email="won@test.example",
        pipeline_stage=PipelineStage.HOT,
    )
    not_won = _lead(
        name="Other Co",
        category="legal",
        contact_email="notwon@test.example",
    )
    db_session.add_all([won, not_won])
    await db_session.flush()

    await apply_stage_change(
        db_session, won, PipelineStage.CONVERTED, PipelineTransitionReason.MANUAL
    )

    items = await get_top_industries(db_session)
    assert [i.label for i in items] == ["retail"]


@pytest.mark.asyncio
async def test_top_services_ranks_by_won_deals(db_session: AsyncSession) -> None:
    """Services tied to a won lead's sent drafts are ranked."""
    won = _lead(contact_email="won@test.example", pipeline_stage=PipelineStage.HOT)
    db_session.add(won)
    await db_session.flush()

    service = Service(
        name="Website Performance Tuneup",
        slug="perf-tuneup",
        category="Web",
        summary="Speed improvements.",
        description="A longer description.",
        pricing_model=PricingModel.PROJECT_RANGE,
    )
    db_session.add(service)
    await db_session.flush()

    db_session.add(_sent_draft(won, NOW, source_service_id=service.id))
    await db_session.flush()

    await apply_stage_change(
        db_session, won, PipelineStage.CONVERTED, PipelineTransitionReason.MANUAL
    )

    items = await get_top_services(db_session)
    assert [i.label for i in items] == ["Website Performance Tuneup"]


@pytest.mark.asyncio
async def test_prompt_version_performance_groups_by_version_and_variant(
    db_session: AsyncSession,
) -> None:
    """Drafts with no prompt_version_id bucket under the code-default label."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    version = PromptVersion(
        agent_name="outreach-draft-agent-v1",
        channel=OutreachChannel.EMAIL,
        label="v2-shorter-subject",
        prompt_text="Write a shorter email.",
        is_active=True,
    )
    db_session.add(version)
    await db_session.flush()

    default_draft = _sent_draft(lead, NOW)
    versioned_draft = _sent_draft(
        lead, NOW, prompt_version_id=version.id, ab_variant="v2-shorter-subject"
    )
    db_session.add_all([default_draft, versioned_draft])
    await db_session.flush()

    db_session.add(EmailOpenEvent(draft_id=versioned_draft.id, opened_at=NOW))
    await db_session.flush()

    results = await get_prompt_version_performance(db_session)
    labels = {r.label: r for r in results}
    assert "(code-default prompt)" in labels
    assert labels["(code-default prompt)"].sent == 1
    versioned_label = next(k for k in labels if k != "(code-default prompt)")
    assert labels[versioned_label].opened == 1
