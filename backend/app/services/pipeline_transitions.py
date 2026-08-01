"""DB-aware CRM pipeline orchestration.

Wraps the pure stage machine in app/services/pipeline.py with the effects a
real transition has: recording a `PipelineEvent`, updating `Lead.pipeline_stage`,
syncing the older `Lead.status` field at the terminal edges, and triggering
call-center card generation when a lead enters Hot.

Kept separate from app/agents/call_card.py so "did this transition happen" and
"what does the resulting card look like" can be reasoned about and tested
independently -- a card-generation failure must never silently prevent the
stage change itself from being recorded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.lead import Lead, LeadStatus
from app.db.models.pipeline import InboundMessage, PipelineEvent
from app.services.pipeline import (
    InvalidTransitionError,
    PipelineStage,
    PipelineTransitionReason,
    TransitionResult,
    next_stage_towards,
    target_stage_for_intent,
    validate_transition,
)
from app.services.reply_classification import ReplyClassification

logger = logging.getLogger(__name__)

#: Terminal `Lead.status` values kept in sync when the pipeline reaches its
#: own terminal stages. Only these two edges are synced -- open pipeline
#: stages do not attempt to guess an equivalent `LeadStatus`, since the two
#: fields track different things (see the docstring on Lead.pipeline_stage).
_TERMINAL_STATUS_SYNC: dict[PipelineStage, LeadStatus] = {
    PipelineStage.CONVERTED: LeadStatus.WON,
    PipelineStage.LOST: LeadStatus.LOST,
}


@dataclass
class StageChangeOutcome:
    """The result of applying a pipeline stage change to a lead.

    Attributes:
        transition: The validated transition that was applied.
        event: The persisted `PipelineEvent` row.
        entered_hot: True if this change is what put the lead into Hot --
            the caller should trigger call-card generation when this is True.
    """

    transition: TransitionResult
    event: PipelineEvent
    entered_hot: bool


async def apply_stage_change(
    db: AsyncSession,
    lead: Lead,
    to_stage: PipelineStage,
    reason: PipelineTransitionReason,
    *,
    triggered_by_id: uuid.UUID | None = None,
    inbound_message_id: uuid.UUID | None = None,
    note: str | None = None,
    force: bool = False,
) -> StageChangeOutcome:
    """Validate and apply a pipeline stage change to a lead.

    Args:
        db: Active database session. The caller is responsible for commit.
        lead: The lead to move. Mutated in place.
        to_stage: The proposed new stage.
        reason: Why the change is happening.
        triggered_by_id: The user who triggered a manual/forced change, if any.
        inbound_message_id: The inbound message that triggered a
            reply-classified change, if any.
        note: Free-text context for the event log.
        force: Bypass the transition graph (approver-level correction only).

    Returns:
        The outcome, including whether this change entered Hot.

    Raises:
        InvalidTransitionError: If the transition is not permitted and
            ``force`` is False.
    """
    from_stage = lead.pipeline_stage
    result = validate_transition(from_stage, to_stage, reason, force=force)

    lead.pipeline_stage = result.to_stage
    synced_status = _TERMINAL_STATUS_SYNC.get(result.to_stage)
    if synced_status is not None:
        lead.status = synced_status

    event = PipelineEvent(
        lead_id=lead.id,
        from_stage=result.from_stage,
        to_stage=result.to_stage,
        reason=result.reason,
        triggered_by_id=triggered_by_id,
        inbound_message_id=inbound_message_id,
        changed_at=utcnow(),
        note=note,
    )
    db.add(event)
    await db.flush()

    logger.info(
        "Pipeline stage change applied",
        extra={
            "lead_id": str(lead.id),
            "from_stage": result.from_stage.value,
            "to_stage": result.to_stage.value,
            "reason": result.reason.value,
            "entered_hot": result.entered_hot,
            "synced_status": synced_status.value if synced_status else None,
        },
    )
    return StageChangeOutcome(transition=result, event=event, entered_hot=result.entered_hot)


async def advance_from_classification(
    db: AsyncSession,
    lead: Lead,
    message: InboundMessage,
    classification: ReplyClassification,
) -> StageChangeOutcome | None:
    """Advance a lead's pipeline stage based on a classified reply.

    Unclear replies and replies whose implied target the lead has already
    reached (or exceeded) do not produce a stage change -- returning None is
    the caller's signal to leave the lead where it is and, for UNCLEAR,
    likely flag the message for human review instead.

    Args:
        db: Active database session.
        lead: The lead the message belongs to.
        message: The classified inbound message.
        classification: The classification result.

    Returns:
        The stage change outcome, or None if no advancement applies.
    """
    target = target_stage_for_intent(classification.intent)
    if target is None:
        logger.info(
            "Reply classified as unclear; no pipeline advancement",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return None

    next_stage = next_stage_towards(lead.pipeline_stage, target)
    if next_stage is None:
        logger.info(
            "Classified reply implies no further pipeline advancement",
            extra={
                "lead_id": str(lead.id),
                "current_stage": lead.pipeline_stage.value,
                "implied_target": target.value,
            },
        )
        return None

    try:
        return await apply_stage_change(
            db,
            lead,
            next_stage,
            PipelineTransitionReason.REPLY_CLASSIFIED,
            inbound_message_id=message.id,
            note=f"Reply classified as '{classification.intent.value}' "
            f"(confidence {classification.confidence:.2f}).",
        )
    except InvalidTransitionError:
        logger.exception(
            "Reply-driven transition rejected by the stage machine",
            extra={"lead_id": str(lead.id), "target": next_stage.value},
        )
        return None


async def advance_on_meeting_booked(
    db: AsyncSession,
    lead: Lead,
    *,
    inbound_message_id: uuid.UUID | None = None,
) -> StageChangeOutcome | None:
    """Advance a lead to MEETING_BOOKED after a real calendar booking.

    Called from the booking-confirm route (app.api.v1.booking) immediately
    after a Google Calendar event and Meeting row are created -- i.e. after
    the fact this call records has already, unconditionally, happened.
    Unlike :func:`advance_from_classification`, this is not a probabilistic
    inference from a reply: a real meeting exists on the shared calendar
    whether or not the pipeline bookkeeping can represent it, so a rejected
    transition here is defensively swallowed (logged, not raised) rather
    than surfaced as an error the caller must handle -- there is nothing a
    caller could usefully do to "fix" a stage-graph mismatch after the
    external event it describes has already occurred. See
    app.services.pipeline's _ALLOWED_TRANSITIONS docstring for why
    MEETING_BOOKED is reachable directly from CONTACTED, INTERESTED, and
    HOT, which is expected to make this rarely, if ever, actually reject in
    practice -- the defensive catch exists for the edge case of a lead that
    reached CONVERTED or LOST between the booking link being generated and
    the prospect confirming a slot.

    Args:
        db: Active database session. The caller is responsible for commit.
        lead: The lead whose meeting was just booked. Mutated in place if
            the transition succeeds.
        inbound_message_id: The reply that originally triggered the booking
            link, if known -- recorded on the PipelineEvent for traceability,
            same as advance_from_classification does for reply-driven moves.

    Returns:
        The stage change outcome, or None if the lead was already in a
        terminal stage (CONVERTED/LOST) and the transition was rejected.
    """
    try:
        return await apply_stage_change(
            db,
            lead,
            PipelineStage.MEETING_BOOKED,
            PipelineTransitionReason.MEETING_BOOKED,
            inbound_message_id=inbound_message_id,
            note="A meeting was booked on the shared sales calendar via the booking link.",
        )
    except InvalidTransitionError:
        logger.exception(
            "Meeting booked but the lead's pipeline stage could not advance to "
            "MEETING_BOOKED -- the calendar event and Meeting record are still valid "
            "and unaffected by this",
            extra={"lead_id": str(lead.id), "current_stage": lead.pipeline_stage.value},
        )
        return None


async def advance_on_outreach_sent(db: AsyncSession, lead: Lead) -> StageChangeOutcome | None:
    """Advance a New lead to Contacted when an outreach draft is sent.

    Only fires from `NEW` -- a lead already `CONTACTED` or further along has
    presumably been sent to before, so a second send is not a new pipeline
    event worth recording here.

    Args:
        db: Active database session.
        lead: The lead an outreach draft was just sent to.

    Returns:
        The stage change outcome, or None if the lead was not in `NEW`.
    """
    if lead.pipeline_stage is not PipelineStage.NEW:
        return None
    return await apply_stage_change(
        db, lead, PipelineStage.CONTACTED, PipelineTransitionReason.OUTREACH_SENT
    )
