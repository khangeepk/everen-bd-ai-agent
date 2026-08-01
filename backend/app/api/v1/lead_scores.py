"""Lead scoring routes.

Scoring is compute-on-demand rather than automatic on every lead change --
running the Fit search calls the embeddings API and the Need component reads
the latest audit, so triggering it explicitly (by a rep, or by a scheduled
task later) keeps costs and staleness visible rather than hidden behind every
lead edit.

Every computation is stored as a new row (see app/db/models/lead_score.py), so
scoring history is auditable: a lead's trajectory from Cold to Hot, or the
moment a Do-Not-Contact flag started overriding its label, is all on the record.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_write_access
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.lead_score import LeadScore
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.lead_score import (
    ComponentScoreResponse,
    LeadScoreResponse,
    PaginatedLeadScores,
)
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.lead_scoring import score_lead
from app.services.lead_signals import build_score_breakdown

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["lead-scoring"])


def _split_reasons(text: str | None) -> list[str]:
    """Split stored newline-joined reasons back into a list.

    Args:
        text: The stored reasons text, or None.

    Returns:
        A list of reason strings, empty if ``text`` is None or blank.
    """
    if not text:
        return []
    return [line for line in text.split("\n") if line]


def _to_response(row: LeadScore) -> LeadScoreResponse:
    """Convert a stored LeadScore row into its API response shape.

    Args:
        row: The persisted score.

    Returns:
        The API response.
    """
    return LeadScoreResponse(
        id=row.id,
        lead_id=row.lead_id,
        need=ComponentScoreResponse(value=row.need_score, reasons=_split_reasons(row.need_reasons)),
        fit=ComponentScoreResponse(value=row.fit_score, reasons=_split_reasons(row.fit_reasons)),
        contactability=ComponentScoreResponse(
            value=row.contactability_score, reasons=_split_reasons(row.contactability_reasons)
        ),
        revenue=ComponentScoreResponse(
            value=row.revenue_score, reasons=_split_reasons(row.revenue_reasons)
        ),
        compliance=ComponentScoreResponse(
            value=row.compliance_score, reasons=_split_reasons(row.compliance_reasons)
        ),
        gate_triggered=row.gate_triggered,
        gate_reasons=_split_reasons(row.gate_reasons),
        total_score=row.total_score,
        label=row.label,
        formula_version=row.formula_version,
        computed_at=row.computed_at,
    )


@router.post(
    "/{lead_id}/score",
    response_model=LeadScoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Compute and store a lead score",
    description=(
        "Computes Need(30%)+Fit(25%)+Contactability(20%)+Revenue(15%)+ComplianceRisk(10%) "
        "and bands it to Hot/Warm/Cold/Do-Not-Contact. A triggered compliance gate "
        "(do_not_contact=true) forces Do-Not-Contact regardless of the weighted total. "
        "Stores the full breakdown as a new history row."
    ),
)
async def compute_lead_score(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadScoreResponse:
    """Compute a fresh score for a lead and persist it.

    Args:
        lead_id: The lead to score.
        db: Active database session.
        user: The authenticated caller, recorded as the computer of record.

    Returns:
        The newly computed and stored score.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    breakdown = await build_score_breakdown(db, lead, kb)
    result = score_lead(breakdown)

    row = LeadScore(
        lead_id=lead.id,
        need_score=breakdown.need.value,
        fit_score=breakdown.fit.value,
        contactability_score=breakdown.contactability.value,
        revenue_score=breakdown.revenue.value,
        compliance_score=breakdown.compliance.value,
        need_reasons="\n".join(breakdown.need.reasons),
        fit_reasons="\n".join(breakdown.fit.reasons),
        contactability_reasons="\n".join(breakdown.contactability.reasons),
        revenue_reasons="\n".join(breakdown.revenue.reasons),
        compliance_reasons="\n".join(breakdown.compliance.reasons),
        gate_triggered=breakdown.gate.triggered,
        gate_reasons="\n".join(breakdown.gate.reasons),
        total_score=result.total_score,
        label=result.label,
        formula_version=result.formula_version,
        computed_by_id=user.id,
        computed_at=utcnow(),
    )
    db.add(row)
    await db.flush()

    logger.info(
        "Lead score computed",
        extra={
            "lead_id": str(lead.id),
            "total_score": result.total_score,
            "label": result.label.value,
            "gate_triggered": breakdown.gate.triggered,
            "user_id": str(user.id),
        },
    )
    return _to_response(row)


@router.get(
    "/{lead_id}/score",
    response_model=LeadScoreResponse,
    summary="Get a lead's latest score",
    description="Returns the most recently computed score. Does not recompute.",
)
async def get_latest_score(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LeadScoreResponse:
    """Fetch the most recent stored score for a lead.

    Args:
        lead_id: The lead to look up.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The latest score.

    Raises:
        HTTPException: 404 if the lead has never been scored, or does not exist.
    """
    if await db.get(Lead, lead_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    row = (
        await db.execute(
            select(LeadScore)
            .where(LeadScore.lead_id == lead_id)
            .order_by(LeadScore.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This lead has not been scored yet"
        )
    return _to_response(row)


@router.get(
    "/{lead_id}/score/history",
    response_model=PaginatedLeadScores,
    summary="Get a lead's score history",
    description="Returns every past computation for a lead, newest first.",
)
async def get_score_history(
    lead_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedLeadScores:
    """List historical scores for a lead.

    Args:
        lead_id: The lead to look up.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100 per AGENTS.md section 9.3.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        A page of historical scores.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    if await db.get(Lead, lead_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    total = (
        await db.execute(
            select(func.count()).select_from(LeadScore).where(LeadScore.lead_id == lead_id)
        )
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(LeadScore)
                .where(LeadScore.lead_id == lead_id)
                .order_by(LeadScore.computed_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )

    return PaginatedLeadScores(
        items=[_to_response(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
