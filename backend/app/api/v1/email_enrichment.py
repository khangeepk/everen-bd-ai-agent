"""Email-enrichment routes.

An enrichment run is triggered by a BD rep against a specific lead --
mirroring the audit and signal-scan patterns (app/api/v1/audits.py,
app/api/v1/signals.py): it can crawl the lead's own website, so a
human-initiated, attributable request is what keeps that defensible. There
is no bulk or automatic path.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_write_access
from app.db.base import utcnow
from app.db.models.email_enrichment import EmailEnrichmentAttempt
from app.db.models.lead import Lead
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.email_enrichment import (
    EmailCandidateResponse,
    EmailEnrichmentAttemptResponse,
    EmailEnrichmentScanResponse,
    EmailVerifyResponse,
)
from app.services.email_enrichment_scanner import enrich_lead_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["email-enrichment"])


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
    "/{lead_id}/email/enrich",
    response_model=EmailEnrichmentScanResponse,
    summary="Run the fallback email-discovery chain for a lead",
    description=(
        "Tries the lead's website contact/footer page first; only if that finds nothing "
        "does it fall back to a common-pattern name@domain guess. The highest-confidence "
        "candidate is applied to the lead as an UNVERIFIED email -- outreach drafting is "
        "blocked for it until a human confirms it via POST /leads/{id}/email/verify. A "
        "lead that already has a verified email is left untouched."
    ),
)
async def enrich_email(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> EmailEnrichmentScanResponse:
    """Run an on-demand email-enrichment scan for one lead.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        Every candidate found and which one, if any, was applied.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    lead = await _get_lead_or_404(db, lead_id)

    outcome = await enrich_lead_email(db, lead)

    logger.info(
        "Email enrichment requested",
        extra={
            "lead_id": str(lead_id),
            "user_id": str(user.id),
            "candidates_found": len(outcome.candidates),
            "applied": outcome.applied is not None,
        },
    )
    return EmailEnrichmentScanResponse(
        lead_id=lead_id,
        candidates=[
            EmailCandidateResponse(
                email=c.email,
                source=c.source,
                confidence_score=c.confidence_score,
                evidence=c.evidence,
            )
            for c in outcome.candidates
        ],
        applied=(
            EmailCandidateResponse(
                email=outcome.applied.email,
                source=outcome.applied.source,
                confidence_score=outcome.applied.confidence_score,
                evidence=outcome.applied.evidence,
            )
            if outcome.applied
            else None
        ),
        skipped_reason=outcome.skipped_reason,
    )


@router.get(
    "/{lead_id}/email/candidates",
    response_model=list[EmailEnrichmentAttemptResponse],
    summary="List every email candidate ever found for a lead",
    description="Full history, not just the one applied -- most recently detected first.",
)
async def list_email_candidates(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EmailEnrichmentAttemptResponse]:
    """List a lead's email-enrichment attempt history.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The lead's enrichment attempts, most recently detected first.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    await _get_lead_or_404(db, lead_id)

    rows = (
        (
            await db.execute(
                select(EmailEnrichmentAttempt)
                .where(EmailEnrichmentAttempt.lead_id == lead_id)
                .order_by(EmailEnrichmentAttempt.detected_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [EmailEnrichmentAttemptResponse.model_validate(row) for row in rows]


@router.post(
    "/{lead_id}/email/verify",
    response_model=EmailVerifyResponse,
    summary="Manually confirm a lead's contact email",
    description=(
        "Marks the lead's current contact_email as verified, lifting the block on email "
        "draft generation. Use this after checking the address by hand -- there is no "
        "automated verifier wired in yet."
    ),
)
async def verify_email(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> EmailVerifyResponse:
    """Mark a lead's contact email as manually verified.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated verification status.

    Raises:
        HTTPException: 404 if no such lead exists, 409 if the lead has no
            contact email to verify.
    """
    lead = await _get_lead_or_404(db, lead_id)

    if not lead.contact_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lead has no contact email on file to verify.",
        )

    if not lead.contact_email_verified:
        lead.contact_email_verified = True
        await db.flush()
        logger.info(
            "Contact email manually verified",
            extra={"lead_id": str(lead_id), "user_id": str(user.id)},
        )
    return EmailVerifyResponse(lead_id=lead_id, contact_email_verified=lead.contact_email_verified)
