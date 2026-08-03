"""Outreach routes: draft generation, approval queue, and gated sending.

THE SEND GATE LIVES HERE. Per AGENTS.md section 8:

* ``POST /drafts`` generates drafts, always ``pending_review``. It cannot send.
* ``POST /{id}/approve`` marks a draft sendable. It does NOT send.
* ``POST /{id}/send`` is the only endpoint that dispatches, and it verifies
  ``status == APPROVED`` before doing anything else.

Every status transition writes an :class:`OutreachAuditLog` row. No Celery
task may call the send path.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.outreach import (
    AGENT_NAME,
    OutreachDraftAgent,
    finalize_email_body,
    sender_identity,
)
from app.api.deps import (
    get_current_user,
    require_approver,
    require_write_access,
    verify_sendgrid_webhook,
    verify_webhook_secret,
)
from app.core.config import settings
from app.db.base import utcnow
from app.db.models.analytics import EmailOpenEvent
from app.db.models.audit import AuditFinding
from app.db.models.knowledge_base import Service
from app.db.models.lead import Lead
from app.db.models.outreach import (
    EDITABLE_STATUSES,
    DraftStatus,
    OutreachAuditLog,
    OutreachDraft,
    ProcessedWebhookEvent,
    SuppressionReason,
)
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.alert_log import DomainPauseRequest, DomainPauseResponse
from app.schemas.outreach import (
    ApproveDraftRequest,
    AuditLogEntryResponse,
    BounceWebhookEvent,
    BounceWebhookResponse,
    DetectedProblemResponse,
    DraftClaimResponse,
    DraftResponse,
    EnrichedDraftResponse,
    EnrichedLeadSummary,
    FollowUpScanResponse,
    FollowUpSkipResponse,
    GenerateDraftsRequest,
    GenerateDraftsResponse,
    PaginatedDrafts,
    PaginatedEnrichedDrafts,
    QuotaStatusResponse,
    RejectDraftRequest,
    SendResultResponse,
    SkippedChannelResponse,
    UnsubscribeResponse,
    UpdateDraftRequest,
)
from app.services.campaign_followup_scanner import scan_due_follow_ups
from app.services.dashboard_summary import compliance_state_for_lead, latest_scores_by_lead
from app.services.canspam import (
    CanSpamViolationError,
    validate_sendable_email,
    validate_subject,
    verify_unsubscribe_token,
)
from app.services.email_sender import EmailSendError, SendGridEmailSender
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.outreach_policy import OutreachChannel, assess_channel
from app.services.pii import email_blind_index
from app.services.send_limits import (
    QuotaExceededError,
    check_can_send,
    classify_sendgrid_event,
)
from app.services.outreach_audit import log_draft_transition
from app.services.pipeline_transitions import advance_on_outreach_sent
from app.services.sendgrid_health import pause_domain_outreach
from app.services.warmup_tracker import resolve_effective_daily_limit
from app.services.suppression import (
    get_quota_status,
    has_hard_bounced,
    increment_send_counter,
    is_suppressed,
    normalize_identifier,
    record_bounce,
    suppress,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach", tags=["outreach"])


#: Kept as a module-level alias so every existing call site below reads
#: unchanged -- the implementation now lives in
#: app.services.outreach_audit, shared with the objection-response scanner.
_log_transition = log_draft_transition


@router.post(
    "/leads/{lead_id}/drafts",
    response_model=GenerateDraftsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate outreach drafts for a lead",
    description=(
        "Generates email, WhatsApp, call-script, and/or LinkedIn drafts grounded in the "
        "lead's latest website audit and best-matching service. Every draft is created "
        "with status=pending_review. This endpoint cannot send anything -- and for "
        "LinkedIn, nothing in this system ever will: it produces a connection-request "
        "note and follow-up message as plain text (see linkedin_followup_message) for a "
        "rep to copy and send manually from their own LinkedIn account. WhatsApp is "
        "skipped unless the lead has recorded opt-in, as Meta requires; LinkedIn is "
        "skipped unless the lead has a linkedin_url on file."
    ),
)
async def generate_drafts(
    lead_id: uuid.UUID,
    payload: GenerateDraftsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> GenerateDraftsResponse:
    """Generate outreach drafts for a lead.

    Args:
        lead_id: The lead to draft for.
        payload: Which channels to draft.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The created drafts and any skipped channels with reasons.

    Raises:
        HTTPException: 404 if the lead does not exist, 422 if CAN-SPAM sender
            configuration is incomplete.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    email_suppressed = bool(
        lead.contact_email
        and await is_suppressed(db, lead.contact_email, OutreachChannel.EMAIL)
    )
    hard_bounced = bool(lead.contact_email and await has_hard_bounced(db, lead.contact_email))

    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    agent = OutreachDraftAgent(db=db, kb=kb)
    generation = await agent.generate(
        lead,
        payload.channels,
        email_suppressed=email_suppressed,
        hard_bounced=hard_bounced,
    )

    sender = sender_identity()
    created: list[OutreachDraft] = []

    for content in generation.drafts:
        draft = OutreachDraft(
            lead_id=lead.id,
            channel=content.channel,
            status=DraftStatus.PENDING_REVIEW,
            subject=content.subject,
            body=content.body,
            recipient_email=lead.contact_email,
            recipient_phone=lead.contact_phone,
            created_by_agent=AGENT_NAME,
            used_fallback=content.used_fallback,
            review_warnings="\n".join(content.warnings) or None,
            prompt_version_id=content.prompt_version_id,
            ab_variant=content.ab_variant,
            draft_language=content.draft_language,
            # Snapshot the lead's campaign_type at generation time -- see
            # app/db/models/outreach.py's comment on this column. This is the
            # initial send for this lead's cadence, so follow_up_sequence
            # starts at 0 (see app/services/campaign_cadence.py).
            campaign_type=lead.campaign_type,
            follow_up_sequence=0,
        )

        if content.channel is OutreachChannel.EMAIL:
            db.add(draft)
            await db.flush()
            try:
                assembled, unsubscribe_url = finalize_email_body(
                    draft.id, lead.id, lead.contact_email or "", content.body
                )
            except CanSpamViolationError as exc:
                await db.rollback()
                logger.exception("CAN-SPAM configuration incomplete")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Cannot draft a compliant email: {exc}. Check "
                        "OUTREACH_PHYSICAL_ADDRESS and OUTREACH_PUBLIC_BASE_URL."
                    ),
                ) from exc

            draft.body = assembled
            draft.unsubscribe_url = unsubscribe_url
            draft.sender_name = sender.from_name
            draft.sender_email = sender.from_email
            draft.sender_company = sender.company_name
            draft.sender_physical_address = sender.physical_address
        elif content.channel is OutreachChannel.LINKEDIN:
            # The connection-request note is already in draft.body (set
            # above, same as every other channel's primary text); the
            # follow-up message is the one piece of LinkedIn content that
            # doesn't fit that shape -- see DraftContent.linkedin_followup_body's
            # docstring for why it's carried separately.
            draft.linkedin_followup_message = content.linkedin_followup_body
            db.add(draft)
            await db.flush()
        else:
            db.add(draft)
            await db.flush()

        await _log_transition(
            db, draft, None, DraftStatus.PENDING_REVIEW, user.id, "Draft generated"
        )
        created.append(draft)

    logger.info(
        "Drafts generated",
        extra={
            "lead_id": str(lead.id),
            "created": len(created),
            "skipped": len(generation.skipped),
            "user_id": str(user.id),
        },
    )
    return GenerateDraftsResponse(
        lead_id=lead.id,
        drafts=[DraftResponse.model_validate(draft) for draft in created],
        skipped=[
            SkippedChannelResponse(
                channel=decision.channel,
                blockers=list(decision.blockers),
                warnings=list(decision.warnings),
            )
            for decision in generation.skipped
        ],
    )


