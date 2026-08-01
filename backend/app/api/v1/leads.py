"""Leads routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_write_access
from app.db.base import utcnow
from app.db.models.lead import Lead, LeadStatus
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.lead import LeadCreate, LeadResponse, LeadUpdate, PaginatedLeads
from app.services.signal_queue import attach_signal_summary, signal_summary_columns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post(
    "",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a lead",
    description="Creates a lead in status 'new'. Does not trigger any outreach.",
)
async def create_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadResponse:
    """Create a lead.

    Args:
        payload: The lead to create.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The created lead.

    Raises:
        HTTPException: 409 if the contact email is already on another lead.
    """
    data = payload.model_dump()
    for url_field in ("website", "linkedin_url"):
        if data.get(url_field) is not None:
            data[url_field] = str(data[url_field])
    # contact_email is set via set_contact_email() below so its blind-index
    # hash (uq_leads_contact_email_hash) stays in sync -- see
    # app/db/models/lead.py.
    raw_email = data.pop("contact_email", None)
    email = str(raw_email).lower() if raw_email is not None else None

    lead = Lead(**data, status=LeadStatus.NEW)
    lead.set_contact_email(email)
    if lead.gdpr_consent:
        lead.gdpr_consent_recorded_at = utcnow()
    db.add(lead)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.exception("Duplicate lead contact_email")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A lead with this contact email already exists",
        ) from exc

    logger.info("Lead created", extra={"lead_id": str(lead.id), "user_id": str(user.id)})
    await attach_signal_summary(db, lead)
    return LeadResponse.model_validate(lead)


@router.get(
    "",
    response_model=PaginatedLeads,
    summary="List leads",
    description="Returns a page of leads, filterable by status and category.",
)
async def list_leads(
    status_filter: LeadStatus | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None, max_length=100),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedLeads:
    """List leads with filtering and pagination.

    Ordering surfaces high-signal leads first: a lead with at least one
    unacknowledged trigger-event signal (job posting, business status
    change, review count jump -- see app/services/signal_queue.py) sorts
    ahead of confidence_score, since a fresh trigger event is a
    time-sensitive reason to reach out now regardless of how the lead
    originally scored.

    Args:
        status_filter: Optional pipeline status filter.
        category: Optional category filter.
        min_confidence: Minimum confidence score, inclusive.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100 per AGENTS.md section 9.3.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        A page of leads plus the total match count.
    """
    filters = [Lead.confidence_score >= min_confidence]
    if status_filter is not None:
        filters.append(Lead.status == status_filter)
    if category:
        filters.append(Lead.category == category)

    total = (
        await db.execute(select(func.count()).select_from(Lead).where(*filters))
    ).scalar_one()

    active_signal_count, latest_signal_type, latest_signal_at = signal_summary_columns()

    result = await db.execute(
        select(Lead, active_signal_count, latest_signal_type, latest_signal_at)
        .where(*filters)
        .order_by(
            active_signal_count.desc(),
            Lead.confidence_score.desc(),
            Lead.created_at.desc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    items: list[LeadResponse] = []
    for lead, signal_count, signal_type, signal_at in result.all():
        lead.active_signal_count = int(signal_count or 0)
        lead.latest_signal_type = signal_type
        lead.latest_signal_at = signal_at
        items.append(LeadResponse.model_validate(lead))

    return PaginatedLeads(
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Get a lead",
    description="Retrieves a single lead by its identifier.",
)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LeadResponse:
    """Retrieve one lead.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The requested lead.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    await attach_signal_summary(db, lead)
    return LeadResponse.model_validate(lead)


@router.patch(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Update a lead",
    description="Applies a partial update. Status changes here never send anything.",
)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadResponse:
    """Partially update a lead.

    Note:
        Setting ``status`` to ``contacted`` records that contact happened; it
        does not itself send anything. Delivery only occurs through the
        approved-outreach endpoint (AGENTS.md section 8).

    Args:
        lead_id: Identifier of the lead.
        payload: Fields to change.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated lead.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field in {"website", "linkedin_url"} and value is not None:
            value = str(value)
        if field == "contact_email":
            # Goes through set_contact_email() to keep contact_email_hash
            # (the actual unique-constraint/lookup key) in sync -- see
            # app/db/models/lead.py.
            lead.set_contact_email(str(value).lower() if value is not None else None)
            continue
        if field == "gdpr_consent" and value and not lead.gdpr_consent:
            # Newly granted, not just re-saved -- stamp when it happened.
            lead.gdpr_consent_recorded_at = utcnow()
        setattr(lead, field, value)

    await db.flush()
    logger.info(
        "Lead updated",
        extra={"lead_id": str(lead.id), "user_id": str(user.id), "fields": list(updates)},
    )
    await attach_signal_summary(db, lead)
    return LeadResponse.model_validate(lead)


@router.post(
    "/{lead_id}/detect-language",
    response_model=LeadResponse,
    summary="Trigger language detection for a lead",
    description=(
        "Immediately runs heuristic language detection against the lead's website "
        "and country, saving the result to detected_language. Returns the updated lead."
    ),
)
async def detect_language_endpoint(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadResponse:
    """Run language detection on demand for a lead.

    Args:
        lead_id: Identifier of the lead.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The updated lead with ``detected_language`` populated.

    Raises:
        HTTPException: 404 if no such lead exists.
    """
    from app.services.language_detection import detect_language

    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    detected = await detect_language(lead.website, lead.country)
    lead.detected_language = detected
    await db.flush()

    logger.info(
        "Manual language detection completed",
        extra={"lead_id": str(lead.id), "detected_language": detected, "user_id": str(user.id)},
    )
    await attach_signal_summary(db, lead)
    return LeadResponse.model_validate(lead)

