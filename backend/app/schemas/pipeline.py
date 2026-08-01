"""Pydantic v2 schemas for the CRM pipeline: stage, inbound messages, call cards."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.pipeline import InboundChannel
from app.services.pipeline import PipelineStage, PipelineTransitionReason
from app.services.reply_classification import ObjectionType, ReplyIntent


class PipelineEventResponse(BaseModel):
    """One recorded stage transition."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    from_stage: PipelineStage | None
    to_stage: PipelineStage
    reason: PipelineTransitionReason
    triggered_by_id: uuid.UUID | None
    inbound_message_id: uuid.UUID | None
    changed_at: datetime
    note: str | None


class LeadPipelineResponse(BaseModel):
    """A lead's current stage plus its transition history."""

    lead_id: uuid.UUID
    pipeline_stage: PipelineStage
    events: list[PipelineEventResponse]


class StageUpdateRequest(BaseModel):
    """Request to manually move a lead's pipeline stage."""

    to_stage: PipelineStage
    note: str | None = Field(default=None, max_length=2000)
    force: bool = Field(
        default=False,
        description=(
            "Bypass the transition graph. Restricted to approver roles -- "
            "reserved for correcting a mis-classification, not routine use."
        ),
    )


class LogMessageRequest(BaseModel):
    """Request to log an inbound message from a lead."""

    channel: InboundChannel
    body: str = Field(min_length=1, max_length=10000)
    received_at: datetime | None = Field(
        default=None,
        description="Defaults to now. Set explicitly when backfilling a delayed report.",
    )
    related_draft_id: uuid.UUID | None = None
    auto_classify: bool = Field(
        default=True,
        description="Classify the message and auto-advance the pipeline immediately.",
    )


class InboundMessageResponse(BaseModel):
    """A logged inbound message and its classification, if any."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    channel: InboundChannel
    body: str
    received_at: datetime
    related_draft_id: uuid.UUID | None
    classified_intent: ReplyIntent | None
    classification_confidence: float | None
    classification_reasons: str | None
    classified_at: datetime | None
    used_fallback: bool
    stage_change: PipelineEventResponse | None = None
    objection_type: ObjectionType | None = Field(
        default=None,
        description=(
            "Set when this reply was classified as an objection (price/timing/"
            "not_interested_yet). Not a mapped column -- attached after "
            "classification, same as stage_change."
        ),
    )
    objection_draft_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The auto-generated suggested-response draft's id, if one was created. "
            "Fetch it via GET /outreach/drafts/{id}, or find it in "
            "GET /outreach/queue like any other pending_review draft -- it is not a "
            "separate queue."
        ),
    )
    booking_draft_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The auto-generated booking-link reply draft's id, if one was created "
            "because this reply was classified book_call or interested. Same "
            "queue/fetch semantics as objection_draft_id. Null if the shared "
            "calendar isn't configured yet or the lead wasn't eligible on this "
            "channel -- see the message's classification for why."
        ),
    )


class CallCenterCardResponse(BaseModel):
    """A generated call-center briefing card."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    triggering_message_id: uuid.UUID | None
    contact_name: str | None
    contact_title: str | None
    contact_email: str | None
    contact_phone: str | None
    problems_summary: str
    recommended_service_id: uuid.UUID | None
    recommended_service_summary: str | None
    message_history_markdown: str
    call_script: str
    generated_by_agent: str
    used_fallback: bool
    created_at: datetime
