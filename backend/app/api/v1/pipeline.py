"""CRM pipeline routes: stage history, inbound messages, call-center cards.

Prefixed under ``/leads`` alongside lead_scores.py, since every endpoint here
operates on a specific lead.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.call_card import CallCenterCardAgent
from app.agents.reply_classifier import ReplyClassifierAgent
from app.api.deps import get_current_user, require_approver, require_write_access
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.pipeline import CallCenterCard, InboundMessage, PipelineEvent
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.pipeline import (
    CallCenterCardResponse,
    InboundMessageResponse,
    LeadPipelineResponse,
    LogMessageRequest,
    PipelineEventResponse,
    StageUpdateRequest,
)
from app.services.booking_link_scanner import (
    BookingLinkDraftOutcome,
    maybe_generate_booking_link_draft,
)
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.objection_response_scanner import (
    ObjectionDraftOutcome,
    maybe_generate_objection_draft,
)
from app.services.pipeline import InvalidTransitionError, PipelineTransitionReason
from app.services.pipeline_transitions import advance_from_classification, apply_stage_change
from app.services.reply_classification import ReplyClassification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["pipeline"])


async def _get_lead_or_404(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    """Fetch a lead or raise 404.

    Args:
        db: Active database session.
        lead_id: The lead to fetch.

    Returns:
        The lead.

    Raises:
        HTTPException: 404 if it does not exist.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


async def _maybe_generate_call_card(db: AsyncSession, lead: Lead, entered_hot: bool) -> None:
    """Generate a call-center card if this change is what entered Hot.

    Card-generation failures are logged, not raised -- the stage change
    itself must already be committed by the time this runs, since a rep
    briefing that fails to render must never roll back a real pipeline
    transition (the lead's conversation truly did advance).

    Args:
        db: Active database session.
        lead: The lead that changed stage.
        entered_hot: Whether this change is what put the lead into Hot.
    """
    if not entered_hot:
        return
    try:
        kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
        await CallCenterCardAgent(db, kb).generate(lead)
    except Exception:
        logger.exception(
            "Call-center card generation failed after entering Hot",
            extra={"lead_id": str(lead.id)},
        )


