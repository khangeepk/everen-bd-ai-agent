"""SendGrid deliverability health service.

Provides the core business logic for the n8n health-monitor webhook:
bulk-pausing ``OutreachDraft`` rows for a domain that has breached a
deliverability threshold, auditing every status transition, and recording
the event in ``AlertLog``.

This module contains NO send logic and no code that could dispatch an
email. It is the backend half of the n8n → FastAPI alert pipeline.

See ``app/api/v1/outreach.py`` (``POST /api/v1/outreach/pause``) for the
route handler that calls into this service.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.alert_log import AlertLog
from app.db.models.outreach import DraftStatus, OutreachAuditLog, OutreachDraft
from app.schemas.alert_log import DomainPauseRequest, DomainPauseResponse

logger = logging.getLogger(__name__)


async def pause_domain_outreach(
    db: AsyncSession,
    payload: DomainPauseRequest,
) -> DomainPauseResponse:
    """Pause all pending outreach drafts for a domain and log the alert.

    Called exclusively by ``POST /api/v1/outreach/pause`` when the n8n
    SendGrid health-monitor detects a deliverability threshold breach.

    Behaviour:
    * Selects every ``OutreachDraft`` with ``status = PENDING_REVIEW``
      whose ``sender_email`` ends with ``@<domain>``.
    * Transitions each draft to ``PAUSED`` and writes one
      ``OutreachAuditLog`` row per draft (AGENTS.md section 8.5).
    * Inserts one ``AlertLog`` row recording the event.
    * Commits nothing — the caller (route handler) owns the transaction.

    No email is sent, no Celery task is queued, and no draft is moved to
    any status that permits sending. This is a pure pause / log operation.

    Args:
        db: Active async SQLAlchemy session (transaction not yet committed).
        payload: Validated ``DomainPauseRequest`` from the webhook body.

    Returns:
        A ``DomainPauseResponse`` with the alert log ID, domain, and count
        of drafts that were paused.

    Raises:
        SQLAlchemyError: Propagated if any DB operation fails; the caller
            should allow the session's transaction to roll back.
    """
    domain = payload.domain.strip().lower()
    suffix = f"@{domain}"

    logger.info(
        "Deliverability alert received — scanning for drafts to pause",
        extra={
            "domain": domain,
            "alert_type": payload.alert_type,
            "metric_value": payload.metric_value,
            "threshold_value": payload.threshold_value,
        },
    )

    # -----------------------------------------------------------------------
    # 1. Fetch all PENDING_REVIEW drafts for this domain.
    # -----------------------------------------------------------------------
    result = await db.execute(
        select(OutreachDraft).where(
            OutreachDraft.status == DraftStatus.PENDING_REVIEW,
            OutreachDraft.sender_email.ilike(f"%{suffix}"),
        )
    )
    drafts: list[OutreachDraft] = list(result.scalars().all())

    if not drafts:
        logger.info(
            "No PENDING_REVIEW drafts found for domain — nothing to pause",
            extra={"domain": domain, "alert_type": payload.alert_type},
        )

    # -----------------------------------------------------------------------
    # 2. Transition each draft to PAUSED and write an audit log row.
    #    Per AGENTS.md section 8.5: every status transition must be recorded.
    #    changed_by_id is None because this is a system/automated action.
    # -----------------------------------------------------------------------
    paused_count = 0
    for draft in drafts:
        draft.status = DraftStatus.PAUSED
        db.add(draft)

        db.add(
            OutreachAuditLog(
                draft_id=draft.id,
                old_status=DraftStatus.PENDING_REVIEW,
                new_status=DraftStatus.PAUSED,
                changed_by_id=None,  # automated system action
                changed_at=utcnow(),
                note=(
                    f"Auto-paused by SendGrid health monitor: "
                    f"{payload.alert_type} = {payload.metric_value:.4f} "
                    f"(threshold {payload.threshold_value:.4f}) on domain '{domain}'"
                ),
            )
        )
        paused_count += 1

    await db.flush()

    logger.info(
        "Drafts paused by health monitor",
        extra={
            "domain": domain,
            "alert_type": payload.alert_type,
            "drafts_paused": paused_count,
        },
    )

    # -----------------------------------------------------------------------
    # 3. Insert an AlertLog row.
    # -----------------------------------------------------------------------
    alert = AlertLog(
        id=uuid.uuid4(),
        alert_type=payload.alert_type,
        domain=domain,
        metric_value=payload.metric_value,
        threshold_value=payload.threshold_value,
        triggered_at=utcnow(),
        resolved_at=None,
        drafts_paused_count=paused_count,
    )
    db.add(alert)
    await db.flush()

    logger.info(
        "AlertLog row created",
        extra={
            "alert_log_id": str(alert.id),
            "domain": domain,
            "alert_type": payload.alert_type,
            "drafts_paused": paused_count,
        },
    )

    return DomainPauseResponse(
        alert_log_id=alert.id,
        domain=domain,
        drafts_paused=paused_count,
        message=(
            f"Outreach paused for domain '{domain}'. "
            f"{paused_count} draft(s) moved to PAUSED. "
            f"Alert: {payload.alert_type} = {payload.metric_value:.4f} "
            f"(threshold {payload.threshold_value:.4f})."
        ),
    )
