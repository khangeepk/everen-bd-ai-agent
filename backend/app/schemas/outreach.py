"""Pydantic v2 schemas for outreach drafting, approval, and sending."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.outreach import DraftStatus, SuppressionReason
from app.services.audit_scoring import FindingCategory
from app.services.outreach_policy import CampaignType, OutreachChannel
from app.services.reply_classification import ObjectionType
from app.services.send_limits import BounceType


class GenerateDraftsRequest(BaseModel):
    """Request to generate outreach drafts for a lead.

    Drafts are always created with ``status = pending_review``. This endpoint
    cannot send anything.
    """

    channels: list[OutreachChannel] = Field(
        default_factory=lambda: [OutreachChannel.EMAIL],
        min_length=1,
        description=(
            "Channels to draft for. WhatsApp requires recorded opt-in and will be "
            "skipped with a reason if the lead has none. LinkedIn requires a "
            "linkedin_url on file and produces text only -- see linkedin_followup_message "
            "on the response; nothing is ever sent or scraped automatically."
        ),
    )


class SkippedChannelResponse(BaseModel):
    """A channel that was requested but could not be drafted."""

    channel: OutreachChannel
    blockers: list[str]
    warnings: list[str]


class DraftResponse(BaseModel):
    """An outreach draft."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    channel: OutreachChannel
    status: DraftStatus
    subject: str | None
    body: str
    recipient_email: str | None
    recipient_phone: str | None
    sender_name: str | None
    sender_email: str | None
    sender_company: str | None
    sender_physical_address: str | None
    unsubscribe_url: str | None
    whatsapp_template_name: str | None
    linkedin_followup_message: str | None = Field(
        default=None,
        description=(
            "LINKEDIN only: the follow-up message to send after the prospect accepts "
            "the connection request in `body`. Null for every other channel."
        ),
    )
    review_warnings: str | None
    created_by_agent: str
    used_fallback: bool
    objection_type: ObjectionType | None = Field(
        default=None,
        description=(
            "Set only for a draft auto-generated in response to a classified reply "
            "objection (price/timing/not_interested_yet). Null for an ordinary "
            "cold-outreach draft."
        ),
    )
    triggering_message_id: uuid.UUID | None = Field(
        default=None,
        description="The inbound reply this draft was generated in response to, if any.",
    )
    campaign_type: CampaignType = Field(
        description=(
            "Snapshot of the lead's campaign_type at draft-generation time. See "
            "app/services/outreach_policy.py::CampaignType."
        )
    )
    follow_up_sequence: int = Field(
        description=(
            "Position in this lead's follow-up cadence: 0 = the initial outreach, "
            "1+ = a cadence-triggered follow-up. See app/services/campaign_cadence.py."
        )
    )
    prompt_version_id: uuid.UUID | None
    ab_variant: str | None
    draft_language: str | None = Field(
        default=None,
        description=(
            "BCP-47 language code the draft was written in, e.g. 'es', 'fr'. "
            "Null means English (the model default) or language not determined."
        ),
    )
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    rejected_reason: str | None
    sent_at: datetime | None
    created_at: datetime


class GenerateDraftsResponse(BaseModel):
    """Result of a draft generation request."""

    lead_id: uuid.UUID
    drafts: list[DraftResponse]
    skipped: list[SkippedChannelResponse]
    notice: str = Field(
        default=(
            "All drafts are created pending_review. Nothing is sent until a human "
            "approves the draft and calls the separate send endpoint."
        )
    )


class UpdateDraftRequest(BaseModel):
    """Reviewer edits to a draft before approval.

    Only drafts in ``pending_review`` or ``rejected`` may be edited -- an
    approved or sent draft is immutable.
    """

    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, min_length=1)
    whatsapp_template_name: str | None = Field(default=None, max_length=200)
    linkedin_followup_message: str | None = Field(
        default=None,
        min_length=1,
        description="LINKEDIN only: edit the follow-up message before copying it.",
    )


class ApproveDraftRequest(BaseModel):
    """Approval of a draft for sending.

    Approval and sending are deliberately separate steps. Approving marks the
    draft sendable; it does not dispatch it.
    """

    note: str | None = Field(
        default=None, max_length=500, description="Optional note recorded in the audit log."
    )


