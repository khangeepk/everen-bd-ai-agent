"""E2E tests: lead discovery (Places search -> candidate staging -> promotion).

Covers the happy path plus the edge cases called out for this phase:

* duplicate candidate (same place_id seen twice) and duplicate lead (same
  contact_email promoted twice)
* Places API failure (transport/5xx) and a rate-limit hit (429)
* "no email found" -- promoting a candidate with no discoverable contact email
  is a valid, non-error outcome that must flow through to the lead record
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import Depends
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.places import get_discovery_service
from app.db.session import get_db
from app.main import app
from app.services.places import (
    PlaceDiscoveryService,
    PlacesError,
    PlaceSearchResult,
)

pytestmark = pytest.mark.asyncio


@dataclass
class FakePlacesClient:
    """A scripted stand-in for :class:`GooglePlacesClient`.

    Attributes:
        results: Results to return on the next successful call.
        error: If set, raised instead of returning ``results``.
        calls: Every query string this fake was called with.
    """

    results: list[PlaceSearchResult] = field(default_factory=list)
    error: PlacesError | None = None
    calls: list[str] = field(default_factory=list)

    async def search_text(
        self, query: str, *, max_results: int = 20
    ) -> list[PlaceSearchResult]:
        """Return the scripted results, or raise the scripted error.

        Args:
            query: The search text (recorded, not used).
            max_results: Ignored by the fake.

        Returns:
            The scripted results.

        Raises:
            PlacesError: If one was configured.
        """
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.results


def _install_fake_places(fake: FakePlacesClient, session_factory) -> None:
    """Override the discovery-service dependency with one wired to ``fake``.

    Args:
        fake: The scripted client to inject.
        session_factory: Factory bound to the test engine, so the injected
            service shares the request's database session.
    """

    async def _override(db: AsyncSession = Depends(get_db)) -> PlaceDiscoveryService:
        # `get_db` is already overridden globally by the e2e_client fixture,
        # so this Depends(get_db) resolves to a session on the test engine,
        # not the real one -- we just swap in the fake Places client.
        return PlaceDiscoveryService(db=db, client=fake)

    app.dependency_overrides[get_discovery_service] = _override


ONE_RESULT = PlaceSearchResult(
    place_id="ChIJ_test_place_001",
    display_name="Congress Dental",
    formatted_address="123 Congress Ave, Austin, TX",
    latitude=30.27,
    longitude=-97.74,
    website="https://congressdental.example",
    phone="+15125550101",
    business_status="OPERATIONAL",
)


async def test_discovery_happy_path_stages_new_candidate(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """A fresh place_id is staged as a new candidate exactly once."""
    fake = FakePlacesClient(results=[ONE_RESULT])
    _install_fake_places(fake, e2e_session_factory)

    response = await e2e_client.post(
        "/api/v1/places/search",
        json={"industry": "dental clinics", "postal_code": "78701", "country": "US"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_found"] == 1
    assert body["new_candidates"] == 1
    assert body["duplicate_candidates"] == 0
    assert body["results"][0]["is_new"] is True
    # Display-only Google Maps Content is present in the response...
    assert body["results"][0]["display_name"] == "Congress Dental"

    candidates = await e2e_client.get("/api/v1/places/candidates")
    assert candidates.json()["total"] == 1
    # ...but never persisted; only place_id and coordinates are stored.
    stored = candidates.json()["items"][0]
    assert stored["place_id"] == "ChIJ_test_place_001"
    assert "display_name" not in stored


async def test_discovery_duplicate_place_id_does_not_create_second_row(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """The same place_id found on a later search updates, not duplicates."""
    fake = FakePlacesClient(results=[ONE_RESULT])
    _install_fake_places(fake, e2e_session_factory)

    await e2e_client.post(
        "/api/v1/places/search",
        json={"industry": "dental clinics", "postal_code": "78701"},
    )
    second = await e2e_client.post(
        "/api/v1/places/search",
        json={"industry": "dental clinics", "postal_code": "78701"},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["new_candidates"] == 0
    assert body["duplicate_candidates"] == 1
    assert body["results"][0]["is_new"] is False

    candidates = await e2e_client.get("/api/v1/places/candidates")
    assert candidates.json()["total"] == 1, "must not create a second row for the same place_id"


async def test_promoting_same_candidate_twice_is_rejected(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """A candidate already promoted to a lead cannot be promoted again."""
    fake = FakePlacesClient(results=[ONE_RESULT])
    _install_fake_places(fake, e2e_session_factory)
    await e2e_client.post(
        "/api/v1/places/search", json={"industry": "dental clinics", "postal_code": "78701"}
    )
    candidate_id = (await e2e_client.get("/api/v1/places/candidates")).json()["items"][0]["id"]

    payload = {
        "name": "Congress Dental LLC",
        "contact_email": "owner@congressdental.example",
        "enrichment_source": "company website",
    }
    first = await e2e_client.post(f"/api/v1/places/candidates/{candidate_id}/promote", json=payload)
    assert first.status_code == 201

    second = await e2e_client.post(
        f"/api/v1/places/candidates/{candidate_id}/promote",
        json={**payload, "contact_email": "someone-else@congressdental.example"},
    )
    assert second.status_code == 409


async def test_duplicate_lead_contact_email_is_rejected(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """Two leads may not share a contact_email (uq_leads_contact_email)."""
    payload = {"name": "Business One", "contact_email": "same@duplicate-lead.example"}
    first = await e2e_client.post("/api/v1/leads", json=payload)
    assert first.status_code == 201

    second = await e2e_client.post(
        "/api/v1/leads",
        json={
            "name": "A Different Business",
            "contact_email": "same@duplicate-lead.example",
        },
    )
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


async def test_places_api_failure_surfaces_as_502(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """A Places transport/5xx failure is reported as a gateway error, not a 500."""
    fake = FakePlacesClient(error=PlacesError("Places API error 500: internal error"))
    _install_fake_places(fake, e2e_session_factory)

    response = await e2e_client.post(
        "/api/v1/places/search",
        json={"industry": "dental clinics", "postal_code": "78701"},
    )

    assert response.status_code == 502
    assert "provider unavailable" in response.json()["detail"].lower()


async def test_places_rate_limit_hit_surfaces_as_502(
    e2e_client: AsyncClient,
    e2e_session_factory: async_sessionmaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 429 from Places is treated the same as any other provider failure.

    The Places client (see app.services.places.GooglePlacesClient.search_text)
    does not special-case 429 -- every non-2xx response becomes a PlacesError,
    which the route maps to 502 rather than retrying or crashing. Also
    verifies the failure is logged at ERROR (app.api.v1.places.search_places'
    ``logger.exception`` call), so a rate-limit hit is visible in aggregate
    error monitoring rather than only in the 502 response body.
    """
    fake = FakePlacesClient(error=PlacesError("Places API error 429: RESOURCE_EXHAUSTED"))
    _install_fake_places(fake, e2e_session_factory)

    import logging

    with caplog.at_level(logging.ERROR, logger="app.api.v1.places"):
        response = await e2e_client.post(
            "/api/v1/places/search", json={"industry": "dental clinics", "postal_code": "78701"}
        )

    assert response.status_code == 502
    # No candidates should have been staged from a call that never returned data.
    candidates = await e2e_client.get("/api/v1/places/candidates")
    assert candidates.json()["total"] == 0
    assert any(
        record.levelname == "ERROR" and "Places discovery failed" in record.message
        for record in caplog.records
    )


async def test_no_email_found_promotes_lead_with_null_contact_email(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """A candidate with no discoverable email is still a valid promotion.

    Google Places never returns an email address (see the module docstring on
    app.services.places.PlaceSearchResult), so "no email found" is the normal
    case, not a failure -- the lead is created with contact_email=None, and it
    is the outreach channel-eligibility gate (see test_outreach_approval_e2e.py)
    that later blocks EMAIL drafts for it.
    """
    fake = FakePlacesClient(results=[ONE_RESULT])
    _install_fake_places(fake, e2e_session_factory)
    await e2e_client.post(
        "/api/v1/places/search", json={"industry": "dental clinics", "postal_code": "78701"}
    )
    candidate_id = (await e2e_client.get("/api/v1/places/candidates")).json()["items"][0]["id"]

    response = await e2e_client.post(
        f"/api/v1/places/candidates/{candidate_id}/promote",
        json={
            "name": "Congress Dental",
            "enrichment_source": "manual research: no public contact email found",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["contact_email"] is None
    assert body["status"] == "new"
