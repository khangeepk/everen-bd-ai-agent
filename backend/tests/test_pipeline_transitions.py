"""Tests for :mod:`app.services.pipeline_transitions` and the call-card agent.

Requires `db_session` (in-memory SQLite, see tests/conftest.py) and a running
event loop, so these run under `pytest` + `pytest-asyncio` and are not part
of the offline stdlib shim used elsewhere in this repo (no network/DB access
in this sandbox -- see master.md for the offline-testing constraint).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.call_card import CallCenterCardAgent
from app.db.models.audit import AuditFinding, AuditStatus, FindingCategory, WebsiteAudit
from app.db.models.knowledge_base import PricingModel, Service
from app.db.models.lead import Lead, LeadSource, LeadStatus
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import InboundChannel, InboundMessage
from app.services.knowledge_base import KnowledgeBaseService
from app.services.audit_scoring import Severity
from app.services.outreach_policy import OutreachChannel
from app.services.pipeline import InvalidTransitionError, PipelineStage, PipelineTransitionReason
from app.services.pipeline_transitions import (
    advance_from_classification,
    advance_on_outreach_sent,
    apply_stage_change,
)
from app.services.reply_classification import ReplyClassification, ReplyIntent


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
        "contact_name": "Jamie Rivera",
        "contact_email": "jamie@examplebakery.test",
        "contact_phone": "+15550100",
        "source": LeadSource.MANUAL,
        "confidence_score": 0.8,
    }
    defaults.update(overrides)
    return Lead(**defaults)


@pytest.mark.asyncio
async def test_apply_stage_change_records_event_and_moves_lead(
    db_session: AsyncSession,
) -> None:
    """A valid manual change updates the lead and writes one event row."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    outcome = await apply_stage_change(
        db_session, lead, PipelineStage.CONTACTED, PipelineTransitionReason.MANUAL
    )

    assert lead.pipeline_stage is PipelineStage.CONTACTED
    assert outcome.event.from_stage is PipelineStage.NEW
    assert outcome.event.to_stage is PipelineStage.CONTACTED
    assert outcome.entered_hot is False


@pytest.mark.asyncio
async def test_apply_stage_change_rejects_invalid_transition(db_session: AsyncSession) -> None:
    """An invalid jump raises rather than moving the lead."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    with pytest.raises(InvalidTransitionError):
        await apply_stage_change(
            db_session, lead, PipelineStage.HOT, PipelineTransitionReason.MANUAL
        )
    assert lead.pipeline_stage is PipelineStage.NEW


@pytest.mark.asyncio
async def test_converting_a_lead_syncs_legacy_status_to_won(db_session: AsyncSession) -> None:
    """Reaching pipeline Converted syncs the older LeadStatus field."""
    lead = _lead(pipeline_stage=PipelineStage.HOT)
    db_session.add(lead)
    await db_session.flush()

    await apply_stage_change(
        db_session, lead, PipelineStage.CONVERTED, PipelineTransitionReason.MANUAL
    )

    assert lead.status is LeadStatus.WON


@pytest.mark.asyncio
async def test_losing_a_lead_syncs_legacy_status_to_lost(db_session: AsyncSession) -> None:
    """Reaching pipeline Lost syncs the older LeadStatus field."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    await apply_stage_change(
        db_session, lead, PipelineStage.LOST, PipelineTransitionReason.SUPPRESSED
    )

    assert lead.status is LeadStatus.LOST