class RejectDraftRequest(BaseModel):
    """Rejection of a draft."""

    reason: str = Field(
        min_length=1, max_length=500, description="Why the draft was rejected. Required."
    )


class PaginatedDrafts(BaseModel):
    """A page of drafts from the approval queue."""

    items: list[DraftResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class SendResultResponse(BaseModel):
    """Outcome of a send attempt."""

    draft_id: uuid.UUID
    status: DraftStatus
    sent_at: datetime | None
    provider_message_id: str | None
    quota_remaining: int


class QuotaStatusResponse(BaseModel):
    """Current daily send quota standing."""

    channel: OutreachChannel
    quota_date: str
    limit: int
    used: int
    remaining: int
    resets_at: datetime


class AuditLogEntryResponse(BaseModel):
    """One recorded status transition."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    old_status: DraftStatus | None
    new_status: DraftStatus
    changed_by_id: uuid.UUID | None
    changed_at: datetime
    note: str | None


class BounceWebhookEvent(BaseModel):
    """One event from the email provider's webhook."""

    email: str
    event: str
    reason: str | None = None
    sg_message_id: str | None = None
    sg_event_id: str | None = None
    timestamp: int | None = None



class BounceWebhookResponse(BaseModel):
    """Summary of processed webhook events."""

    processed: int
    suppressed: int


class SuppressionEntryResponse(BaseModel):
    """A suppressed identifier."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    identifier: str
    channel: OutreachChannel
    reason: SuppressionReason
    detail: str | None
    suppressed_at: datetime


class BounceEventResponse(BaseModel):
    """A recorded delivery failure."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID | None
    identifier: str
    bounce_type: BounceType
    provider_event: str | None
    reason: str | None
    occurred_at: datetime
    suppressed: bool


class UnsubscribeResponse(BaseModel):
    """Confirmation that an opt-out was recorded."""

    message: str


class FollowUpSkipResponse(BaseModel):
    """A lead+channel combination considered for a follow-up but not drafted."""

    lead_id: uuid.UUID
    channel: OutreachChannel
    reason: str


class DetectedProblemResponse(BaseModel):
    """One audit finding shown as a detected problem in the context column."""

    category: FindingCategory
    title: str
    detail: str | None


class DraftClaimResponse(BaseModel):
    """One personalized claim in the draft, tied back to the finding it's
    grounded in -- the approval queue's "why it says this" provenance."""

    phrase: str
    source: FindingCategory
    evidence: str | None


class EnrichedLeadSummary(BaseModel):
    """Minimal lead context shown alongside a draft in the approval queue."""

    id: uuid.UUID
    name: str
    industry: str | None
    location: str | None


class EnrichedDraftResponse(BaseModel):
    """A draft joined with its lead, score, audit findings, and recommended
    service -- everything the approval-review screen needs in one call, with
    no frontend N+1 and no invented data. See GET /outreach/queue/enriched.
    """

    id: uuid.UUID
    lead: EnrichedLeadSummary
    channel: OutreachChannel
    status: DraftStatus
    subject: str | None
    body: str
    linkedin_followup_message: str | None
    review_warnings: str | None
    created_at: datetime

    #: None if this lead has never been scored.
    score: float | None
    score_reasons: list[str]
    #: None if the lead is not blocked from contact.
    compliance_state: str | None

    #: From the draft's source_audit_id, if set. Empty list if the draft
    #: isn't grounded in an audit (e.g. hand-created, or the audit was never
    #: linked) -- never fabricated findings.
    problems: list[DetectedProblemResponse]
    claims: list[DraftClaimResponse]

    #: Name of the service named by the draft's source_service_id, if set
    #: and if that Service row still exists. None otherwise -- the frontend
    #: shows "not yet matched" rather than a fake recommendation.
    recommended_service: str | None


class PaginatedEnrichedDrafts(BaseModel):
    """A page of enriched approval-queue drafts."""

    items: list[EnrichedDraftResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class FollowUpScanResponse(BaseModel):
    """Result of one on-demand scan for due campaign follow-ups."""

    scanned_leads: int
    drafts: list[DraftResponse]
    skipped: list[FollowUpSkipResponse]
    notice: str = Field(
        default=(
            "All drafts are created pending_review. Nothing is sent until a human "
            "approves the draft and calls the separate send endpoint."
        )
    )
