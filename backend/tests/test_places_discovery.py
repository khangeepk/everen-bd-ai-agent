"""Tests for :mod:`app.services.places` discovery, dedup, and retention.

Uses the fabricated ZIP fixtures in :mod:`tests.sample_places_data`; the
Places API is never called.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.place import CandidateStatus, PlaceCandidate, PlaceSearch
from app.services.places import (
    MAX_RESULTS_PER_PAGE,
    PlaceDiscoveryService,
    PlaceSearchResult,
    parse_place,
)
from app.services.places_policy import MAX_COORDINATE_TTL_DAYS, PolicyViolationError
from tests.sample_places_data import (
    AUSTIN_78701,
    FORBIDDEN_STRINGS,
    MALFORMED,
    RESPONSES_BY_ZIP,
    SHARED_PLACE_ID,
)


class FakePlacesClient:
    """Serves canned responses keyed by the ZIP embedded in the query."""

    def __init__(self) -> None:
        """Initialize the fake and its call log."""
        self.queries: list[str] = []

    async def search_text(
        self, query: str, *, max_results: int = MAX_RESULTS_PER_PAGE
    ) -> list[PlaceSearchResult]:
        """Return fixture results matching the ZIP in the query.

        Args:
            query: The search string.
            max_results: Result cap.

        Returns:
            Parsed transient results, empty if no fixture matches.
        """
        self.queries.append(query)
        for zip_code, payload in RESPONSES_BY_ZIP.items():
            if zip_code in query:
                parsed = [
                    result
                    for raw in payload["places"]
                    if (result := parse_place(raw)) is not None
                ]
                return parsed[:max_results]
        return []


@pytest.fixture
def places_client() -> FakePlacesClient:
    """Provide a fresh fake Places client.

    Returns:
        A :class:`FakePlacesClient`.
    """
    return FakePlacesClient()


def test_parse_extracts_place_id_and_coordinates() -> None:
    """A well-formed Places entry parses into a transient result."""
    result = parse_place(AUSTIN_78701["places"][0])

    assert result is not None
    assert result.place_id == "ChIJ_TEST_AUSTIN_00000001"
    assert result.latitude == pytest.approx(30.2669)
    assert result.display_name == "Congress Avenue Dental"


def test_parse_skips_entries_without_an_id() -> None:
    """Malformed entries are dropped, not raised on."""
    parsed = [r for raw in MALFORMED["places"] if (r := parse_place(raw)) is not None]

    assert len(parsed) == 1
    assert parsed[0].place_id == "ChIJ_TEST_VALID_000000001"


def test_parse_handles_missing_location() -> None:
    """A result with no location block parses with null coordinates."""
    result = parse_place(AUSTIN_78701["places"][2])

    assert result is not None
    assert result.latitude is None
    assert result.longitude is None


def test_persistable_fields_exclude_display_content() -> None:
    """Only place_id and coordinates survive the persistence filter."""
    result = parse_place(AUSTIN_78701["places"][0])
    assert result is not None

    assert set(result.persistable_fields()) == {"place_id", "latitude", "longitude"}


def test_persistable_fields_omit_coordinates_when_absent() -> None:
    """A coordinate-less result yields place_id alone."""
    result = parse_place(AUSTIN_78701["places"][2])
    assert result is not None

    assert result.persistable_fields() == {"place_id": "ChIJ_TEST_AUSTIN_00000003"}


def test_persistable_fields_never_leak_a_business_name() -> None:
    """No fixture business name appears in a persistable payload."""
    for raw in AUSTIN_78701["places"]:
        result = parse_place(raw)
        assert result is not None
        serialized = str(result.persistable_fields())
        for forbidden in FORBIDDEN_STRINGS:
            assert forbidden not in serialized


@pytest.mark.asyncio
async def test_discovery_stages_candidates_for_a_zip(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Searching a ZIP stages one candidate per unique result."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    outcome = await service.discover(industry="dental clinics", postal_code="78701")

    assert outcome.total_found == 4
    assert outcome.new_candidates == 4
    assert outcome.duplicate_candidates == 0
    assert "78701" in places_client.queries[0]


@pytest.mark.asyncio
async def test_repeat_search_creates_no_duplicates(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Re-running the same search dedups entirely on place_id."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")
    second = await service.discover(industry="dental clinics", postal_code="78701")

    rows = (await db_session.execute(select(PlaceCandidate))).scalars().all()

    assert second.new_candidates == 0
    assert second.duplicate_candidates == 4
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_dedup_works_across_different_zips(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """A place appearing in two ZIP searches is stored once."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")
    nyc = await service.discover(industry="dental clinics", postal_code="10001")

    shared = (
        await db_session.execute(
            select(PlaceCandidate).where(PlaceCandidate.place_id == SHARED_PLACE_ID)
        )
    ).scalars().all()

    assert nyc.new_candidates == 2
    assert nyc.duplicate_candidates == 1
    assert len(shared) == 1
    assert shared[0].times_seen == 2


@pytest.mark.asyncio
async def test_empty_result_set_is_handled(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """A ZIP with no matches stages nothing and does not raise."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    outcome = await service.discover(industry="dental clinics", postal_code="00000")

    assert outcome.total_found == 0
    assert outcome.new_candidates == 0


@pytest.mark.asyncio
async def test_source_and_timestamps_are_recorded(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Every candidate carries its source and discovery timestamp."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    candidates = (await db_session.execute(select(PlaceCandidate))).scalars().all()

    for candidate in candidates:
        assert candidate.source == "google_places"
        assert candidate.discovered_at is not None
        assert candidate.last_seen_at is not None
        assert candidate.status is CandidateStatus.NEW


@pytest.mark.asyncio
async def test_search_provenance_row_is_written(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """The search itself is recorded with its parameters and counts."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701", country="US")

    search = (await db_session.execute(select(PlaceSearch))).scalar_one()

    assert search.industry == "dental clinics"
    assert search.postal_code == "78701"
    assert search.country == "US"
    assert search.result_count == 4
    assert search.new_candidate_count == 4
    assert search.provider == "google_places"


