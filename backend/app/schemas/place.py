"""Pydantic v2 schemas for Places discovery.

Note the asymmetry between response models: :class:`PlaceSearchResultResponse`
carries display-only Google Maps Content that the client must render live and
not store, while :class:`PlaceCandidateResponse` reflects what is actually
persisted -- ``place_id`` plus TTL-bounded coordinates.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from app.db.models.place import CandidateStatus


class PlaceSearchRequest(BaseModel):
    """A location + industry search."""

    industry: str = Field(min_length=2, max_length=200, examples=["dental clinics"])
    postal_code: str = Field(min_length=3, max_length=20, examples=["78701"])
    country: str | None = Field(default=None, max_length=100, examples=["US"])
    radius_meters: int = Field(default=5000, ge=100, le=50000)
    max_results: int = Field(default=20, ge=1, le=20)


class PlaceSearchResultResponse(BaseModel):
    """One search hit, for immediate display only.

    WARNING: ``display_name``, ``formatted_address``, ``website``, and
    ``phone`` are Google Maps Content. Clients may render these but must not
    persist them. Only ``place_id`` is safe to store.
    """

    place_id: str
    display_name: str | None = None
    formatted_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website: str | None = None
    phone: str | None = None
    business_status: str | None = None
    is_new: bool = Field(
        default=True, description="False when this place_id was already staged."
    )


class PlaceSearchResponse(BaseModel):
    """The outcome of a discovery run."""

    search_id: uuid.UUID
    industry: str
    postal_code: str
    total_found: int
    new_candidates: int
    duplicate_candidates: int
    results: list[PlaceSearchResultResponse]
    attribution: str = Field(
        default="Powered by Google Maps",
        description=(
            "Attribution required when displaying Places content. Render the "
            "Google Maps logo where space allows."
        ),
    )
    storage_notice: str = Field(
        default=(
            "Only place_id and coordinates are stored. Business names, addresses, "
            "phone numbers, and websites are display-only under Google Maps "
            "Platform Service Specific Terms 10.3 and must not be persisted."
        )
    )


class PlaceCandidateResponse(BaseModel):
    """A staged candidate as actually stored.

    Carries no business name or address by design.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    place_id: str
    latitude: float | None
    longitude: float | None
    coordinates_expire_at: datetime | None
    source: str
    discovered_at: datetime
    last_seen_at: datetime
    times_seen: int
    status: CandidateStatus
    confidence_score: float
    lead_id: uuid.UUID | None


class PaginatedCandidates(BaseModel):
    """A page of staged candidates."""

    items: list[PlaceCandidateResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class PromoteCandidateRequest(BaseModel):
    """Promote a staged candidate into a lead.

    Contact fields are REQUIRED from the caller and must originate from a
    source that permits storage -- the company's own website, a licensed data
    vendor, or manual research. Copying them from the Places response would
    breach the caching restriction, so this endpoint cannot fill them for you.
    """

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Business name from a storable source, NOT from the Places response.",
    )
    category: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_title: str | None = Field(default=None, max_length=150)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=50)
    website: HttpUrl | None = None
    linkedin_url: HttpUrl | None = None
    country: str | None = Field(default=None, max_length=100)
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str | None = None
    enrichment_source: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "Where the contact data came from, e.g. 'company website', "
            "'apollo.io', 'manual research'. Recorded for audit."
        ),
    )


class RetentionSweepResponse(BaseModel):
    """Result of a manual coordinate retention sweep."""

    rows_purged: int
