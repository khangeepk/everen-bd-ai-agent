"""Scans contacted, non-responding leads for a due cadence follow-up.

Rep-triggered via ``POST /outreach/follow-ups/scan`` (app/api/v1/outreach.py)
-- nothing in this codebase runs Celery beat yet (same known gap already
documented in app.services.objection_response_scanner and
app.services.signal_scanner), so this is an on-demand scan a rep runs, not a
background job, until that infrastructure exists.

THIS MODULE NEVER SENDS ANYTHING. Every draft it creates is persisted with
``OutreachDraft.status = PENDING_REVIEW`` via the exact same model, the same
CAN-SPAM footer logic, and the same channel-eligibility gate
(:func:`app.services.outreach_policy.assess_channel`) as a normal,
rep-requested outreach draft -- mirroring
:mod:`app.services.objection_response_scanner` rather than introducing a
parallel path. A draft this module creates only reaches the outbox through
``POST /outreach/drafts/{id}/send`` after a human approves it. See AGENTS.md
section 8.

Candidate selection deliberately reuses state that already exists rather
than tracking anything new:

* ``Lead.pipeline_stage == CONTACTED`` is exactly "an outreach draft was sent
  and the lead has not replied" -- a reply moves the lead to INTERESTED, HOT,
  or LOST via :mod:`app.services.pipeline_transitions`/reply classification,
  so a CONTACTED lead is precisely the "no response yet" population a
  follow-up cadence exists for. A lead in any other stage is left alone here.
* Whether a follow-up is *due*, and how many remain, comes from the most
  recently SENT draft's ``sent_at`` and ``follow_up_sequence`` for that
  lead+channel, fed into :mod:`app.services.campaign_cadence`.
* CALL_SCRIPT is never followed up on here -- this system has no record of
  whether or when a call happened (a script is a document for a human to
  read, not something this system tracks delivery of), so there is nothing
  to cadence a follow-up off of.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

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
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.services.campaign_cadence import is_cadence_exhausted, is_follow_up_due
from app.services.canspam import CanSpamViolationError
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.outreach_audit import log_draft_transition
from app.services.outreach_policy import OutreachChannel, assess_channel
from app.services.pipeline import PipelineStage
from app.services.suppression import has_hard_bounced, is_suppressed

__all__ = ["AGENT_NAME", "CampaignFollowUpScanResult", "FollowUpSkip", "scan_due_follow_ups"]

logger = logging.getLogger(__name__)

AGENT_NAME = "campaign-followup-agent-v1"

#: Channels a cadence follow-up may be drafted for. CALL_SCRIPT is excluded
#: -- see this module's docstring.
_FOLLOW_UP_CHANNELS: tuple[OutreachChannel, ...] = (
    OutreachChannel.EMAIL,
    OutreachChannel.WHATSAPP,
)


@dataclass
class FollowUpSkip:
    """Why a candidate lead+channel did not get a follow-up drafted.

    Attributes:
        lead_id: The lead considered.
        channel: The channel considered.
        reason: Human-readable explanation.
    """

    lead_id: uuid.UUID
    channel: OutreachChannel
    reason: str


@dataclass
class CampaignFollowUpScanResult:
    """Outcome of one scan across all contacted, non-responding leads.

    Attributes:
        scanned_leads: How many leads were examined (pipeline_stage=CONTACTED,
            not suppressed).
        created: Newly generated follow-up drafts, pending_review.
        skipped: Lead+channel combinations considered but not drafted, with
            reasons -- includes both "not due yet" and "channel ineligible"
            cases, not just failures.
    """

    scanned_leads: int
    created: list[OutreachDraft] = field(default_factory=list)
    skipped: list[FollowUpSkip] = field(default_factory=list)


async def _latest_sent_draft(
    db: AsyncSession, lead_id: uuid.UUID, channel: OutreachChannel
) -> OutreachDraft | None:
    """Find a lead's most recently sent draft on one channel.

    Args:
        db: Active database session.
        lead_id: The lead to look up.
        channel: The channel to look up.

    Returns:
        The most recently sent draft, or None if nothing has been sent to
        this lead on this channel yet -- there is no cadence to follow up
        on until an initial message has actually gone out.
    """
    return (
        await db.execute(
            select(OutreachDraft)
            .where(
                OutreachDraft.lead_id == lead_id,
                OutreachDraft.channel == channel,
                OutreachDraft.status == DraftStatus.SENT,
                OutreachDraft.sent_at.is_not(None),
            )
            .order_by(OutreachDraft.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _newer_draft_exists(
    db: AsyncSession, lead_id: uuid.UUID, channel: OutreachChannel, since: datetime
) -> bool:
    """Whether any draft has been created on this channel since a given time.

    Args:
        db: Active database session.
        lead_id: The lead to check.
        channel: The channel to check.
        since: Only drafts created after this instant count.

    Returns:
        True if a draft already exists -- of any status -- created after
        ``since``. Used as the idempotency check: if a follow-up (or
        anything else, e.g. a rep manually drafted one) was already created
        for this lead+channel since the last send, this scan must not queue
        a second one alongside it.
    """
    existing = (
        await db.execute(
            select(OutreachDraft.id).where(
                OutreachDraft.lead_id == lead_id,
                OutreachDraft.channel == channel,
                OutreachDraft.created_at > since,
            )
        )
    ).scalar_one_or_none()
    return existing is not None


async def scan_due_follow_ups(
    db: AsyncSession, *, now: datetime | None = None
) -> CampaignFollowUpScanResult:
    """Scan contacted, non-responding leads and draft any due follow-up.

    Args:
        db: Active database session. Caller is responsible for committing
            (this only flushes, per this codebase's service-layer convention).
        now: The instant to evaluate cadence due-dates against. Defaults to
            now.

    Returns:
        The scan result: how many leads were examined, the drafts created,
        and every lead+channel combination that was skipped and why.
    """
    moment = now or utcnow()

    candidates = (
        (
            await db.execute(
                select(Lead).where(
                    Lead.pipeline_stage == PipelineStage.CONTACTED,
                    Lead.do_not_contact.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )

    result = CampaignFollowUpScanResult(scanned_leads=len(candidates))
    if not candidates:
        logger.info("Follow-up scan found no CONTACTED leads to consider")
        return result

    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    outreach_agent = OutreachDraftAgent(db, kb)

    for lead in candidates:
        for channel in _FOLLOW_UP_CHANNELS:
            last_sent = await _latest_sent_draft(db, lead.id, channel)
            if last_sent is None:
                # Nothing sent on this channel yet -- no cadence to follow
                # up on. Not logged as a skip: this is the overwhelmingly
                # common case (most leads only have one channel drafted)
                # and would drown out the skips worth a rep's attention.
                continue

            if is_cadence_exhausted(lead.campaign_type, last_sent.follow_up_sequence):
                continue

            if not is_follow_up_due(
                lead.campaign_type, last_sent.follow_up_sequence, last_sent.sent_at, moment
            ):
                continue

            if await _newer_draft_exists(db, lead.id, channel, last_sent.sent_at):
                result.skipped.append(
                    FollowUpSkip(
                        lead_id=lead.id,
                        channel=channel,
                        reason=(
                            "A draft already exists for this lead+channel since the last "
                            "send -- not queuing a duplicate follow-up."
                        ),
                    )
                )
                continue

            email_suppressed = bool(
                lead.contact_email
                and await is_suppressed(db, lead.contact_email, OutreachChannel.EMAIL)
            )
            hard_bounced = bool(
                lead.contact_email and await has_hard_bounced(db, lead.contact_email)
            )
            context = build_lead_context(
                lead, email_suppressed=email_suppressed, hard_bounced=hard_bounced
            )
            decision = assess_channel(channel, context)
            if not decision.allowed:
                result.skipped.append(
                    FollowUpSkip(
                        lead_id=lead.id,
                        channel=channel,
                        reason="Lead is not currently eligible for this channel: "
                        + "; ".join(decision.blockers),
                    )
                )
                continue

            audit, findings = await outreach_agent.top_findings(lead.id)
            service = await outreach_agent.best_service(lead, findings)
            next_sequence = last_sent.follow_up_sequence + 1

            content = await outreach_agent.generate_follow_up(
                lead, findings, service, channel, next_sequence
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
                created_by_agent=f"{AGENT_NAME}+{OUTREACH_AGENT_NAME}",
                used_fallback=content.used_fallback,
                review_warnings="\n".join(decision.warnings) or None,
                campaign_type=lead.campaign_type,
                follow_up_sequence=next_sequence,
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
                        "Cannot draft a compliant follow-up email; sender identity "
                        "configuration incomplete",
                        extra={"lead_id": str(lead.id), "draft_id": str(draft.id)},
                    )
                    await db.delete(draft)
                    await db.flush()
                    result.skipped.append(
                        FollowUpSkip(
                            lead_id=lead.id,
                            channel=channel,
                            reason=(
                                "Could not assemble a CAN-SPAM-compliant email (sender "
                                "configuration incomplete); see logs."
                            ),
                        )
                    )
                    continue

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
                note=(
                    f"Auto-generated follow-up #{next_sequence} under the "
                    f"{lead.campaign_type.value} cadence. Awaiting human review, same as "
                    "any other draft."
                ),
            )

            logger.info(
                "Follow-up draft generated",
                extra={
                    "lead_id": str(lead.id),
                    "draft_id": str(draft.id),
                    "channel": channel.value,
                    "campaign_type": lead.campaign_type.value,
                    "follow_up_sequence": next_sequence,
                    "used_fallback": draft.used_fallback,
                },
            )
            result.created.append(draft)

    logger.info(
        "Follow-up scan complete",
        extra={
            "scanned_leads": result.scanned_leads,
            "created": len(result.created),
            "skipped": len(result.skipped),
        },
    )
    return result
