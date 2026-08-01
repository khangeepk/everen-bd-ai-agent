"""CRM pipeline ORM models: stage history, inbound messages, call-center cards.

See app/services/pipeline.py for the stage machine these tables record the
output of, and app/services/reply_classification.py for the classification
categories on InboundMessage.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EncryptedString
from app.services.pipeline import PipelineStage, PipelineTransitionReason
from app.services.reply_classification import ReplyIntent


class InboundChannel(str, enum.Enum):
    """Where an inbound message arrived from.

    Deliberately separate from
    :class:`app.services.outreach_policy.OutreachChannel` -- an inbound
    WhatsApp reply can arrive from a lead who opted in through a channel other
    than one of our outbound drafts, and a phone conversation is logged as a
    rep's written summary, not a transmitted message.
    """

    EMAIL = "email"
    WHATSAPP = "whatsapp"
    PHONE_NOTE = "phone_note"


class PipelineEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One recorded pipeline stage transition.

    Insert-only, like :class:`app.db.models.outreach.OutreachAuditLog` -- a
    lead's path through the pipeline is a fact of record, not something
    later edited.
    """

    __tablename__ = "pipeline_events"
    __table_args__ = (
        Index("ix_pipeline_events_lead_changed", "lead_id", "changed_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    from_stage: Mapped[PipelineStage | None] = mapped_column(
        SAEnum(PipelineStage, name="pipeline_stage"), nullable=True
    )
    to_stage: Mapped[PipelineStage] = mapped_column(
        SAEnum(PipelineStage, name="pipeline_stage"), nullable=False
    )
    reason: Mapped[PipelineTransitionReason] = mapped_column(
        SAEnum(PipelineTransitionReason, name="pipeline_transition_reason"), nullable=False
    )
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_messages.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class InboundMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reply or logged contact received from a lead."""

    __tablename__ = "inbound_messages"
    __table_args__ = (
        CheckConstraint(
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0.0 AND classification_confidence <= 1.0)",
            name="ck_inbound_messages_confidence_range",
        ),
        Index("ix_inbound_messages_lead_received", "lead_id", "received_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[InboundChannel] = mapped_column(
        SAEnum(InboundChannel, name="inbound_channel"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: The outreach draft this is a reply to, if known.
    related_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outreach_drafts.id", ondelete="SET NULL"), nullable=True
    )
    #: Who logged this message -- the rep for a phone note, null for an
    #: inbound email/WhatsApp reply ingested automatically.
    logged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    classified_intent: Mapped[ReplyIntent | None] = mapped_column(
        SAEnum(ReplyIntent, name="reply_intent"), nullable=True
    )
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classified_by_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_fallback: Mapped[bool] = mapped_column(nullable=False, default=False)


class CallCenterCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A generated briefing for the call-center rep on a Hot lead.

    Generated automatically when a lead enters :attr:`PipelineStage.HOT` (see
    app/services/pipeline_transitions.py) and regenerable on demand. A new
    row is created each time rather than updating one in place, so a rep can
    see what the card looked like at the point it was acted on even if the
    lead's audit or contact info changes later.
    """

    __tablename__ = "call_center_cards"
    __table_args__ = (
        Index("ix_call_center_cards_lead_generated", "lead_id", "created_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    triggering_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_messages.id", ondelete="SET NULL"), nullable=True
    )

    # Contact snapshot, taken at generation time (encrypted at rest).
    contact_name: Mapped[str | None] = mapped_column(EncryptedString(200), nullable=True)
    contact_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(EncryptedString(320), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(EncryptedString(50), nullable=True)

    problems_summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    recommended_service_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    message_history_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    call_script: Mapped[str] = mapped_column(Text, nullable=False)

    generated_by_agent: Mapped[str] = mapped_column(Text, nullable=False)
    used_fallback: Mapped[bool] = mapped_column(nullable=False, default=False)
