"""Outreach ORM models.

HUMAN-APPROVAL GATE -- read before changing anything here.

AGENTS.md section 8 is non-negotiable: no email, WhatsApp message, or any
external outreach may be sent without explicit human approval. The schema
enforces the workflow rather than relying on callers to remember it:

* Every draft is created with ``status = PENDING_REVIEW``. There is no code
  path that constructs one in any other state.
* ``approved_by`` and ``approved_at`` are null until a human approves, and
  the send endpoint verifies ``status == APPROVED`` before dispatch.
* ``sent_at`` is set only after the provider confirms delivery acceptance.
* Every status transition is written to :class:`OutreachAuditLog`, so the
  question "who approved this and when" always has an answer.

Celery tasks must never move a draft to APPROVED or call the send path.
They may only process sends a human has already approved through the API.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EncryptedString
from app.services.outreach_policy import CampaignType, OutreachChannel
from app.services.reply_classification import ObjectionType
from app.services.send_limits import BounceType


class DraftStatus(str, enum.Enum):
    """Lifecycle of an outreach draft.

    The only transition that permits sending is ``APPROVED -> SENT``, and
    only through the dedicated send endpoint.

    ``PAUSED`` is set by the automated SendGrid health-monitor webhook when a
    sending domain's bounce rate, spam-complaint rate, or open-rate trend
    crosses a configured threshold. A human must explicitly un-pause (by
    moving the draft back to ``PENDING_REVIEW`` through the approval UI)
    before any send can proceed. Celery tasks must never un-pause automatically.
    """

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    FAILED = "failed"
    PAUSED = "paused"


#: Statuses from which a draft may still be edited by a reviewer.
#: PAUSED is included so a human can restore a paused draft to PENDING_REVIEW.
EDITABLE_STATUSES: frozenset[DraftStatus] = frozenset(
    {DraftStatus.PENDING_REVIEW, DraftStatus.REJECTED, DraftStatus.PAUSED}
)

#: Terminal statuses. A draft in one of these may not transition further.
#: PAUSED is intentionally NOT terminal -- it is recoverable by a human.
TERMINAL_STATUSES: frozenset[DraftStatus] = frozenset({DraftStatus.SENT})


class SuppressionReason(str, enum.Enum):
    """Why an address or number was suppressed."""

    UNSUBSCRIBED = "unsubscribed"
    HARD_BOUNCE = "hard_bounce"
    SPAM_COMPLAINT = "spam_complaint"
    MANUAL = "manual"
    LEGAL_REQUEST = "legal_request"


class OutreachDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A drafted outreach message awaiting human review.

    Carries the fields AGENTS.md section 8.2 requires, plus the CAN-SPAM
    sender identification snapshot taken at draft time so an approved message
    cannot silently lose its compliance footer if configuration changes
    between approval and send.
    """

    __tablename__ = "outreach_drafts"
    __table_args__ = (
        CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)",
            name="ck_outreach_drafts_sent_at_matches_status",
        ),
        CheckConstraint(
            "approved_at IS NULL OR approved_by_id IS NOT NULL",
            name="ck_outreach_drafts_approval_is_attributed",
        ),
        CheckConstraint(
            "status <> 'sent' OR approved_by_id IS NOT NULL",
            name="ck_outreach_drafts_sent_requires_approver",
        ),
        Index("ix_outreach_drafts_status_created", "status", "created_at"),
        Index("ix_outreach_drafts_lead", "lead_id"),
        Index("ix_outreach_drafts_triggering_message", "triggering_message_id"),
        #: Backs app.services.campaign_followup_scanner's lookup of each
        #: lead's most recent sent draft per channel, to find last_sent_at
        #: and follow_up_sequence without scanning every draft ever created.
        Index("ix_outreach_drafts_lead_channel_sent", "lead_id", "channel", "sent_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[OutreachChannel] = mapped_column(
        SAEnum(OutreachChannel, name="outreach_channel"), nullable=False
    )
    status: Mapped[DraftStatus] = mapped_column(
        SAEnum(DraftStatus, name="draft_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=DraftStatus.PENDING_REVIEW,
    )

    # Content. subject is null for WhatsApp and call scripts.
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Recipient snapshot, so a later edit to the lead cannot redirect an
    #: already-approved message to a different address.
    #: Encrypted at rest (Fernet). Not queried by equality anywhere in this
    #: codebase, so no blind-index companion is needed here -- contrast with
    #: Lead.contact_email_hash in app/db/models/lead.py.
    recipient_email: Mapped[str | None] = mapped_column(EncryptedString(320), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(EncryptedString(50), nullable=True)

    # CAN-SPAM sender identification, snapshotted at draft time.
    sender_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sender_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_physical_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unsubscribe_url: Mapped[str | None] = mapped_column(String(600), nullable=True)

    #: Meta-approved template identifier. Required before a WhatsApp draft
    #: may be sent, since business-initiated messages must use one.
    whatsapp_template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: LINKEDIN only. A LinkedIn draft is two independent pieces of text
    #: sent at two different times -- the connection-request note (which
    #: lives in `body`, like every other channel's primary text) and this
    #: follow-up message, sent manually by the rep only after the prospect
    #: accepts the connection. Null for every other channel. See
    #: app/agents/outreach.py::OutreachDraftAgent.generate_linkedin_content.
    linkedin_followup_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Warnings surfaced to the reviewer, newline-joined. Not blockers -- a
    #: draft with warnings still reaches the queue, deliberately, so a human
    #: makes the call.
    review_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Grounding: which findings and services the draft was built from.
    source_audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("website_audits.id", ondelete="SET NULL"), nullable=True
    )
    source_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    #: Set only for a draft auto-generated by
    #: app.services.objection_response_scanner in response to a classified
    #: reply objection (price/timing/not_interested_yet). Null for every
    #: ordinary cold-outreach draft. See
    #: app.services.reply_classification.ObjectionType --
    #: classify_objection() never returns a value for a hard opt-out, so a
    #: value here can never correspond to one.
    objection_type: Mapped[ObjectionType | None] = mapped_column(
        SAEnum(ObjectionType, name="objection_type"), nullable=True
    )
    #: The inbound reply this draft was generated in response to, if any.
    #: Mirrors CallCenterCard.triggering_message_id. Used by the objection
    #: scanner to avoid generating a second draft for the same message on
    #: re-classification.
    triggering_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_messages.id", ondelete="SET NULL"), nullable=True
    )

    #: The prompt version used to generate this draft's body, if a
    #: `PromptVersion` row was active for this agent+channel at generation
    #: time. Null means the code-default hardcoded prompt was used (see
    #: app/agents/outreach.py). Enables comparing performance old vs. new
    #: prompt, and per-variant when this draft was part of an A/B split.
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), nullable=True
    )
    #: Which side of an A/B split this draft was assigned to (e.g. "A"/"B"),
    #: null if no experiment was running for this agent+channel at the time.
    ab_variant: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: BCP-47 language the draft body was written in (e.g. 'es', 'fr'). Null
    #: means English (the model's natural default) or language not determined.
    #: Snapshotted from the lead's effective_language at generation time so
    #: analytics queries never need to join back to the lead -- the lead's
    #: language may be updated by detection after the draft is created.
    draft_language: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)

    #: Snapshot of Lead.campaign_type at draft-generation time (see
    #: app/services/outreach_policy.py::CampaignType). Snapshotted rather
    #: than read live from the lead for the same reason recipient_email is
    #: snapshotted above: a later change to the lead's campaign_type must
    #: not retroactively change what an already-created draft is attributed
    #: to in analytics (app.services.analytics::get_campaign_performance).
    campaign_type: Mapped[CampaignType] = mapped_column(
        SAEnum(CampaignType, name="campaign_type"),
        nullable=False,
        default=CampaignType.COLD,
    )
    #: Position of this draft in its lead's follow-up cadence for
    #: campaign_type (see app/services/campaign_cadence.py). 0 = the initial
    #: outreach for this lead under this campaign type; 1 = the first
    #: cadence-triggered follow-up; 2 = the second; and so on. Set by
    #: app.services.campaign_followup_scanner, never by a human -- it exists
    #: purely so the scanner knows how far along a lead's cadence is without
    #: re-deriving it from draft history on every scan.
    follow_up_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by_agent: Mapped[str] = mapped_column(String(100), nullable=False)
    used_fallback: Mapped[bool] = mapped_column(nullable=False, default=False)

    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Provider message id, for correlating bounce webhooks back to the draft.
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    audit_entries: Mapped[list["OutreachAuditLog"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )

    def is_sendable(self) -> bool:
        """Whether this draft may be dispatched right now.

        Note:
            A True result is necessary but not sufficient -- the send
            endpoint additionally re-checks suppression, the daily quota, and
            CAN-SPAM validity immediately before dispatch.

        Returns:
            True only when a human has approved the draft and it has not
            already been sent.
        """
        return (
            self.status is DraftStatus.APPROVED
            and self.approved_by_id is not None
            and self.sent_at is None
        )


class OutreachAuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable record of one status transition on a draft.

    Required by AGENTS.md section 8.5. Rows are only ever inserted; nothing
    in the codebase updates or deletes them.
    """

    __tablename__ = "outreach_audit_log"
    __table_args__ = (
        Index("ix_outreach_audit_log_draft_changed", "draft_id", "changed_at"),
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outreach_drafts.id", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[DraftStatus | None] = mapped_column(
        SAEnum(DraftStatus, name="draft_status"), nullable=True
    )
    new_status: Mapped[DraftStatus] = mapped_column(
        SAEnum(DraftStatus, name="draft_status"), nullable=False
    )
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    draft: Mapped["OutreachDraft"] = relationship(back_populates="audit_entries")


class SuppressionEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A permanently suppressed email address or phone number.

    Suppression never expires. CAN-SPAM opt-outs are indefinite unless the
    recipient later opts back in explicitly, so there is deliberately no
    ``expires_at`` column and no bulk-delete path.

    The address/phone is encrypted at rest via EncryptedString. Lookups use
    ``identifier_hash`` (an HMAC-SHA256 blind index computed with
    app.services.pii.blind_index).
    """

    __tablename__ = "suppression_entries"
    __table_args__ = (
        UniqueConstraint("identifier_hash", name="uq_suppression_entries_identifier_hash"),
        Index("ix_suppression_entries_reason", "reason"),
        Index("ix_suppression_entries_hash", "identifier_hash"),
    )

    #: Encrypted lowercased email address or normalized phone number.
    identifier: Mapped[str] = mapped_column(EncryptedString(320), nullable=False)
    #: Blind index hash backing equality lookups and unique constraint.
    identifier_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[OutreachChannel] = mapped_column(
        SAEnum(OutreachChannel, name="outreach_channel"), nullable=False
    )
    reason: Mapped[SuppressionReason] = mapped_column(
        SAEnum(SuppressionReason, name="suppression_reason"), nullable=False
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outreach_drafts.id", ondelete="SET NULL"), nullable=True
    )
    suppressed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BounceEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A delivery failure reported by the email provider."""

    __tablename__ = "bounce_events"
    __table_args__ = (
        Index("ix_bounce_events_identifier_hash_occurred", "identifier_hash", "occurred_at"),
    )

    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outreach_drafts.id", ondelete="SET NULL"), nullable=True
    )
    #: Encrypted address or phone number.
    identifier: Mapped[str] = mapped_column(EncryptedString(320), nullable=False)
    #: Blind index hash for querying.
    identifier_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    bounce_type: Mapped[BounceType] = mapped_column(
        SAEnum(BounceType, name="bounce_type"), nullable=False
    )
    provider_event: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suppressed: Mapped[bool] = mapped_column(nullable=False, default=False)


class ProcessedWebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable record of a processed provider webhook event ID for idempotency."""

    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_processed_webhook_events_event_id"),
        Index("ix_processed_webhook_events_provider_event", "provider", "event_id"),
    )

    event_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="sendgrid")
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailySendCounter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sends made on one UTC date, for daily-limit enforcement."""

    __tablename__ = "daily_send_counters"
    __table_args__ = (
        UniqueConstraint("quota_date", "channel", name="uq_daily_send_counters_date_channel"),
        CheckConstraint("sent_count >= 0", name="ck_daily_send_counters_count_nonneg"),
    )

    quota_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    channel: Mapped[OutreachChannel] = mapped_column(
        SAEnum(OutreachChannel, name="outreach_channel"), nullable=False
    )
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