async def _maybe_generate_objection_draft(
    db: AsyncSession,
    lead: Lead,
    message: InboundMessage,
    classification: ReplyClassification,
) -> ObjectionDraftOutcome | None:
    """Generate a suggested objection-response draft, if the reply warrants one.

    Failures are logged, not raised -- by the time this runs, the message
    itself and any pipeline advancement have already been flushed in this
    same request, and an optional suggested-response draft failing to
    generate must never take that real, already-happened work down with it.

    Args:
        db: Active database session.
        lead: The lead who sent the reply.
        message: The classified inbound message.
        classification: The reply's classification.

    Returns:
        The outcome, so the caller can surface objection_type/
        objection_draft_id on the response -- or None if generation raised,
        in which case the message and any pipeline change are still valid
        and already flushed.
    """
    try:
        outcome = await maybe_generate_objection_draft(db, lead, message, classification)
        if outcome.draft is not None:
            logger.info(
                "Objection-response draft queued for review",
                extra={
                    "lead_id": str(lead.id),
                    "draft_id": str(outcome.draft.id),
                    "objection_type": outcome.objection_type.value
                    if outcome.objection_type
                    else None,
                },
            )
        return outcome
    except Exception:
        logger.exception(
            "Objection-response draft generation failed",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return None


async def _maybe_generate_booking_link_draft(
    db: AsyncSession,
    lead: Lead,
    message: InboundMessage,
    classification: ReplyClassification,
) -> BookingLinkDraftOutcome | None:
    """Generate a booking-link reply draft, if the reply asks to book a call.

    Same defensive posture as :func:`_maybe_generate_objection_draft`:
    failures are logged, not raised, since by the time this runs the
    message itself and any pipeline advancement are already flushed, and an
    optional booking-link draft failing to generate must never take that
    real, already-happened work down with it.

    Args:
        db: Active database session.
        lead: The lead who sent the reply.
        message: The classified inbound message.
        classification: The reply's classification.

    Returns:
        The outcome, so the caller can surface booking_draft_id on the
        response -- or None if generation raised.
    """
    try:
        outcome = await maybe_generate_booking_link_draft(db, lead, message, classification)
        if outcome.draft is not None:
            logger.info(
                "Booking-link draft queued for review",
                extra={"lead_id": str(lead.id), "draft_id": str(outcome.draft.id)},
            )
        return outcome
    except Exception:
        logger.exception(
            "Booking-link draft generation failed",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return None


@router.get(
    "/{lead_id}/pipeline",
    response_model=LeadPipelineResponse,
    summary="Get a lead's pipeline stage and event history",
)
async def get_pipeline(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LeadPipelineResponse:
    """Fetch a lead's current stage and its full transition history.

    Args:
        lead_id: The lead to look up.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The current stage and event history, oldest first.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    lead = await _get_lead_or_404(db, lead_id)
    events = (
        (
            await db.execute(
                select(PipelineEvent)
                .where(PipelineEvent.lead_id == lead_id)
                .order_by(PipelineEvent.changed_at)
            )
        )
        .scalars()
        .all()
    )
    return LeadPipelineResponse(
        lead_id=lead.id,
        pipeline_stage=lead.pipeline_stage,
        events=[PipelineEventResponse.model_validate(e) for e in events],
    )


@router.patch(
    "/{lead_id}/pipeline/stage",
    response_model=PipelineEventResponse,
    summary="Manually move a lead's pipeline stage",
    description=(
        "Validates against the stage transition graph unless force=true. "
        "force requires an approver role and is reserved for correcting a "
        "mis-classification, not routine stage-setting."
    ),
)
async def update_stage(
    lead_id: uuid.UUID,
    body: StageUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> PipelineEventResponse:
    """Manually change a lead's pipeline stage.

    Args:
        lead_id: The lead to move.
        body: The requested stage change.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The recorded transition event.

    Raises:
        HTTPException: 404 if the lead does not exist; 403 if ``force`` is
            requested by a non-approver; 409 if the transition is invalid
            and ``force`` is False.
    """
    lead = await _get_lead_or_404(db, lead_id)

    if body.force and not user.can_approve_outreach():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only approver roles may force a pipeline transition.",
        )

    reason = PipelineTransitionReason.FORCED if body.force else PipelineTransitionReason.MANUAL
    try:
        outcome = await apply_stage_change(
            db,
            lead,
            body.to_stage,
            reason,
            triggered_by_id=user.id,
            note=body.note,
            force=body.force,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _maybe_generate_call_card(db, lead, outcome.entered_hot)

    logger.info(
        "Pipeline stage updated manually",
        extra={"lead_id": str(lead.id), "to_stage": body.to_stage.value, "user_id": str(user.id)},
    )
    return PipelineEventResponse.model_validate(outcome.event)


@router.post(
    "/{lead_id}/messages",
    response_model=InboundMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log an inbound message and (by default) classify + auto-advance",
)
async def log_message(
    lead_id: uuid.UUID,
    body: LogMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> InboundMessageResponse:
    """Log an inbound message from a lead.

    By default this also classifies the message via the LLM (with a keyword
    fallback) and advances the pipeline if the classification implies it --
    set ``auto_classify=false`` to only log the message.

    Args:
        lead_id: The lead the message is from.
        body: The message to log.
        db: Active database session.
        user: The authenticated caller, recorded as the logger for phone notes.

    Returns:
        The logged message, its classification (if run), and any resulting
        stage change.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    lead = await _get_lead_or_404(db, lead_id)

    message = InboundMessage(
        lead_id=lead.id,
        channel=body.channel,
        body=body.body,
        received_at=body.received_at or utcnow(),
        related_draft_id=body.related_draft_id,
        logged_by_id=user.id,
        used_fallback=False,
    )
    db.add(message)
    await db.flush()

    stage_change_response: PipelineEventResponse | None = None
    objection_outcome: ObjectionDraftOutcome | None = None
    booking_outcome: BookingLinkDraftOutcome | None = None

    if body.auto_classify:
        classification = await ReplyClassifierAgent().classify(body.body, db)
        message.classified_intent = classification.intent
        message.classification_confidence = classification.confidence
        message.classification_reasons = "\n".join(classification.reasons)
        message.classified_at = utcnow()
        message.classified_by_agent = "reply-classifier-agent-v1"
        message.used_fallback = classification.used_fallback
        await db.flush()

        outcome = await advance_from_classification(db, lead, message, classification)
        if outcome is not None:
            stage_change_response = PipelineEventResponse.model_validate(outcome.event)
            await _maybe_generate_call_card(db, lead, outcome.entered_hot)

        # Both scanners below are independent of whether the pipeline
        # advanced -- e.g. a PRICING reply from a lead already at/past
        # Interested produces no stage change, but is still worth an
        # objection response; a second BOOK_CALL reply from a lead already
        # in Hot is still worth a fresh booking link.
        objection_outcome = await _maybe_generate_objection_draft(
            db, lead, message, classification
        )
        booking_outcome = await _maybe_generate_booking_link_draft(
            db, lead, message, classification
        )

    logger.info(
        "Inbound message logged",
        extra={
            "lead_id": str(lead.id),
            "message_id": str(message.id),
            "channel": body.channel.value,
            "auto_classified": body.auto_classify,
        },
    )

    response = InboundMessageResponse.model_validate(message)
    response.stage_change = stage_change_response
    if objection_outcome is not None:
        response.objection_type = objection_outcome.objection_type
        response.objection_draft_id = (
            objection_outcome.draft.id if objection_outcome.draft else None
        )
    if booking_outcome is not None:
        response.booking_draft_id = booking_outcome.draft.id if booking_outcome.draft else None
    return response


@router.post(
    "/{lead_id}/messages/{message_id}/classify",
    response_model=InboundMessageResponse,
    summary="(Re-)classify an already-logged message and auto-advance",
)
async def classify_message(
    lead_id: uuid.UUID,
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> InboundMessageResponse:
    """Classify (or re-classify) a logged message and advance the pipeline.

    Args:
        lead_id: The lead the message belongs to.
        message_id: The message to classify.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated message with its new classification and any stage change.

    Raises:
        HTTPException: 404 if the lead or message does not exist, or the
            message does not belong to the lead.
    """
    lead = await _get_lead_or_404(db, lead_id)
    message = await db.get(InboundMessage, message_id)
    if message is None or message.lead_id != lead.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    classification = await ReplyClassifierAgent().classify(message.body, db)
    message.classified_intent = classification.intent
    message.classification_confidence = classification.confidence
    message.classification_reasons = "\n".join(classification.reasons)
    message.classified_at = utcnow()
    message.classified_by_agent = "reply-classifier-agent-v1"
    message.used_fallback = classification.used_fallback
    await db.flush()

    stage_change_response: PipelineEventResponse | None = None
    outcome = await advance_from_classification(db, lead, message, classification)
    if outcome is not None:
        stage_change_response = PipelineEventResponse.model_validate(outcome.event)
        await _maybe_generate_call_card(db, lead, outcome.entered_hot)

    # Independent of whether the pipeline advanced -- see log_message's
    # identical comment above.
    objection_outcome = await _maybe_generate_objection_draft(db, lead, message, classification)
    booking_outcome = await _maybe_generate_booking_link_draft(db, lead, message, classification)

    logger.info(
        "Message re-classified",
        extra={
            "lead_id": str(lead.id),
            "message_id": str(message.id),
            "intent": classification.intent.value,
            "user_id": str(user.id),
        },
    )

    response = InboundMessageResponse.model_validate(message)
    response.stage_change = stage_change_response
    if objection_outcome is not None:
        response.objection_type = objection_outcome.objection_type
        response.objection_draft_id = (
            objection_outcome.draft.id if objection_outcome.draft else None
        )
    if booking_outcome is not None:
        response.booking_draft_id = booking_outcome.draft.id if booking_outcome.draft else None
    return response


@router.get(
    "/{lead_id}/call-card",
    response_model=CallCenterCardResponse,
    summary="Get a lead's most recent call-center card",
)
async def get_call_card(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CallCenterCardResponse:
    """Fetch the most recently generated call-center card for a lead.

    Args:
        lead_id: The lead to look up.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The latest card.

    Raises:
        HTTPException: 404 if the lead does not exist or has no card yet.
    """
    await _get_lead_or_404(db, lead_id)
    card = (
        await db.execute(
            select(CallCenterCard)
            .where(CallCenterCard.lead_id == lead_id)
            .order_by(CallCenterCard.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No call-center card has been generated for this lead yet.",
        )
    return CallCenterCardResponse.model_validate(card)


@router.post(
    "/{lead_id}/call-card/regenerate",
    response_model=CallCenterCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Regenerate a lead's call-center card on demand",
    description=(
        "Generates a fresh card from the lead's current audit findings, "
        "matched service, and message history, regardless of pipeline stage. "
        "Does not require the lead to be in Hot -- useful if a rep wants an "
        "updated briefing before a scheduled call."
    ),
)
async def regenerate_call_card(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> CallCenterCardResponse:
    """Regenerate a call-center card on demand.

    Args:
        lead_id: The lead to generate a card for.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The newly generated card.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    lead = await _get_lead_or_404(db, lead_id)
    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    card = await CallCenterCardAgent(db, kb).generate(lead)

    logger.info(
        "Call-center card regenerated on demand",
        extra={"lead_id": str(lead.id), "card_id": str(card.id), "user_id": str(user.id)},
    )
    return CallCenterCardResponse.model_validate(card)
