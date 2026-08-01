"""Auto-generates a booking-link reply draft when a reply asks to book a call.

Event-triggered from app/api/v1/pipeline.py's log_message/classify_message
routes, right after a reply is classified -- the same event-triggered
convention already used by :mod:`app.services.objection_response_scanner`
(see that module's docstring for the fuller rationale on why this is a
per-event check, not a scheduled scan: nothing in this codebase runs Celery
beat yet).

THIS MODULE NEVER SENDS ANYTHING AND NEVER BOOKS A MEETING ITSELF. It only
drafts a reply pointing the prospect at a booking link; the actual booking
happens later, when the prospect follows that link and confirms a slot
through the public, token-scoped app.api.v1.booking routes -- an action the
prospect takes themselves, not something this scanner or a human reviewer
triggers on their behalf. The draft this module creates still goes through
the ordinary PENDING_REVIEW -> approved -> sent gate like any other outreach
content, exactly like objection_response_scanner's drafts. See AGENTS.md
section 8.

Two independent scanners (this one and objection_response_scanner) can both
create drafts keyed by the same triggering_message_id -- see the
created_by_agent filter on each one's existing-draft check for why that
does not cause them to collide or falsely dedupe against one another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.outreach import (
    AGENT_NAME as OUTREACH_AGENT_NAME,
)
from app.agents.outreach import (
    OutreachDraftAgent,
    build_lead_context,
    finalize_email_body,
    sender_identity,
)
from app.core.config import settings
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import InboundChannel, InboundMessage
from app.services.booking_token import build_booking_url, make_booking_token
from app.services.canspam import CanSpamViolationError
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.google_calendar import is_configured as is_calendar_configured
from app.services.knowledge_base import KnowledgeBaseService
from app.services.outreach_audit import log_draft_transition
from app.services.outreach_policy import OutreachChannel, assess_channel
from app.services.reply_classification import ReplyClassification, ReplyIntent
from app.services.suppression import has_hard_bounced, is_suppressed

__all__ = ["AGENT_NAME", "BookingLinkDraftOutcome", "maybe_generate_booking_link_draft"]

logger = logging.getLogger(__name__)

AGENT_NAME = "booking-link-agent-v1"

#: Which classified intents this scanner reacts to. BOOK_CALL is an
#: explicit ask; INTERESTED is engagement without that explicit ask, but
#: still worth proactively offering a link rather than waiting for a
#: second reply that spells it out -- consistent with how pipeline.py
#: already routes both toward advancing the conversation (see
#: _INTENT_TARGET_STAGE), just not all the way to a booking there.
_TRIGGERING_INTENTS: frozenset[ReplyIntent] = frozenset(
    {ReplyIntent.BOOK_CALL, ReplyIntent.INTERESTED}
)

#: Mirrors objection_response_scanner's channel mapping. A phone-note
#: "reply" (a rep's logged call summary) has no channel of its own to
#: answer the booking link on, so it defaults to email -- the one channel
#: this system can actually send once approved.
_INBOUND_TO_OUTREACH_CHANNEL: dict[InboundChannel, OutreachChannel] = {
    InboundChannel.EMAIL: OutreachChannel.EMAIL,
    InboundChannel.WHATSAPP: OutreachChannel.WHATSAPP,
    InboundChannel.PHONE_NOTE: OutreachChannel.EMAIL,
}


@dataclass
class BookingLinkDraftOutcome:
    """What happened when checking a classified reply for a booking ask.

    Attributes:
        triggered: Whether this reply's classified intent warranted a
            booking-link draft at all (True even if one was ultimately
            skipped for another reason, e.g. channel ineligibility).
        draft: The created draft, if one was generated.
        skipped_reason: Why no draft was created, if applicable.
    """

    triggered: bool = False
    draft: OutreachDraft | None = None
    skipped_reason: str | None = None


async def maybe_generate_booking_link_draft(
    db: AsyncSession,
    lead: Lead,
    message: InboundMessage,
    classification: ReplyClassification,
) -> BookingLinkDraftOutcome:
    """Generate a booking-link reply draft, if the reply asks to book a call.

    Args:
        db: Active database session. Caller is responsible for committing
            (this only flushes, per this codebase's service-layer convention).
        lead: The lead who sent the reply.
        message: The classified inbound message.
        classification: The reply's classification.

    Returns:
        The outcome: whether this reply's intent triggered consideration of
        a draft, the created draft (if any), and a reason when nothing was
        created.
    """
    if classification.intent not in _TRIGGERING_INTENTS:
        return BookingLinkDraftOutcome(triggered=False)

    # Filtered by created_by_agent, not just triggering_message_id -- see
    # the module docstring and objection_response_scanner's matching check
    # for why: without this, this scanner and objection_response_scanner
    # would each mistake the other's draft on the same message for their
    # own prior run and wrongly skip.
    existing = (
        await db.execute(
            select(OutreachDraft.id).where(
                OutreachDraft.triggering_message_id == message.id,
                OutreachDraft.created_by_agent == f"{AGENT_NAME}+{OUTREACH_AGENT_NAME}",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "Booking-link draft already exists for this message; not generating a duplicate",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return BookingLinkDraftOutcome(
            triggered=True,
            skipped_reason="A booking-link draft was already generated for this message.",
        )

    if not is_calendar_configured():
        logger.info(
            "Booking-link draft skipped: shared calendar is not configured",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return BookingLinkDraftOutcome(
            triggered=True,
            skipped_reason=(
                "The shared sales calendar is not configured yet, so no booking link "
                "can be generated. An admin must set the GOOGLE_CALENDAR_* settings."
            ),
        )

    channel = _INBOUND_TO_OUTREACH_CHANNEL[message.channel]

    email_suppressed = bool(
        lead.contact_email and await is_suppressed(db, lead.contact_email, OutreachChannel.EMAIL)
    )
    hard_bounced = bool(lead.contact_email and await has_hard_bounced(db, lead.contact_email))
    context = build_lead_context(
        lead, email_suppressed=email_suppressed, hard_bounced=hard_bounced
    )
    decision = assess_channel(channel, context)
    if not decision.allowed:
        logger.info(
            "Booking-link draft skipped: lead not eligible for this channel",
            extra={
                "lead_id": str(lead.id),
                "channel": channel.value,
                "blockers": len(decision.blockers),
            },
        )
        return BookingLinkDraftOutcome(
            triggered=True,
            skipped_reason=(
                "Lead is not currently eligible for this channel: "
                + "; ".join(decision.blockers)
            ),
        )

    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    outreach_agent = OutreachDraftAgent(db, kb)
    audit, findings = await outreach_agent.top_findings(lead.id)
    service = await outreach_agent.best_service(lead, findings)

    content = await outreach_agent.generate_booking_reply(
        lead, findings, service, message.body, channel
    )

    # The real booking URL, appended deterministically -- never generated by
    # the LLM. See generate_booking_reply's docstring and
    # app.services.booking_token for why. The token binds the triggering
    # message so app.api.v1.booking's confirm route can (eventually) trace a
    # booking back to the reply that started it.
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.booking_link_expiry_days)
    token = make_booking_token(str(lead.id), str(message.id), expires_at, settings.secret_key)
    booking_url = build_booking_url(settings.outreach_public_base_url, token)
    body_with_link = f"{content.body.rstrip()}\n\n{booking_url}"

    draft = OutreachDraft(
        lead_id=lead.id,
        channel=channel,
        status=DraftStatus.PENDING_REVIEW,
        subject=content.subject,
        body=body_with_link,
        recipient_email=lead.contact_email,
        recipient_phone=lead.contact_phone,
        source_audit_id=audit.id if audit else None,
        source_service_id=service.id if service else None,
        triggering_message_id=message.id,
        created_by_agent=f"{AGENT_NAME}+{OUTREACH_AGENT_NAME}",
        used_fallback=content.used_fallback,
        review_warnings="\n".join(decision.warnings) or None,
        # Snapshot the lead's campaign_type, same rationale as
        # objection_response_scanner: a booking reply to a re-engagement
        # touch is still part of that re-engagement campaign in analytics.
        # follow_up_sequence stays at its default (0) -- this draft is
        # reply-triggered, not a cadence step.
        campaign_type=lead.campaign_type,
    )

    if channel is OutreachChannel.EMAIL:
        db.add(draft)
        await db.flush()
        try:
            assembled, unsubscribe_url = finalize_email_body(
                draft.id, lead.id, lead.contact_email or "", body_with_link
            )
        except CanSpamViolationError:
            logger.exception(
                "Cannot draft a compliant booking-link email; sender identity "
                "configuration incomplete",
                extra={"lead_id": str(lead.id), "draft_id": str(draft.id)},
            )
            await db.delete(draft)
            await db.flush()
            return BookingLinkDraftOutcome(
                triggered=True,
                skipped_reason=(
                    "Could not assemble a CAN-SPAM-compliant email (sender "
                    "configuration incomplete); see logs."
                ),
            )

        sender = sender_identity()
        draft.body = assembled
        draft.unsubscribe_url = unsubscribe_url
        draft.sender_name = sender.from_name
        draft.sender_email = sender.from_email
        draft.sender_company = sender.company_name
        draft.sender_physical_address = sender.physical_address
    else:
        db.add(draft)
        await db.flush()

    await log_draft_transition(
        db,
        draft,
        None,
        DraftStatus.PENDING_REVIEW,
        user_id=None,
        note=f"Auto-generated in response to a reply classified as "
        f"'{classification.intent.value}', offering a booking link. Awaiting human "
        "review, same as any other draft.",
    )

    logger.info(
        "Booking-link draft generated",
        extra={
            "lead_id": str(lead.id),
            "draft_id": str(draft.id),
            "message_id": str(message.id),
            "intent": classification.intent.value,
            "channel": channel.value,
            "used_fallback": draft.used_fallback,
        },
    )
    return BookingLinkDraftOutcome(triggered=True, draft=draft)