@pytest.mark.asyncio
async def test_coordinate_expiry_is_set_to_thirty_days(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Stored coordinates carry a 30-day expiry."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    with_coords = (
        (
            await db_session.execute(
                select(PlaceCandidate).where(PlaceCandidate.latitude.is_not(None))
            )
        )
        .scalars()
        .all()
    )

    assert with_coords
    for candidate in with_coords:
        window = candidate.coordinates_expire_at - candidate.discovered_at
        assert window == timedelta(days=MAX_COORDINATE_TTL_DAYS)


@pytest.mark.asyncio
async def test_candidate_without_coordinates_has_no_expiry(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """No coordinates means no retention clock."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    candidate = (
        await db_session.execute(
            select(PlaceCandidate).where(
                PlaceCandidate.place_id == "ChIJ_TEST_AUSTIN_00000003"
            )
        )
    ).scalar_one()

    assert candidate.latitude is None
    assert candidate.coordinates_expire_at is None


@pytest.mark.asyncio
async def test_purge_deletes_only_expired_coordinates(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """The sweeper redacts expired rows and leaves fresh ones alone."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    candidates = (
        (
            await db_session.execute(
                select(PlaceCandidate).where(PlaceCandidate.latitude.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    stale = candidates[0]
    stale.coordinates_expire_at = stale.discovered_at - timedelta(days=1)
    await db_session.flush()

    purged = await service.purge_expired_coordinates()
    await db_session.refresh(stale)

    assert purged == 1
    assert stale.latitude is None
    assert stale.longitude is None
    assert stale.coordinates_expire_at is None


@pytest.mark.asyncio
async def test_purge_preserves_place_id(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Redaction removes coordinates but keeps the permanently-storable key."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    candidate = (
        (
            await db_session.execute(
                select(PlaceCandidate).where(PlaceCandidate.latitude.is_not(None))
            )
        )
        .scalars()
        .first()
    )
    original_id = candidate.place_id
    candidate.coordinates_expire_at = candidate.discovered_at - timedelta(days=1)
    await db_session.flush()

    await service.purge_expired_coordinates()
    await db_session.refresh(candidate)

    assert candidate.place_id == original_id


@pytest.mark.asyncio
async def test_purge_is_idempotent(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """A second sweep with nothing expired redacts nothing."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    assert await service.purge_expired_coordinates() == 0


@pytest.mark.asyncio
async def test_redact_coordinates_is_idempotent(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Model-level redaction reports whether it changed anything."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")

    candidate = (
        (
            await db_session.execute(
                select(PlaceCandidate).where(PlaceCandidate.latitude.is_not(None))
            )
        )
        .scalars()
        .first()
    )

    assert candidate.redact_coordinates() is True
    assert candidate.redact_coordinates() is False


@pytest.mark.asyncio
async def test_no_google_content_is_ever_persisted(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Compliance regression test.

    Scans every column of every staged row for any business name, address,
    phone number, or website present in the fixtures. This is the check that
    catches someone helpfully adding a ``name`` column later.
    """
    service = PlaceDiscoveryService(db=db_session, client=places_client)
    await service.discover(industry="dental clinics", postal_code="78701")
    await service.discover(industry="dental clinics", postal_code="10001")

    candidates = (await db_session.execute(select(PlaceCandidate))).scalars().all()
    assert candidates

    for candidate in candidates:
        serialized = " ".join(
            str(getattr(candidate, column.name))
            for column in PlaceCandidate.__table__.columns
        )
        for forbidden in FORBIDDEN_STRINGS:
            assert forbidden not in serialized, (
                f"Google Maps Content {forbidden!r} was persisted on "
                f"place_candidates row {candidate.id}"
            )


@pytest.mark.asyncio
async def test_candidate_table_has_no_name_or_address_column() -> None:
    """The schema itself forbids storing name or address."""
    columns = {column.name for column in PlaceCandidate.__table__.columns}
    forbidden_columns = {
        "name",
        "display_name",
        "business_name",
        "address",
        "formatted_address",
        "phone",
        "website",
        "rating",
    }

    assert not (columns & forbidden_columns)


@pytest.mark.asyncio
async def test_persisting_google_content_raises(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """Attempting to persist restricted content fails loudly."""
    result = PlaceSearchResult(place_id="ChIJ_X", display_name="Some Business")
    payload = result.persistable_fields()
    payload["display_name"] = result.display_name

    from app.services.places_policy import assert_persistable

    with pytest.raises(PolicyViolationError):
        assert_persistable(payload)


@pytest.mark.asyncio
async def test_invalid_postal_code_is_rejected(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """An unusable postal code raises before any API call."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)

    with pytest.raises(ValueError):
        await service.discover(industry="dental clinics", postal_code="---")

    assert places_client.queries == []


@pytest.mark.asyncio
async def test_blank_industry_is_rejected(
    db_session: AsyncSession, places_client: FakePlacesClient
) -> None:
    """A blank industry raises before any API call."""
    service = PlaceDiscoveryService(db=db_session, client=places_client)

    with pytest.raises(ValueError):
        await service.discover(industry="  ", postal_code="78701")

    assert places_client.queries == []