@router.post(
    "/follow-ups/scan",
    response_model=FollowUpScanResponse,
    summary="Scan for and draft any due campaign follow-ups",
    description=(
        "Looks at every lead in pipeline stage 'contacted' (outreach sent, no reply "
        "yet), checks whether that lead's campaign-type cadence "
        "(app/services/campaign_cadence.py) says the next follow-up is due, and "
        "drafts it -- always status=pending_review, same as any other draft. "
        "Rep-triggered: nothing in this codebase runs this on a schedule yet."
    ),
)
async def scan_follow_ups(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> FollowUpScanResponse:
    """Scan contacted, non-responding leads and draft any due follow-up.

    Args:
        db: Active database session.
        user: The authenticated caller.

    Returns:
        Every draft created, plus every lead+channel combination that was
        considered but skipped and why.
    """
    result = await scan_due_follow_ups(db)
    logger.info(
        "Follow-up scan requested via API",
        extra={
            "user_id": str(user.id),
            "scanned_leads": result.scanned_leads,
            "created": len(result.created),
            "skipped": len(result.skipped),
        },
    )
    return FollowUpScanResponse(
        scanned_leads=result.scanned_leads,
        drafts=[DraftResponse.model_validate(draft) for draft in result.created],
        skipped=[
            FollowUpSkipResponse(lead_id=skip.lead_id, channel=skip.channel, reason=skip.reason)
            for skip in result.skipped
        ],
    )


@router.get(
    "/queue",
    response_model=PaginatedDrafts,
    summary="List the approval queue",
    description="Returns drafts awaiting human review, oldest first.",
)
async def list_queue(
    status_filter: DraftStatus = Query(default=DraftStatus.PENDING_REVIEW, alias="status"),
    channel: OutreachChannel | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedDrafts:
    """List drafts in the approval queue.

    Args:
        status_filter: Which status to list. Defaults to pending_review.
        channel: Optional channel filter.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        A page of drafts.
    """
    filters = [OutreachDraft.status == status_filter]
    if channel is not None:
        filters.append(OutreachDraft.channel == channel)

    total = (
        await db.execute(select(func.count()).select_from(OutreachDraft).where(*filters))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(OutreachDraft)
                .where(*filters)
                .order_by(OutreachDraft.created_at.asc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )

    return PaginatedDrafts(
        items=[DraftResponse.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/queue/enriched",
    response_model=PaginatedEnrichedDrafts,
    summary="List the approval queue with full review context",
    description=(
        "Same filtering/paging as GET /outreach/queue, but each draft is joined "
        "with its lead, latest score, the audit findings it's grounded in "
        "(via source_audit_id), and its recommended service (via "
        "source_service_id) -- everything the approval-review screen needs "
        "in one call. A draft with no source_audit_id simply has an empty "
        "problems/claims list; nothing here is invented."
    ),
)
async def list_queue_enriched(
    status_filter: DraftStatus = Query(default=DraftStatus.PENDING_REVIEW, alias="status"),
    channel: OutreachChannel | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedEnrichedDrafts:
    """List drafts in the approval queue, enriched with review context.

    Args:
        status_filter: Which status to list. Defaults to pending_review.
        channel: Optional channel filter.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        A page of enriched drafts.
    """
    filters = [OutreachDraft.status == status_filter]
    if channel is not None:
        filters.append(OutreachDraft.channel == channel)

    total = (
        await db.execute(select(func.count()).select_from(OutreachDraft).where(*filters))
    ).scalar_one()

    drafts = (
        (
            await db.execute(
                select(OutreachDraft)
                .where(*filters)
                .order_by(OutreachDraft.created_at.asc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )

    lead_ids = list({d.lead_id for d in drafts})
    leads_by_id: dict[uuid.UUID, Lead] = {}
    if lead_ids:
        leads_by_id = {
            lead.id: lead
            for lead in (
                (await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))).scalars().all()
            )
        }
    scores_by_lead = await latest_scores_by_lead(db, lead_ids)

    audit_ids = list({d.source_audit_id for d in drafts if d.source_audit_id is not None})
    findings_by_audit: dict[uuid.UUID, list[AuditFinding]] = {}
    if audit_ids:
        findings = (
            (
                await db.execute(
                    select(AuditFinding).where(AuditFinding.audit_id.in_(audit_ids))
                )
            )
            .scalars()
            .all()
        )
        for finding in findings:
            findings_by_audit.setdefault(finding.audit_id, []).append(finding)

    service_ids = list({d.source_service_id for d in drafts if d.source_service_id is not None})
    service_names_by_id: dict[uuid.UUID, str] = {}
    if service_ids:
        service_names_by_id = {
            row.id: row.name
            for row in (
                (await db.execute(select(Service).where(Service.id.in_(service_ids))))
                .scalars()
                .all()
            )
        }

    items: list[EnrichedDraftResponse] = []
    for draft in drafts:
        lead = leads_by_id.get(draft.lead_id)
        score_row = scores_by_lead.get(draft.lead_id)
        findings = findings_by_audit.get(draft.source_audit_id, []) if draft.source_audit_id else []

        items.append(
            EnrichedDraftResponse(
                id=draft.id,
                lead=EnrichedLeadSummary(
                    id=lead.id if lead else draft.lead_id,
                    name=lead.name if lead else "(lead unavailable)",
                    industry=lead.category if lead else None,
                    # Lead has no city/state field, only country -- this is
                    # the real granularity available, not a placeholder.
                    location=lead.country if lead else None,
                ),
                channel=draft.channel,
                status=draft.status,
                subject=draft.subject,
                body=draft.body,
                linkedin_followup_message=draft.linkedin_followup_message,
                review_warnings=draft.review_warnings,
                created_at=draft.created_at,
                score=score_row.total_score if score_row else None,
                score_reasons=(
                    [
                        r
                        for r in (
                            (score_row.need_reasons or "").split("\n")
                            + (score_row.fit_reasons or "").split("\n")
                        )
                        if r
                    ][:3]
                    if score_row
                    else []
                ),
                compliance_state=compliance_state_for_lead(lead) if lead else None,
                problems=[
                    DetectedProblemResponse(
                        category=f.category, title=f.title, detail=f.detail
                    )
                    for f in findings
                ],
                claims=[
                    DraftClaimResponse(phrase=f.title, source=f.category, evidence=f.evidence)
                    for f in findings
                ],
                recommended_service=(
                    service_names_by_id.get(draft.source_service_id)
                    if draft.source_service_id
                    else None
                ),
            )
        )

    return PaginatedEnrichedDrafts(items=items, total=int(total), page=page, page_size=page_size)


@router.get(
    "/drafts/{draft_id}",
    response_model=DraftResponse,
    summary="Get a draft",
    description="Retrieves one outreach draft.",
)
async def get_draft(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DraftResponse:
    """Fetch one draft.

    Args:
        draft_id: The draft to fetch.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The draft.

    Raises:
        HTTPException: 404 if no such draft exists.
    """
    draft = await db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return DraftResponse.model_validate(draft)


@router.patch(
    "/drafts/{draft_id}",
    response_model=DraftResponse,
    summary="Edit a draft before approval",
    description=(
        "Edits a pending or rejected draft. Approved and sent drafts are immutable, "
        "so an approval cannot be applied to content the approver never saw."
    ),
)
async def update_draft(
    draft_id: uuid.UUID,
    payload: UpdateDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> DraftResponse:
    """Edit a draft that has not yet been approved.

    Args:
        draft_id: The draft to edit.
        payload: The edits.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated draft.

    Raises:
        HTTPException: 404 if missing, 409 if the draft is no longer editable,
            422 if an edited subject would violate CAN-SPAM.
    """
    draft = await db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    if draft.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A draft in status '{draft.status.value}' cannot be edited. Approved "
                "and sent drafts are immutable."
            ),
        )

    updates = payload.model_dump(exclude_unset=True)

    if updates.get("subject") is not None:
        try:
            validate_subject(updates["subject"])
        except CanSpamViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    if updates.get("body") is not None and draft.channel is OutreachChannel.EMAIL:
        # Re-attach the footer if an edit stripped it, rather than letting a
        # non-compliant body reach the send gate.
        if draft.unsubscribe_url and draft.unsubscribe_url not in updates["body"]:
            sender = sender_identity()
            updates["body"] = (
                updates["body"].rstrip()
                + "\n\n---\n"
                + f"This message was sent by {sender.company_name}.\n"
                + f"{sender.physical_address}\n\n"
                + "Not interested? Unsubscribe here and we will not contact you again:\n"
                + f"{draft.unsubscribe_url}\n"
            )

    for field_name, value in updates.items():
        setattr(draft, field_name, value)

    await db.flush()
    logger.info(
        "Draft edited",
        extra={"draft_id": str(draft.id), "user_id": str(user.id), "fields": list(updates)},
    )
    return DraftResponse.model_validate(draft)


@router.post(
    "/drafts/{draft_id}/approve",
    response_model=DraftResponse,
    summary="Approve a draft for sending",
    description=(
        "Marks a draft sendable and records who approved it and when. Does NOT send. "
        "Sending is a separate call to POST /drafts/{id}/send. Restricted to "
        "approver roles."
    ),
)
async def approve_draft(
    draft_id: uuid.UUID,
    payload: ApproveDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_approver),
) -> DraftResponse:
    """Approve a draft.

    Args:
        draft_id: The draft to approve.
        payload: Optional approval note.
        db: Active database session.
        user: The authenticated approver.

    Returns:
        The approved draft.

    Raises:
        HTTPException: 404 if missing, 409 if not pending review, 422 if the
            lead has since become ineligible for this channel.
    """
    draft = await db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    if draft.status is not DraftStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending_review drafts can be approved; this one is "
            f"'{draft.status.value}'.",
        )

    # Re-check eligibility at approval time: the lead may have been suppressed
    # or opted out between generation and review.
    lead = await db.get(Lead, draft.lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    from app.agents.outreach import build_lead_context

    email_suppressed = bool(
        lead.contact_email
        and await is_suppressed(db, lead.contact_email, OutreachChannel.EMAIL)
    )
    hard_bounced = bool(lead.contact_email and await has_hard_bounced(db, lead.contact_email))
    decision = assess_channel(
        draft.channel,
        build_lead_context(lead, email_suppressed=email_suppressed, hard_bounced=hard_bounced),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "This lead is no longer eligible for this channel: "
                + "; ".join(decision.blockers)
            ),
        )

    old_status = draft.status
    draft.status = DraftStatus.APPROVED
    draft.approved_by_id = user.id
    draft.approved_at = utcnow()
    await db.flush()

    await _log_transition(db, draft, old_status, DraftStatus.APPROVED, user.id, payload.note)

    logger.info(
        "Draft approved",
        extra={"draft_id": str(draft.id), "approved_by": str(user.id)},
    )
    return DraftResponse.model_validate(draft)


@router.post(
    "/drafts/{draft_id}/reject",
    response_model=DraftResponse,
    summary="Reject a draft",
    description="Marks a draft rejected with a required reason. It will not be sent.",
)
async def reject_draft(
    draft_id: uuid.UUID,
    payload: RejectDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> DraftResponse:
    """Reject a draft.

    Args:
        draft_id: The draft to reject.
        payload: The rejection reason.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The rejected draft.

    Raises:
        HTTPException: 404 if missing, 409 if already sent.
    """
    draft = await db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    if draft.status is DraftStatus.SENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A sent draft cannot be rejected."
        )

    old_status = draft.status
    draft.status = DraftStatus.REJECTED
    draft.rejected_reason = payload.reason
    await db.flush()

    await _log_transition(
        db, draft, old_status, DraftStatus.REJECTED, user.id, payload.reason
    )

    logger.info("Draft rejected", extra={"draft_id": str(draft.id), "user_id": str(user.id)})
    return DraftResponse.model_validate(draft)


@router.post(
    "/drafts/{draft_id}/send",
    response_model=SendResultResponse,
    summary="Send an approved draft",
    description=(
        "The ONLY endpoint that dispatches outreach. Verifies status == approved "
        "before doing anything else, then re-checks suppression, the daily quota, "
        "and CAN-SPAM validity immediately before dispatch. Restricted to approver "
        "roles."
    ),
)
async def send_draft(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_approver),
) -> SendResultResponse:
    """Send an approved draft.

    Args:
        draft_id: The draft to send.
        db: Active database session.
        user: The authenticated approver.

    Returns:
        The send result.

    Raises:
        HTTPException: 404 if missing; 403 if not approved; 409 if already
            sent, suppressed, or over quota; 422 if CAN-SPAM validation fails;
            502 if the provider rejects the message.
    """
    draft = await db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    # THE GATE. AGENTS.md section 8.
    if draft.status is not DraftStatus.APPROVED:
        logger.warning(
            "Send refused: draft not approved",
            extra={"draft_id": str(draft.id), "status": draft.status.value},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Draft status is '{draft.status.value}'. Only approved drafts can be "
                "sent. A human must approve this draft first."
            ),
        )
    if draft.approved_by_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Draft is marked approved but carries no approver. Refusing to send.",
        )
    if draft.sent_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This draft has already been sent."
        )

    if draft.channel is not OutreachChannel.EMAIL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"This endpoint sends email only. A '{draft.channel.value}' draft is a "
                "document for a human to use, not something this system transmits."
            ),
        )
    if not draft.recipient_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Draft has no recipient email address.",
        )

    # Re-check suppression at send time, not just at approval.
    if await is_suppressed(db, draft.recipient_email, OutreachChannel.EMAIL):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Recipient is on the suppression list. CAN-SPAM opt-outs never expire, "
                "so this address may not be contacted."
            ),
        )

    # The effective limit honors an active warmup schedule (see
    # app/services/warmup_tracker.py) -- it is never higher than
    # settings.outreach_daily_send_limit, only ever equal to or lower while
    # a ramp is in progress.
    daily_limit = await resolve_effective_daily_limit(
        db, OutreachChannel.EMAIL, settings.outreach_daily_send_limit
    )
    quota = await get_quota_status(db, OutreachChannel.EMAIL, daily_limit)
    try:
        check_can_send(quota, count=1)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    sender = sender_identity()
    try:
        validate_sendable_email(
            subject=draft.subject or "",
            body=draft.body,
            sender=sender,
            unsubscribe_url=draft.unsubscribe_url or "",
        )
    except CanSpamViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Draft fails CAN-SPAM validation: {exc}",
        ) from exc

    try:
        result = await SendGridEmailSender().send(
            to_email=draft.recipient_email,
            subject=draft.subject or "",
            body=draft.body,
            from_email=sender.from_email,
            from_name=sender.from_name,
            reply_to=sender.reply_to,
            unsubscribe_url=draft.unsubscribe_url,
        )
    except EmailSendError as exc:
        old_status = draft.status
        draft.status = DraftStatus.FAILED
        draft.failure_detail = str(exc)
        await db.commit()
        await _log_transition(db, draft, old_status, DraftStatus.FAILED, user.id, str(exc))
        logger.exception("Send failed", extra={"draft_id": str(draft.id)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Email provider rejected the message"
        ) from exc

    old_status = draft.status
    draft.status = DraftStatus.SENT
    draft.sent_at = utcnow()
    draft.provider_message_id = result.provider_message_id
    await db.flush()

    await increment_send_counter(db, OutreachChannel.EMAIL)
    await _log_transition(db, draft, old_status, DraftStatus.SENT, user.id, "Sent")

    lead = await db.get(Lead, draft.lead_id)
    if lead is not None:
        # New -> Contacted only; a lead further along has presumably been
        # sent to before, so this is a no-op there. See
        # app/services/pipeline_transitions.py.
        await advance_on_outreach_sent(db, lead)

    updated_quota = await get_quota_status(db, OutreachChannel.EMAIL, daily_limit)

    logger.info(
        "Draft sent",
        extra={"draft_id": str(draft.id), "sent_by": str(user.id)},
    )
    return SendResultResponse(
        draft_id=draft.id,
        status=draft.status,
        sent_at=draft.sent_at,
        provider_message_id=draft.provider_message_id,
        quota_remaining=updated_quota.remaining,
    )


@router.get(
    "/drafts/{draft_id}/audit-log",
    response_model=list[AuditLogEntryResponse],
    summary="Get a draft's audit trail",
    description="Every status transition on this draft, oldest first.",
)
async def get_audit_log(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AuditLogEntryResponse]:
    """Fetch a draft's audit trail.

    Args:
        draft_id: The draft to look up.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The transition history.

    Raises:
        HTTPException: 404 if the draft does not exist.
    """
    if await db.get(OutreachDraft, draft_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    rows = (
        (
            await db.execute(
                select(OutreachAuditLog)
                .where(OutreachAuditLog.draft_id == draft_id)
                .order_by(OutreachAuditLog.changed_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [AuditLogEntryResponse.model_validate(row) for row in rows]


@router.get(
    "/quota",
    response_model=QuotaStatusResponse,
    summary="Get today's send quota standing",
    description="Reports how many sends remain against the daily limit.",
)
async def get_quota(
    channel: OutreachChannel = Query(default=OutreachChannel.EMAIL),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QuotaStatusResponse:
    """Report the current daily send quota.

    Args:
        channel: Which channel to report on.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The quota standing. ``limit`` reflects an active warmup schedule if
        one is configured for this channel (see
        app/services/warmup_tracker.py) -- never higher than
        settings.outreach_daily_send_limit, only ever equal to or lower.
    """
    daily_limit = await resolve_effective_daily_limit(
        db, channel, settings.outreach_daily_send_limit
    )
    quota = await get_quota_status(db, channel, daily_limit)
    return QuotaStatusResponse(
        channel=channel,
        quota_date=quota.quota_date.isoformat(),
        limit=quota.limit,
        used=quota.used,
        remaining=quota.remaining,
        resets_at=quota.resets_at,
    )


#: A 1x1 transparent GIF, served on every open-tracking pixel hit regardless
#: of whether the draft is found -- an email client should never see a
#: broken-image icon because of our tracking logic.
_TRACKING_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"
)


@router.get(
    "/track/open/{draft_id}.gif",
    summary="Email open-tracking pixel",
    description=(
        "Public, unauthenticated -- loaded as an <img> tag by the recipient's email "
        "client. Records an EmailOpenEvent and always returns a 1x1 transparent GIF, "
        "even if the draft_id is unknown, so a broken pixel never shows in the email. "
        "Note: an open-tracking pixel is a 'tracking technology' under EU ePrivacy "
        "rules; this is used only for aggregate B2B outreach analytics on mail we sent."
    ),
    include_in_schema=True,
)
async def track_open(
    draft_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Record an email open and return a 1x1 transparent GIF.

    Args:
        draft_id: The draft whose send this open belongs to.
        request: The inbound request, used only to derive a hashed client
            identifier and the user-agent string.
        db: Active database session.

    Returns:
        The tracking pixel image, unconditionally -- failures to find or
        record against the draft are logged, never surfaced to the client.
    """
    try:
        draft = await db.get(OutreachDraft, draft_id)
        if draft is not None and draft.channel is OutreachChannel.EMAIL:
            client_ip = request.client.host if request.client else ""
            client_hash = (
                hashlib.sha256(client_ip.encode("utf-8")).hexdigest() if client_ip else None
            )
            db.add(
                EmailOpenEvent(
                    draft_id=draft.id,
                    opened_at=utcnow(),
                    client_hash=client_hash,
                    user_agent=request.headers.get("user-agent", "")[:300] or None,
                )
            )
            await db.commit()
            logger.info("Email open recorded", extra={"draft_id": str(draft.id)})
        else:
            logger.info(
                "Open pixel hit for unknown or non-email draft",
                extra={"draft_id": str(draft_id)},
            )
    except Exception:
        logger.exception("Failed to record email open; serving pixel anyway")

    return Response(content=_TRACKING_PIXEL_GIF, media_type="image/gif")


@router.get(
    "/unsubscribe",
    response_model=UnsubscribeResponse,
    summary="Process an unsubscribe request",
    description=(
        "Public endpoint reached from the unsubscribe link in every email. Requires "
        "no login and no form, as CAN-SPAM mandates: a single page visit completes "
        "the opt-out. Suppression is permanent."
    ),
)
async def unsubscribe(
    draft: str = Query(..., description="Draft identifier from the link."),
    email: str = Query(..., description="Recipient address from the link."),
    token: str = Query(..., description="HMAC verification token from the link."),
    db: AsyncSession = Depends(get_db),
) -> UnsubscribeResponse:
    """Record an opt-out from an unsubscribe link.

    Deliberately unauthenticated -- requiring a login would breach CAN-SPAM's
    rule that opting out must need no more than a single page visit.

    Args:
        draft: Draft identifier from the link.
        email: Recipient address from the link.
        token: HMAC token from the link.
        db: Active database session.

    Returns:
        Confirmation that the opt-out was recorded.

    Raises:
        HTTPException: 400 if the token does not verify.
    """
    if not verify_unsubscribe_token(token, draft, email, settings.secret_key):
        logger.warning("Invalid unsubscribe token presented")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid unsubscribe link."
        )

    await suppress(
        db,
        email,
        OutreachChannel.EMAIL,
        SuppressionReason.UNSUBSCRIBED,
        detail="Recipient used the unsubscribe link.",
    )

    # Flag the lead so the scoring engine's compliance gate picks it up.
    # Looked up by the blind-index hash, not the (encrypted, non-deterministic)
    # contact_email column itself -- see app/services/pii.py.
    normalized = normalize_identifier(email, OutreachChannel.EMAIL)
    lead = (
        await db.execute(
            select(Lead).where(Lead.contact_email_hash == email_blind_index(normalized))
        )
    ).scalar_one_or_none()
    if lead is not None:
        lead.do_not_contact = True
        lead.do_not_contact_reason = "Unsubscribed via email link"
        await db.flush()

    logger.info("Unsubscribe processed")
    return UnsubscribeResponse(
        message=(
            "You have been unsubscribed and will not be contacted again. "
            "No further action is needed."
        )
    )


@router.post(
    "/webhooks/bounce",
    response_model=BounceWebhookResponse,
    summary="Process delivery-failure webhooks",
    description=(
        "Receives SendGrid event webhooks. Hard bounces and spam complaints "
        "permanently suppress the address and flag the lead do-not-contact."
    ),
)
async def bounce_webhook(
    raw_body: bytes = Depends(verify_sendgrid_webhook),
    db: AsyncSession = Depends(get_db),
) -> BounceWebhookResponse:
    """Process delivery-failure events from SendGrid.

    Signature verification runs against raw untouched body bytes via
    ``verify_sendgrid_webhook`` BEFORE JSON parsing. Fails closed in production.

    Idempotent: duplicate provider event IDs are skipped without double side effects.

    Args:
        raw_body: The verified raw request body bytes.
        db: Active database session.

    Returns:
        Counts of processed and suppressed events.
    """
    import json

    try:
        data = json.loads(raw_body.decode("utf-8"))
        if isinstance(data, dict):
            data = [data]
        events = [BounceWebhookEvent.model_validate(e) for e in data]
    except Exception as exc:
        logger.warning("Failed to parse SendGrid webhook payload", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON webhook payload",
        ) from exc

    processed = 0
    suppressed = 0

    for event in events:
        event_id = getattr(event, "sg_event_id", None) or getattr(event, "event_id", None)
        if event_id:
            existing_event = (
                await db.execute(
                    select(ProcessedWebhookEvent.id).where(
                        ProcessedWebhookEvent.event_id == event_id,
                        ProcessedWebhookEvent.provider == "sendgrid",
                    )
                )
            ).scalar_one_or_none()
            if existing_event is not None:
                logger.info("Webhook event already processed (idempotent skip)")
                processed += 1
                continue

        bounce_type = classify_sendgrid_event(event.event, event.reason)
        occurred_at = (
            datetime.fromtimestamp(event.timestamp, tz=timezone.utc)
            if event.timestamp
            else None
        )

        draft_id = None
        if event.sg_message_id:
            found = (
                await db.execute(
                    select(OutreachDraft.id).where(
                        OutreachDraft.provider_message_id == event.sg_message_id
                    )
                )
            ).scalar_one_or_none()
            draft_id = found

        recorded = await record_bounce(
            db,
            identifier=event.email,
            bounce_type=bounce_type,
            provider_event=event.event,
            provider_message_id=event.sg_message_id,
            reason=event.reason,
            draft_id=draft_id,
            occurred_at=occurred_at,
        )
        processed += 1

        if event_id:
            db.add(
                ProcessedWebhookEvent(
                    event_id=event_id,
                    provider="sendgrid",
                    processed_at=utcnow(),
                )
            )

        if recorded.suppressed:
            suppressed += 1
            normalized = normalize_identifier(event.email, OutreachChannel.EMAIL)
            lead = (
                await db.execute(
                    select(Lead).where(Lead.contact_email_hash == email_blind_index(normalized))
                )
            ).scalar_one_or_none()
            if lead is not None:
                lead.do_not_contact = True
                lead.do_not_contact_reason = f"Email {bounce_type.value}: {event.reason or ''}"
                await db.flush()

    logger.info(
        "Bounce webhook processed",
        extra={"processed": processed, "suppressed": suppressed},
    )
    return BounceWebhookResponse(processed=processed, suppressed=suppressed)


# ---------------------------------------------------------------------------
# SendGrid health-monitor webhook (machine-to-machine, called by n8n)
# ---------------------------------------------------------------------------


@router.post(
    "/pause",
    response_model=DomainPauseResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["outreach", "webhooks"],
    summary="Pause outreach for a domain (n8n health-monitor webhook)",
    description=(
        "Called by the n8n SendGrid health-monitor workflow when a sending "
        "domain breaches a deliverability threshold (bounce rate > 5%, "
        "spam-complaint rate > 0.1%, or a sharp open-rate drop). "
        "Transitions all PENDING_REVIEW drafts for that domain to PAUSED and "
        "logs the event. Does NOT send, approve, or reject anything. "
        "Authentication: X-Webhook-Secret header (see AGENTS.md section 5 and "
        "app/api/deps.verify_webhook_secret)."
    ),
)
async def pause_domain(
    payload: DomainPauseRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_webhook_secret),
) -> DomainPauseResponse:
    """Pause all pending outreach drafts for the given domain.

    Validates the ``X-Webhook-Secret`` header before taking any action.
    Delegates all business logic to
    :func:`app.services.sendgrid_health.pause_domain_outreach` and commits
    the transaction only on success.

    Args:
        payload: Validated request body containing domain, alert type, and
            metric values.
        db: Active database session injected by FastAPI.
        _: Webhook-secret validation result (unused beyond the dependency
            raising 401 on failure).

    Returns:
        A :class:`DomainPauseResponse` with the alert log ID and count of
        paused drafts.

    Raises:
        HTTPException: 401 if the webhook secret is invalid or missing.
        HTTPException: 500 for any unexpected database error (global handler).
    """
    logger.info(
        "Outreach pause webhook received",
        extra={
            "domain": payload.domain,
            "alert_type": payload.alert_type,
            "metric_value": payload.metric_value,
        },
    )

    try:
        result = await pause_domain_outreach(db=db, payload=payload)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Outreach pause webhook failed — transaction rolled back",
            extra={"domain": payload.domain, "alert_type": payload.alert_type},
        )
        raise

    logger.info(
        "Outreach pause webhook completed successfully",
        extra={
            "domain": result.domain,
            "alert_log_id": str(result.alert_log_id),
            "drafts_paused": result.drafts_paused,
        },
    )
    return result
