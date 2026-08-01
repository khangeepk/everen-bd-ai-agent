"""Places discovery routes.

Search returns display-only Google Maps Content in the response body while
persisting only ``place_id`` and TTL-bounded coordinates. Promotion to a lead
requires the caller to supply contact data from a storable source.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_approver, require_write_access
from app.db.base import utcnow
from app.db.models.lead import Lead, LeadSource, LeadStatus
from app.db.models.place import CandidateStatus, PlaceCandidate
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.place import (
    PaginatedCandidates,
    PlaceCandidateResponse,
    PlaceSearchRequest,
    PlaceSearchResponse,
    PlaceSearchResultResponse,
    PromoteCandidateRequest,
    RetentionSweepResponse,
)
from app.schemas.lead import LeadResponse
from app.services.cost_guard import BudgetExceededError
from app.services.signal_queue import attach_signal_summary
from app.services.places import (
    GooglePlacesClient,
    PlaceDiscoveryService,
    PlacesError,
    PlacesTestModeLimitExceeded,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/places", tags=["places"])


def get_discovery_service(db: AsyncSession = Depends(get_db)) -> PlaceDiscoveryService:
    """Construct a discovery service for the request.

    Args:
        db: Active database session.

    Returns:
        A configured :class:`PlaceDiscoveryService`.
    """
    return PlaceDiscoveryService(db=db, client=GooglePlacesClient())


@router.post(
    "/search",
    response_model=PlaceSearchResponse,
    summary="Search for businesses by location and industry",
    description=(
        "Runs a Google Places text search and stages new results. Only place_id "
        "and coordinates are persisted; names, addresses, phone numbers, and "
        "websites in the response are display-only and must not be stored by "
        "the client. Dedup keys on place_id."
    ),
)
async def search_places(
    payload: PlaceSearchRequest,
    service: PlaceDiscoveryService = Depends(get_discovery_service),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> PlaceSearchResponse:
    """Discover businesses by location and industry.

    Args:
        payload: Search parameters.
        service: Discovery service.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        Search results plus dedup counts.

    Raises:
        HTTPException: 400 for unusable input, 502 if Places fails.
    """
    try:
        outcome = await service.discover(
            industry=payload.industry,
            postal_code=payload.postal_code,
            country=payload.country,
            radius_meters=payload.radius_meters,
            max_results=payload.max_results,
            executed_by_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlacesTestModeLimitExceeded as exc:
        # Checked before the broader PlacesError below, since this subclasses
        # it but is a deliberate safety-rail refusal, not a provider failure.
        logger.warning("Places test-mode cap refused a request")
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except PlacesError as exc:
        logger.exception("Places discovery failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Place search provider unavailable"
        ) from exc
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc

    known = {
        row
        for row in (
            await db.execute(
                select(PlaceCandidate.place_id).where(
                    PlaceCandidate.place_id.in_([r.place_id for r in outcome.results] or [""]),
                    PlaceCandidate.times_seen > 1,
                )
            )
        ).scalars()
    }

    logger.info(
        "Place search served",
        extra={
            "user_id": str(user.id),
            "found": outcome.total_found,
            "new": outcome.new_candidates,
        },
    )
    return PlaceSearchResponse(
        search_id=outcome.search_id,
        industry=payload.industry,
        postal_code=payload.postal_code,
        total_found=outcome.total_found,
        new_candidates=outcome.new_candidates,
        duplicate_candidates=outcome.duplicate_candidates,
        results=[
            PlaceSearchResultResponse(
                place_id=result.place_id,
                display_name=result.display_name,
                formatted_address=result.formatted_address,
                latitude=result.latitude,
                longitude=result.longitude,
                website=result.website,
                phone=result.phone,
                business_status=result.business_status,
                is_new=result.place_id not in known,
            )
            for result in outcome.results
        ],
    )


@router.get(
    "/candidates",
    response_model=PaginatedCandidates,
    summary="List staged place candidates",
    description=(
        "Returns staged candidates. Carries no business name or address -- "
        "re-fetch those live from Places Details using place_id."
    ),
)
async def list_candidates(
    status_filter: CandidateStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedCandidates:
    """List staged candidates with pagination.

    Args:
        status_filter: Optional lifecycle status filter.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        A page of candidates.
    """
    filters = []
    if status_filter is not None:
        filters.append(PlaceCandidate.status == status_filter)

    total = (
        await db.execute(select(func.count()).select_from(PlaceCandidate).where(*filters))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(PlaceCandidate)
                .where(*filters)
                .order_by(PlaceCandidate.discovered_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )

    return PaginatedCandidates(
        items=[PlaceCandidateResponse.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post(
    "/candidates/{candidate_id}/promote",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Promote a candidate into a lead",
    description=(
        "Creates a lead from a staged candidate. Contact fields must be supplied "
        "by the caller from a source that permits storage -- they cannot be "
        "copied from the Places response. Creates a lead only; sends nothing."
    ),
)
async def promote_candidate(
    candidate_id: uuid.UUID,
    payload: PromoteCandidateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> LeadResponse:
    """Promote a staged candidate into a lead.

    Args:
        candidate_id: Identifier of the candidate.
        payload: Contact data from a storable source.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The created lead.

    Raises:
        HTTPException: 404 if the candidate is missing, 409 if already promoted.
    """
    candidate = await db.get(PlaceCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    if candidate.lead_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate has already been promoted to a lead",
        )

    data = payload.model_dump(exclude={"enrichment_source"})
    for url_field in ("website", "linkedin_url"):
        if data.get(url_field) is not None:
            data[url_field] = str(data[url_field])
    # contact_email is set via set_contact_email() below so its blind-index
    # hash (uq_leads_contact_email_hash) stays in sync -- see
    # app/db/models/lead.py.
    raw_email = data.pop("contact_email", None)
    email = str(raw_email).lower() if raw_email is not None else None

    notes = data.get("notes") or ""
    provenance = (
        f"Discovered via Google Places (place_id={candidate.place_id}). "
        f"Contact data source: {payload.enrichment_source}."
    )
    data["notes"] = f"{notes}\n\n{provenance}".strip()

    lead = Lead(
        **data,
        source=LeadSource.GOOGLE_PLACES,
        source_detail=f"place_id={candidate.place_id}",
        status=LeadStatus.NEW,
    )
    lead.set_contact_email(email)
    db.add(lead)
    await db.flush()

    candidate.lead_id = lead.id
    candidate.status = CandidateStatus.PROMOTED
    candidate.last_seen_at = utcnow()
    await db.flush()

    logger.info(
        "Candidate promoted to lead",
        extra={
            "candidate_id": str(candidate.id),
            "lead_id": str(lead.id),
            "user_id": str(user.id),
            "enrichment_source": payload.enrichment_source,
        },
    )
    await attach_signal_summary(db, lead)
    return LeadResponse.model_validate(lead)


@router.post(
    "/retention/sweep",
    response_model=RetentionSweepResponse,
    summary="Purge expired Places coordinates",
    description=(
        "Deletes cached coordinates past the 30-day retention window required "
        "by Google Maps Platform Service Specific Terms 10.3. Normally runs on "
        "a schedule; this endpoint is for manual verification."
    ),
)
async def sweep_retention(
    service: PlaceDiscoveryService = Depends(get_discovery_service),
    user: User = Depends(require_approver),
) -> RetentionSweepResponse:
    """Run the coordinate retention sweep on demand.

    Args:
        service: Discovery service.
        user: The authenticated caller, who must hold an approver role.

    Returns:
        The number of rows redacted.
    """
    purged = await service.purge_expired_coordinates()
    logger.info("Manual retention sweep", extra={"user_id": str(user.id), "rows": purged})
    return RetentionSweepResponse(rows_purged=purged)
