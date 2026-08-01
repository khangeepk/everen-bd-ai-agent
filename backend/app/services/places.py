"""Google Places API (New) client and lead discovery service.

Two-layer split, deliberately:

* :class:`PlaceSearchResult` is a **transient** dataclass. It carries display
  fields (name, address) that Google permits showing to a user but not storing.
  It never reaches the database.
* :class:`PlaceCandidate` is the **persistent** row, holding only ``place_id``
  and TTL-bounded coordinates.

Dedup keys on ``place_id`` (see :func:`app.services.places_policy.dedup_key`).

See AGENTS.md sections 6, 7, and 9.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import utcnow
from app.db.models.place import CandidateStatus, PlaceCandidate, PlaceSearch
from app.services.cost_guard import PLACES_COST_PER_SEARCH_USD, BudgetExceededError, CostProvider
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.places_policy import (
    assert_persistable,
    build_text_query,
    coordinate_expiry,
    dedup_key,
    is_coordinate_expired,
    normalize_postal_code,
    search_fingerprint,
)

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

#: Place Details (New) endpoint -- {place_id} is interpolated in.
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

#: Minimal field mask for the signal scanner (app/services/signal_scanner.py):
#: only the three fields it needs to detect a business-status change or a
#: review-count jump. Every field here is Google Maps Content per
#: app.services.places_policy.FORBIDDEN_FIELDS -- see PlaceDetailsResult's
#: docstring below for why these values are never persisted verbatim.
PLACE_DETAILS_FIELD_MASK = "businessStatus,rating,userRatingCount"

#: Field mask sent to Places. Kept minimal: every field costs money and
#: everything except id/location is display-only and must not be stored.
PLACES_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.location",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.businessStatus",
    ]
)

DEFAULT_RADIUS_METERS = 5000
MAX_RESULTS_PER_PAGE = 20


class PlacesError(RuntimeError):
    """Raised when the Places API is unreachable or returns an error."""


class PlacesTestModeLimitExceeded(PlacesError):
    """Raised when a test-mode run has used up its Places request allowance.

    Distinct from the dollar-based cost guard (app.services.cost_guard):
    this is a hard request-count rail for a deliberate test/soft-launch
    window, independent of whether the daily dollar budget still has room,
    so a test batch can be kept trivially inside Google Maps Platform's
    monthly free credit without depending on a cost estimate being exact.
    """


#: Process-lifetime counter of Places API requests made while
#: settings.places_test_mode is True. Module-level (not per-service-instance)
#: because a new PlaceDiscoveryService is constructed per request via the
#: FastAPI dependency (see app.api.v1.places.get_discovery_service), so an
#: instance attribute would reset every request and never actually cap
#: anything across a multi-request test run.
_test_mode_request_count = 0


def get_test_mode_request_count() -> int:
    """Return how many Places requests this process has made in test mode.

    Returns:
        The current count.
    """
    return _test_mode_request_count


def reset_test_mode_request_count() -> None:
    """Reset the test-mode request counter.

    Exposed for tests and for deliberately starting a fresh test-mode
    allowance (e.g. at the start of a new soft-launch batch).
    """
    global _test_mode_request_count
    _test_mode_request_count = 0


def enforce_places_test_mode_cap() -> None:
    """Refuse a Places API call if the test-mode request cap is reached.

    Shared by every Places call site -- ``PlaceDiscoveryService.discover``
    (Text Search) and ``app.services.signal_scanner`` (Place Details) both
    call this before making a request, so PLACES_TEST_MODE_MAX_REQUESTS caps
    total Places spend across both endpoints, not just Text Search.
    A no-op when ``settings.places_test_mode`` is False.

    Raises:
        PlacesTestModeLimitExceeded: If the cap has been reached.
    """
    global _test_mode_request_count

    if not settings.places_test_mode:
        return

    if _test_mode_request_count >= settings.places_test_mode_max_requests:
        logger.error(
            "Places test-mode request cap reached; refusing further requests",
            extra={
                "requests_used": _test_mode_request_count,
                "cap": settings.places_test_mode_max_requests,
            },
        )
        raise PlacesTestModeLimitExceeded(
            f"Places test-mode cap of {settings.places_test_mode_max_requests} "
            f"requests reached for this run. Set PLACES_TEST_MODE=false to lift "
            f"the cap, or raise PLACES_TEST_MODE_MAX_REQUESTS."
        )
    _test_mode_request_count += 1
    logger.info(
        "Places test-mode request",
        extra={
            "requests_used": _test_mode_request_count,
            "cap": settings.places_test_mode_max_requests,
        },
    )


@dataclass(frozen=True)
class PlaceSearchResult:
    """A single Places result held in memory only.

    WARNING: ``display_name``, ``formatted_address``, ``website``, and
    ``phone`` are Google Maps Content. They may be returned to the caller for
    immediate display but MUST NOT be written to the database. Only
    :attr:`place_id` and the coordinates are persistable.

    Attributes:
        place_id: Google's stable place identifier.
        display_name: Business name -- display only.
        formatted_address: Full address -- display only.
        latitude: Latitude, persistable for 30 days.
        longitude: Longitude, persistable for 30 days.
        website: Website URL -- display only.
        phone: National phone number -- display only.
        business_status: Operational status -- display only.
    """

    place_id: str
    display_name: str | None = None
    formatted_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website: str | None = None
    phone: str | None = None
    business_status: str | None = None

    def persistable_fields(self) -> dict[str, Any]:
        """Extract only the fields permitted for durable storage.

        Returns:
            A mapping of ``place_id`` and, when present, coordinates.
        """
        payload: dict[str, Any] = {"place_id": self.place_id}
        if self.latitude is not None and self.longitude is not None:
            payload["latitude"] = self.latitude
            payload["longitude"] = self.longitude
        assert_persistable(payload)
        return payload


@dataclass(frozen=True)
class PlaceDetailsResult:
    """A single Place Details lookup, held in memory only.

    WARNING: every field except ``place_id`` is Google Maps Content per
    ``app.services.places_policy.FORBIDDEN_FIELDS``. These values may be used
    to compute a bucketed, keyed-hash fingerprint (see
    ``app.services.signal_detection``) but must NEVER be written to durable
    storage verbatim -- there is no ``persistable_fields()`` method here on
    purpose, unlike :class:`PlaceSearchResult`, so there is nothing to
    accidentally call.

    Attributes:
        place_id: Google's stable place identifier.
        business_status: Raw operational status string, display/derivation
            only.
        rating: Raw star rating, display/derivation only.
        review_count: Raw review count, display/derivation only.
    """

    place_id: str
    business_status: str | None = None
    rating: float | None = None
    review_count: int | None = None


@runtime_checkable
class PlacesClient(Protocol):
    """Interface for a place search provider."""

    async def search_text(
        self, query: str, *, max_results: int = MAX_RESULTS_PER_PAGE
    ) -> list[PlaceSearchResult]:
        """Run a text search.

        Args:
            query: Natural-language search string.
            max_results: Maximum results to return.

        Returns:
            Transient search results.
        """
        ...

    async def get_place_details(self, place_id: str) -> PlaceDetailsResult:
        """Fetch business status and review data for a known place.

        Args:
            place_id: The place to look up.

        Returns:
            The transient details result.
        """
        ...


def parse_place(raw: dict[str, Any]) -> PlaceSearchResult | None:
    """Convert one Places API result object into a transient result.

    Args:
        raw: A single entry from the ``places`` array.

    Returns:
        The parsed result, or None if it carries no usable ``id``.
    """
    place_id = raw.get("id")
    if not isinstance(place_id, str) or not place_id.strip():
        logger.warning("Skipping Places result with no id")
        return None

    location = raw.get("location") or {}
    display = raw.get("displayName") or {}

    return PlaceSearchResult(
        place_id=place_id.strip(),
        display_name=display.get("text") if isinstance(display, dict) else None,
        formatted_address=raw.get("formattedAddress"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        website=raw.get("websiteUri"),
        phone=raw.get("nationalPhoneNumber"),
        business_status=raw.get("businessStatus"),
    )


class GooglePlacesClient:
    """Places API (New) searchText client."""

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 10.0) -> None:
        """Initialize the client.

        Args:
            api_key: Google Maps Platform key. Defaults to settings.
            timeout_seconds: Per-request timeout.
        """
        self._api_key = api_key or settings.google_places_api_key
        self._timeout = timeout_seconds

    async def search_text(
        self, query: str, *, max_results: int = MAX_RESULTS_PER_PAGE
    ) -> list[PlaceSearchResult]:
        """Run a Places text search.

        Args:
            query: Natural-language search string.
            max_results: Maximum results to request, capped at 20 by the API.

        Returns:
            Transient search results.

        Raises:
            PlacesError: On transport failure or a non-2xx response.
        """
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
        }
        body = {"textQuery": query, "maxResultCount": min(max_results, MAX_RESULTS_PER_PAGE)}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(PLACES_SEARCH_URL, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "Places API returned an error", extra={"status": exc.response.status_code}
            )
            raise PlacesError(
                f"Places API error {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("Places API unreachable")
            raise PlacesError(f"Places API request failed: {exc}") from exc

        results = [
            parsed for raw in payload.get("places", []) if (parsed := parse_place(raw)) is not None
        ]
        logger.info("Places search completed", extra={"results": len(results)})
        return results

    async def get_place_details(self, place_id: str) -> PlaceDetailsResult:
        """Fetch business status and review data for a known place.

        Billed separately from ``search_text`` (Place Details, not Text
        Search) -- callers must cost-guard this the same way
        ``PlaceDiscoveryService.discover`` guards search_text (see
        ``app/services/signal_scanner.py``).

        Args:
            place_id: The place to look up.

        Returns:
            The transient details result. Every field but ``place_id`` is
            Google Maps Content -- see :class:`PlaceDetailsResult`'s
            docstring before doing anything with it besides deriving a
            bucketed fingerprint.

        Raises:
            PlacesError: On transport failure or a non-2xx response.
        """
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": PLACE_DETAILS_FIELD_MASK,
        }
        url = PLACE_DETAILS_URL.format(place_id=place_id)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "Place Details API returned an error",
                extra={"status": exc.response.status_code, "place_id": place_id},
            )
            raise PlacesError(
                f"Place Details API error {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("Place Details API unreachable", extra={"place_id": place_id})
            raise PlacesError(f"Place Details API request failed: {exc}") from exc

        result = PlaceDetailsResult(
            place_id=place_id,
            business_status=payload.get("businessStatus"),
            rating=payload.get("rating"),
            review_count=payload.get("userRatingCount"),
        )
        logger.info("Place details fetched", extra={"place_id": place_id})
        return result


@dataclass(frozen=True)
class DiscoveryOutcome:
    """Summary of one discovery run.

    Attributes:
        search_id: Identifier of the recorded :class:`PlaceSearch`.
        results: Transient results, safe to return to the caller for display.
        total_found: How many results the provider returned.
        new_candidates: How many were not already known.
        duplicate_candidates: How many matched an existing ``place_id``.
    """

    search_id: uuid.UUID
    results: list[PlaceSearchResult]
    total_found: int
    new_candidates: int
    duplicate_candidates: int


class PlaceDiscoveryService:
    """Searches for businesses by location and industry, and stages the results."""

    def __init__(self, db: AsyncSession, client: PlacesClient) -> None:
        """Initialize the service.

        Args:
            db: Active database session.
            client: Place search provider.
        """
        self._db = db
        self._client = client

    async def _upsert_candidate(
        self,
        result: PlaceSearchResult,
        search: PlaceSearch,
        now: datetime,
        ttl_days: int,
    ) -> bool:
        """Insert a candidate, or touch it if its ``place_id`` is already known.

        Args:
            result: The transient search result.
            search: The search that produced it.
            now: Timestamp for this run.
            ttl_days: Coordinate retention window.

        Returns:
            True if a new row was created, False if an existing row was updated.
        """
        key = dedup_key(result.place_id)
        existing = (
            await self._db.execute(
                select(PlaceCandidate).where(PlaceCandidate.place_id == key)
            )
        ).scalar_one_or_none()

        persistable = result.persistable_fields()
        has_coords = "latitude" in persistable

        if existing is not None:
            existing.last_seen_at = now
            existing.times_seen += 1
            # Refresh coordinates and restart their retention clock.
            if has_coords:
                existing.latitude = persistable["latitude"]
                existing.longitude = persistable["longitude"]
                existing.coordinates_expire_at = coordinate_expiry(now, ttl_days)
            logger.info("Place candidate already known", extra={"place_id": key})
            return False

        self._db.add(
            PlaceCandidate(
                place_id=key,
                latitude=persistable.get("latitude"),
                longitude=persistable.get("longitude"),
                coordinates_expire_at=coordinate_expiry(now, ttl_days) if has_coords else None,
                search_id=search.id,
                source="google_places",
                discovered_at=now,
                last_seen_at=now,
                times_seen=1,
                status=CandidateStatus.NEW,
            )
        )
        logger.info("Place candidate created", extra={"place_id": key})
        return True

    async def discover(
        self,
        *,
        industry: str,
        postal_code: str,
        country: str | None = None,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        max_results: int = MAX_RESULTS_PER_PAGE,
        executed_by_id: uuid.UUID | None = None,
    ) -> DiscoveryOutcome:
        """Search by location and industry, staging any new places found.

        Args:
            industry: Industry or category term.
            postal_code: Postal code to search within.
            country: Optional country for disambiguation.
            radius_meters: Recorded for provenance; searchText biases by the
                postal code in the query text.
            max_results: Maximum results to request.
            executed_by_id: The user running the search.

        Returns:
            A :class:`DiscoveryOutcome`. ``results`` carries display-only
            fields that the caller must not persist.

        Raises:
            PlacesError: If the provider fails.
            PlacesTestModeLimitExceeded: If places_test_mode is on and this
                run has used up its request allowance.
            ValueError: If ``industry`` or ``postal_code`` is unusable.
        """
        enforce_places_test_mode_cap()

        normalized_zip = normalize_postal_code(postal_code)
        query = build_text_query(industry, normalized_zip, country)
        now = utcnow()

        # Cost guard: Places bills a flat rate per request regardless of
        # results, so this is checked BEFORE the call, not just recorded
        # after -- see the module docstring on app.services.cost_guard.
        await enforce_budget_before_call(
            self._db,
            CostProvider.PLACES,
            settings.cost_guard_daily_budget_places_usd,
            estimated_cost_usd=PLACES_COST_PER_SEARCH_USD,
        )

        results = await self._client.search_text(query, max_results=max_results)

        await record_spend(
            self._db,
            CostProvider.PLACES,
            "places.discover",
            PLACES_COST_PER_SEARCH_USD,
            daily_budget_usd=settings.cost_guard_daily_budget_places_usd,
        )

        search = PlaceSearch(
            industry=industry.strip(),
            postal_code=normalized_zip,
            country=country,
            radius_meters=radius_meters,
            fingerprint=search_fingerprint(industry, normalized_zip, radius_meters),
            provider="google_places",
            executed_by_id=executed_by_id,
            executed_at=now,
            result_count=len(results),
        )
        self._db.add(search)
        await self._db.flush()

        created = 0
        ttl_days = settings.places_coordinate_ttl_days
        for result in results:
            if await self._upsert_candidate(result, search, now, ttl_days):
                created += 1

        search.new_candidate_count = created
        await self._db.flush()

        logger.info(
            "Discovery run complete",
            extra={
                "search_id": str(search.id),
                "postal_code": normalized_zip,
                "found": len(results),
                "new": created,
                "duplicates": len(results) - created,
            },
        )
        return DiscoveryOutcome(
            search_id=search.id,
            results=results,
            total_found=len(results),
            new_candidates=created,
            duplicate_candidates=len(results) - created,
        )

    async def purge_expired_coordinates(self, now: datetime | None = None) -> int:
        """Delete coordinates that have passed their 30-day retention window.

        Must run on a schedule. Without it the system drifts out of compliance
        with Places API Service Specific Terms section 10.3.

        Args:
            now: Current time, for deterministic testing.

        Returns:
            The number of rows redacted.
        """
        current = now or datetime.now(timezone.utc)
        result = await self._db.execute(
            update(PlaceCandidate)
            .where(
                PlaceCandidate.coordinates_expire_at.is_not(None),
                PlaceCandidate.coordinates_expire_at <= current,
            )
            .values(latitude=None, longitude=None, coordinates_expire_at=None)
            .execution_options(synchronize_session=False)
        )
        purged = result.rowcount or 0
        await self._db.flush()

        logger.info("Purged expired Places coordinates", extra={"rows": purged})
        return purged

    @staticmethod
    def filter_expired(
        candidates: Sequence[PlaceCandidate], now: datetime | None = None
    ) -> list[PlaceCandidate]:
        """Return candidates whose coordinates are past their retention window.

        Args:
            candidates: Rows to inspect.
            now: Current time, for deterministic testing.

        Returns:
            The subset needing redaction.
        """
        return [
            candidate
            for candidate in candidates
            if is_coordinate_expired(candidate.coordinates_expire_at, now)
        ]
