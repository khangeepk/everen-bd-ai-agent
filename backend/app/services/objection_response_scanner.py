"""Auto-generates a suggested response draft when a reply raises an objection.

Event-triggered from app/api/v1/pipeline.py's log_message/classify_message
routes, right after a reply is classified -- mirrors the "no bulk/automatic
path, but do react to a specific event" convention already used for
call-center card generation (app/agents/call_card.py, triggered on entering
pipeline stage Hot) and the lead-signals/email-enrichment scanners (rep- or
event-triggered, never on a schedule; nothing in this codebase runs Celery
beat yet).

THIS MODULE NEVER SENDS ANYTHING. Every draft it creates is persisted with
OutreachDraft.status = PENDING_REVIEW via the exact same model, the exact
same CAN-SPAM footer logic, and the exact same channel-eligibility gate
(app.services.outreach_policy.assess_channel) as a normal, rep-requested
outreach draft -- see app/api/v1/outreach.py's generate_drafts route, which
this deliberately mirrors rather than introduces a parallel path for. A
draft this module creates is indistinguishable, from the send gate's point
of view, from one a human asked for; it only reaches the outbox through
POST /outreach/drafts/{id}/send after a human approves it, same as any
other draft. See AGENTS.md section 8.

A hard opt-out reply (see reply_classification.is_hard_opt_out) never
reaches this module's drafting logic at all --
reply_classification.classify_objection() already refuses to return an
objection type for one, so there is no code path here that could generate a
rebuttal to someone who asked to stop being contacted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import InboundChannel, InboundMessage
from app.services.canspam import CanSpamViolationError
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.outreach_audit import log_draft_transition
from app.services.outreach_policy import OutreachChannel, assess_channel
from app.services.reply_classification import (
    ObjectionType,
    ReplyClassification,
    classify_objection,
)
from app.services.suppression import has_hard_bounced, is_suppressed

__all__ = ["AGENT_NAME", "ObjectionDraftOutcome", "maybe_generate_objection_draft"]

logger = logging.getLogger(__name__)

AGENT_NAME = "objection-response-agent-v1"

#: Which outreach channel a suggested response is drafted for, keyed by the
#: channel the objection arrived on. A phone-note objection (a rep's logged
#: summary of a call) has no reply channel of its own to answer on, so it
#: defaults to email -- the one channel this system can actually send once
#: approved, giving the rep a concrete, actionable artifact rather than a
#: call script nothing here would use as a "response."
_INBOUND_TO_OUTREACH_CHANNEL: dict[InboundChannel, OutreachChannel] = {
    InboundChannel.EMAIL: OutreachChannel.EMAIL,
    InboundChannel.WHATSAPP: OutreachChannel.WHATSAPP,
    InboundChannel.PHONE_NOTE: OutreachChannel.EMAIL,
}


@dataclass
class ObjectionDraftOutcome:
    """What happened when checking a classified reply for an objection.

    Attributes:
        objection_type: The classified objection, if any.
        draft: The created draft, if one was generated.
        skipped_reason: Why no draft was created, if applicable.
    """

    objection_type: ObjectionType | None = None
    draft: OutreachDraft | None = None
    skipped_reason: str | None = None


async def maybe_generate_objection_draft(
    db: AsyncSession,
    lead: Lead,
    message: InboundMessage,
    classification: ReplyClassification,
) -> ObjectionDraftOutcome:
    """Generate a suggested objection-response draft, if the reply warrants one.

    Args:
        db: Active database session. Caller is responsible for committing
            (this only flushes, per this codebase's service-layer convention).
        lead: The lead who sent the reply.
        message: The classified inbound message.
        classification: The reply's classification.

    Returns:
        The outcome: the objection type (if any), the created draft (if
        any), and a reason when nothing was created.
    """
    objection_type = classify_objection(message.body, classification.intent)
    if objection_type is None:
        return ObjectionDraftOutcome(objection_type=None, skipped_reason=None)

    # Filtered by created_by_agent, not just triggering_message_id: a second
    # independent scanner (app.services.booking_link_scanner) also creates
    # drafts keyed by triggering_message_id when a reply is BOTH classified
    # as an objection by classify_objection() and, separately, classified
    # BOOK_CALL/INTERESTED by intent classification. Without this filter
    # each scanner would see the other's draft on the same message and
    # wrongly conclude it had already run.
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
            "Objection draft already exists for this message; not generating a duplicate",
            extra={"lead_id": str(lead.id), "message_id": str(message.id)},
        )
        return ObjectionDraftOutcome(
            objection_type=objection_type,
            skipped_reason="A response draft was already generated for this message.",
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
            "Objection response skipped: lead not eligible for this channel",
            extra={
                "lead_id": str(lead.id),
                "channel": channel.value,
                "blockers": len(decision.blockers),
            },
        )
        return ObjectionDraftOutcome(
            objection_type=objection_type,
            skipped_reason=(
                "Lead is not currently eligible for this channel: "
                + "; ".join(decision.blockers)
            ),
        )

    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    outreach_agent = OutreachDraftAgent(db, kb)
    audit, findings = await outreach_agent.top_findings(lead.id)
    service = await outreach_agent.best_service(lead, findings)

    content = await outreach_agent.generate_objection_response(
        lead, findings, service, objection_type, message.body, channel
    )

    draft = OutreachDraft(
        lead_id=lead.id,
        channel=channel,
        status=DraftStatus.PENDING_REVIEW,
        subject=content.subject,
        body=content.body,
        recipient_email=lead.contact_email,
        recipient_phone=lead.contact_phone,
        source_audit_id=audit.id if audit else None,
        source_service_id=service.id if service else None,
        objection_type=objection_type,
        triggering_message_id=message.id,
        created_by_agent=f"{AGENT_NAME}+{OUTREACH_AGENT_NAME}",
        used_fallback=content.used_fallback,
        review_warnings="\n".join(decision.warnings) or None,
        # Snapshot the lead's campaign_type so this draft still attributes
        # to the right bucket in app.services.analytics::get_campaign_performance
        # -- an objection reply to a re-engagement touch is still part of
        # that re-engagement campaign. follow_up_sequence is left at its
        # default (0): this draft is reply-triggered, not a cadence step, so
        # app.services.campaign_cadence's numbering doesn't apply to it.
        campaign_type=lead.campaign_type,
    )

    if channel is OutreachChannel.EMAIL:
        db.add(draft)
        await db.flush()
        try:
            assembled, unsubscribe_url = finalize_email_body(
                draft.id, lead.id, lead.contact_email or "", content.body
            )
        except CanSpamViolationError:
            logger.exception(
                "Cannot draft a compliant objection-response email; sender identity "
                "configuration incomplete",
                extra={"lead_id": str(lead.id), "draft_id": str(draft.id)},
            )
            await db.delete(draft)
            await db.flush()
            return ObjectionDraftOutcome(
                objection_type=objection_type,
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
        note=f"Auto-generated in response to a reply classified as an objection "
        f"({objection_type.value}). Awaiting human review, same as any other draft.",
    )

    logger.info(
        "Objection-response draft generated",
        extra={
            "lead_id": str(lead.id),
            "draft_id": str(draft.id),
            "message_id": str(message.id),
            "objection_type": objection_type.value,
            "channel": channel.value,
            "used_fallback": draft.used_fallback,
        },
    )
    return ObjectionDraftOutcome(objection_type=objection_type, draft=draft)