@pytest.mark.asyncio
async def test_advance_from_classification_book_call_enters_hot(
    db_session: AsyncSession,
) -> None:
    """A book_call reply advances a Contacted lead straight into Hot."""
    lead = _lead(pipeline_stage=PipelineStage.CONTACTED)
    db_session.add(lead)
    await db_session.flush()

    message = InboundMessage(
        lead_id=lead.id,
        channel=InboundChannel.EMAIL,
        body="Can we book a call this week?",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(message)
    await db_session.flush()

    classification = ReplyClassification(
        intent=ReplyIntent.BOOK_CALL, confidence=0.85, reasons=("test",)
    )
    outcome = await advance_from_classification(db_session, lead, message, classification)

    assert outcome is not None
    assert lead.pipeline_stage is PipelineStage.HOT
    assert outcome.entered_hot is True


@pytest.mark.asyncio
async def test_advance_from_classification_unclear_does_nothing(
    db_session: AsyncSession,
) -> None:
    """An unclear reply never auto-advances the pipeline."""
    lead = _lead(pipeline_stage=PipelineStage.CONTACTED)
    db_session.add(lead)
    await db_session.flush()

    message = InboundMessage(
        lead_id=lead.id,
        channel=InboundChannel.EMAIL,
        body="Out of office.",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(message)
    await db_session.flush()

    classification = ReplyClassification(intent=ReplyIntent.UNCLEAR, confidence=0.3)
    outcome = await advance_from_classification(db_session, lead, message, classification)

    assert outcome is None
    assert lead.pipeline_stage is PipelineStage.CONTACTED


@pytest.mark.asyncio
async def test_advance_on_outreach_sent_moves_new_to_contacted(db_session: AsyncSession) -> None:
    """Sending a draft to a brand-new lead advances it to Contacted."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    outcome = await advance_on_outreach_sent(db_session, lead)

    assert outcome is not None
    assert lead.pipeline_stage is PipelineStage.CONTACTED


@pytest.mark.asyncio
async def test_advance_on_outreach_sent_is_a_noop_past_new(db_session: AsyncSession) -> None:
    """A second send to an already-contacted lead does not re-fire."""
    lead = _lead(pipeline_stage=PipelineStage.INTERESTED)
    db_session.add(lead)
    await db_session.flush()

    outcome = await advance_on_outreach_sent(db_session, lead)

    assert outcome is None
    assert lead.pipeline_stage is PipelineStage.INTERESTED


class _FakeEmbedder:
    """Deterministic embedder so KnowledgeBaseService needs no network call."""

    async def embed(self, texts):  # noqa: ANN001, ANN201 - test double
        return [[0.0] * 8 for _ in texts]


@pytest.mark.asyncio
async def test_call_card_agent_generates_card_from_findings_and_service(
    db_session: AsyncSession,
) -> None:
    """The card assembles contact info, problems, service, script, and history."""
    lead = _lead(pipeline_stage=PipelineStage.HOT)
    db_session.add(lead)
    await db_session.flush()

    service = Service(
        name="Website Performance Tuneup",
        slug="perf-tuneup",
        category="Web",
        summary="Speed and Core Web Vitals improvements.",
        description="A longer description of the performance work.",
        price_min=Decimal("1500.00"),
        price_max=Decimal("6000.00"),
        pricing_model=PricingModel.PROJECT_RANGE,
    )
    db_session.add(service)
    await db_session.flush()

    audit = WebsiteAudit(
        lead_id=lead.id,
        url="https://examplebakery.test",
        status=AuditStatus.COMPLETED,
    )
    db_session.add(audit)
    await db_session.flush()

    finding = AuditFinding(
        audit_id=audit.id,
        code="slow_mobile_load",
        category=FindingCategory.MOBILE,
        severity=Severity.HIGH,
        title="Slow mobile load time",
        detail="The homepage takes 6.2s to load on a simulated mobile connection.",
        mapped_service_id=service.id,
    )
    db_session.add(finding)

    inbound = InboundMessage(
        lead_id=lead.id,
        channel=InboundChannel.EMAIL,
        body="Can we book a call this week?",
        received_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
        classified_intent=ReplyIntent.BOOK_CALL,
    )
    db_session.add(inbound)

    sent_draft = OutreachDraft(
        lead_id=lead.id,
        channel=OutreachChannel.EMAIL,
        status=DraftStatus.SENT,
        body="Hi Jamie, noticed your site loads slowly on mobile...",
        subject="Noticed something on your website",
        created_by_agent="outreach-draft-agent-v1",
        approved_by_id=uuid.uuid4(),
        sent_at=datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc),
    )
    db_session.add(sent_draft)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=_FakeEmbedder())
    card = await CallCenterCardAgent(db_session, kb).generate(lead, triggering_message=inbound)

    assert card.lead_id == lead.id
    assert card.contact_email == lead.contact_email
    assert "Slow mobile load time" in card.problems_summary
    assert card.recommended_service_id == service.id
    assert "Website Performance Tuneup" in (card.recommended_service_summary or "")
    assert "Can we book a call" in card.message_history_markdown
    assert "noticed your site loads slowly" in card.message_history_markdown
    assert card.call_script
    assert card.triggering_message_id == inbound.id
