"""Lead trigger-event signal routes.

Signal scans are triggered by a BD rep against a specific lead -- mirroring
the website audit agent's on-demand pattern (app/api/v1/audits.py): a scan
can crawl the lead's own website and/or call the paid Google Place Details
API, so a human-initiated, attributable request is what keeps that
defensible and cost-bounded. There is no bulk or automatic path; nothing in
this codebase schedules a scan (see app/services/signal_scanner.py's module
docstring).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_write_access
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.signal import LeadSignal
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.signal import LeadSignalResponse, SignalScanResponse
from app.services.signal_scanner import scan_lead_for_signals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["signals"])


async def _get_lead_or_404(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    """Fetch a lead or raise 404.

    Args:
        db: Active database session.
        lead_id: Identifier of the lead.

    Returns:
        The lead.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


@router.post(
    "/{lead_id}/signals/scan",
    response_model=SignalScanResponse,
    summary="Scan a lead for trigger-event signals",
    description=(
        "Checks the lead's careers/jobs page for changes, and -- if the lead has a "
        "linked Google Places candidate -- its business status and review count. "
        "The Places-derived checks are cost-guarded the same way discovery search is "
        "(daily budget cap, test-mode request cap) and are skipped rather than failing "
        "the whole scan if the budget or cap is exhausted."
    ),
)
async def scan_signals(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> SignalScanResponse:
    """Run an on-demand signal scan for one lead.

    Args:
        lead_id: Identifier of the lead to scan.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        What was checked, skipped, and newly detected.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    lead = await _get_lead_or_404(db, lead_id)

    outcome = await scan_lead_for_signals(db, lead)

    logger.info(
        "Signal scan requested",
        extra={
            "lead_id": str(lead_id),
            "user_id": str(user.id),
            "new_signals": len(outcome.new_signals),
        },
    )
    return SignalScanResponse(
        lead_id=lead_id,
        new_signals=[LeadSignalResponse.model_validate(s) for s in outcome.new_signals],
        checked=outcome.checked,
        skipped=outcome.skipped,
    )


@router.get(
    "/{lead_id}/signals",
    response_model=list[LeadSignalResponse],
    summary="List a lead's detected signals",
    description="Returns every trigger event ever detected for this lead, most recent first.",
)
async def list_signals(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LeadSignalResponse]:
    """List a lead's signal history.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The lead's signals, most recently detected first.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    await _get_lead_or_404(db, lead_id)

    rows = (
        (
            await db.execute(
                select(LeadSignal)
                .where(LeadSignal.lead_id == lead_id)
                .order_by(LeadSignal.detected_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [LeadSignalResponse.model_validate(row) for row in rows]


@router.post(
    "/{lead_id}/signals/{signal_id}/acknowledge",
    response_model=LeadSignalResponse,
    summary="Acknowledge a detected signal",
    description=(
        "Marks a signal as seen/actioned by a rep. An acknowledged signal no longer "
        "boosts the lead to the top of the outreach queue (GET /leads) -- see "
        "LeadResponse.active_signal_count."
    ),
)
async def acknowledge_signal(
    lead_id: uuid.UUID,
    signal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadSignalResponse:
    """Mark a signal as acknowledged.

    Args:
        lead_id: Identifier of the lead the signal belongs to.
        signal_id: Identifier of the signal.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated signal.

    Raises:
        HTTPException: 404 if no such signal exists for this lead.
    """
    signal = await db.get(LeadSignal, signal_id)
    if signal is None or signal.lead_id != lead_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")

    if signal.acknowledged_at is None:
        signal.acknowledged_at = utcnow()
        await db.flush()
        logger.info(
            "Signal acknowledged",
            extra={"lead_id": str(lead_id), "signal_id": str(signal_id), "user_id": str(user.id)},
        )
    return LeadSignalResponse.model_validate(signal)
