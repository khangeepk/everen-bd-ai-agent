"""Tests for :mod:`app.services.places_policy`.

These encode Google's retention rules as executable assertions. If a change
here starts failing, the fix is almost never to relax the test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.places_policy import (
    FORBIDDEN_FIELDS,
    MAX_COORDINATE_TTL_DAYS,
    PERSISTABLE_FIELDS,
    PolicyViolationError,
    assert_persistable,
    build_text_query,
    coordinate_expiry,
    dedup_key,
    filter_to_persistable,
    is_coordinate_expired,
    is_us_zip,
    normalize_postal_code,
    search_fingerprint,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def test_allowlist_is_exactly_place_id_and_coordinates() -> None:
    """The persistable set matches Places Service Specific Terms 10.3."""
    assert PERSISTABLE_FIELDS == {"place_id", "latitude", "longitude"}


def test_place_id_only_payload_is_persistable() -> None:
    """A place_id alone is always storable."""
    assert_persistable({"place_id": "ChIJ_TEST_00000001"})


def test_coordinates_payload_is_persistable() -> None:
    """place_id plus coordinates is storable."""
    assert_persistable({"place_id": "ChIJ_X", "latitude": 30.2, "longitude": -97.7})


@pytest.mark.parametrize(
    "field",
    [
        "display_name",
        "displayName",
        "formatted_address",
        "formattedAddress",
        "national_phone_number",
        "website_uri",
        "rating",
        "types",
        "business_status",
        "reviews",
        "photos",
    ],
)
def test_google_maps_content_is_refused(field: str) -> None:
    """Persisting any restricted field raises rather than silently dropping it."""
    with pytest.raises(PolicyViolationError, match="Google Maps Content"):
        assert_persistable({"place_id": "ChIJ_X", field: "value"})


def test_business_name_is_refused() -> None:
    """The specific field a name+address dedup would need is refused."""
    with pytest.raises(PolicyViolationError):
        assert_persistable({"place_id": "ChIJ_X", "name": "Congress Avenue Dental"})


def test_unknown_field_is_refused() -> None:
    """Fields off the allowlist are rejected even if not explicitly forbidden."""
    with pytest.raises(PolicyViolationError, match="not on the Places persistable allowlist"):
        assert_persistable({"place_id": "ChIJ_X", "some_new_field": 1})


def test_forbidden_and_persistable_sets_do_not_overlap() -> None:
    """The two policy sets are mutually exclusive."""
    assert not (FORBIDDEN_FIELDS & PERSISTABLE_FIELDS)


def test_filter_drops_restricted_fields() -> None:
    """filter_to_persistable keeps only allowlisted keys."""
    filtered = filter_to_persistable(
        {
            "place_id": "ChIJ_X",
            "latitude": 30.2,
            "longitude": -97.7,
            "displayName": "Congress Avenue Dental",
            "formattedAddress": "100 Congress Ave",
            "rating": 4.6,
        }
    )
    assert filtered == {"place_id": "ChIJ_X", "latitude": 30.2, "longitude": -97.7}


def test_filter_on_clean_payload_is_identity() -> None:
    """Nothing is dropped from an already-compliant payload."""
    payload = {"place_id": "ChIJ_X"}
    assert filter_to_persistable(payload) == payload


def test_expiry_is_thirty_days_out() -> None:
    """The default retention window is 30 days."""
    assert coordinate_expiry(NOW) == NOW + timedelta(days=MAX_COORDINATE_TTL_DAYS)


def test_shorter_ttl_is_allowed() -> None:
    """A conservative retention window below the maximum is permitted."""
    assert coordinate_expiry(NOW, ttl_days=7) == NOW + timedelta(days=7)


def test_ttl_above_policy_maximum_is_rejected() -> None:
    """Configuring retention beyond 30 days is an error, not a silent clamp."""
    with pytest.raises(ValueError, match="exceeds the 30-day maximum"):
        coordinate_expiry(NOW, ttl_days=31)


def test_ttl_of_a_year_is_rejected() -> None:
    """An obviously wrong TTL is caught."""
    with pytest.raises(ValueError, match="exceeds"):
        coordinate_expiry(NOW, ttl_days=365)


def test_non_positive_ttl_is_rejected() -> None:
    """TTL must be positive."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="ttl_days must be positive"):
            coordinate_expiry(NOW, ttl_days=bad)


def test_naive_datetime_is_rejected() -> None:
    """Ambiguous local timestamps cannot anchor a retention window."""
    with pytest.raises(ValueError, match="timezone-aware"):
        coordinate_expiry(datetime(2026, 7, 29, 12, 0))


def test_coordinates_are_not_expired_before_the_window_closes() -> None:
    """Day 29 of a 30-day window is still within retention."""
    expiry = coordinate_expiry(NOW)
    assert is_coordinate_expired(expiry, now=NOW + timedelta(days=29)) is False


def test_coordinates_expire_exactly_at_the_boundary() -> None:
    """Retention ends at the boundary instant, not after it."""
    expiry = coordinate_expiry(NOW)
    assert is_coordinate_expired(expiry, now=expiry) is True


def test_coordinates_are_expired_after_the_window() -> None:
    """Day 31 is past retention."""
    expiry = coordinate_expiry(NOW)
    assert is_coordinate_expired(expiry, now=NOW + timedelta(days=31)) is True


def test_absent_expiry_means_no_coordinates_held() -> None:
    """A null expiry is not treated as expired."""
    assert is_coordinate_expired(None, now=NOW) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("78701", "78701"),
        ("78701-1234", "787011234"),
        (" 10001 ", "10001"),
        ("sw1a 1aa", "SW1A1AA"),
        ("M5V-3A8", "M5V3A8"),
    ],
)
def test_postal_codes_normalize(raw: str, expected: str) -> None:
    """Punctuation and case are stripped from postal codes."""
    assert normalize_postal_code(raw) == expected


def test_unusable_postal_code_is_rejected() -> None:
    """A postal code of pure punctuation is an error."""
    with pytest.raises(ValueError, match="no usable characters"):
        normalize_postal_code("---")


@pytest.mark.parametrize("zip_code", ["78701", "10001", "787011234"])
def test_us_zip_detection_accepts_valid(zip_code: str) -> None:
    """5- and 9-digit numeric codes are recognized as US ZIPs."""
    assert is_us_zip(zip_code) is True


@pytest.mark.parametrize("zip_code", ["SW1A1AA", "1234", "M5V3A8", "1234567"])
def test_us_zip_detection_rejects_invalid(zip_code: str) -> None:
    """Non-US and wrong-length codes are not ZIPs."""
    assert is_us_zip(zip_code) is False


def test_text_query_includes_industry_and_zip() -> None:
    """The query sent to Places contains both search dimensions."""
    assert build_text_query("dental clinics", "78701") == "dental clinics in 78701"


def test_text_query_appends_country() -> None:
    """Country is appended when supplied."""
    assert build_text_query("dental clinics", "78701", "US") == "dental clinics in 78701, US"


def test_blank_industry_is_rejected() -> None:
    """An industry term is required."""
    with pytest.raises(ValueError, match="industry must not be blank"):
        build_text_query("   ", "78701")


def test_dedup_key_is_the_place_id() -> None:
    """place_id is the canonical dedup identity."""
    assert dedup_key("  ChIJ_TEST_00000001  ") == "ChIJ_TEST_00000001"


def test_blank_place_id_is_rejected() -> None:
    """A blank place_id cannot identify anything."""
    with pytest.raises(ValueError, match="place_id must not be blank"):
        dedup_key("   ")


def test_search_fingerprint_is_stable() -> None:
    """The same parameters always produce the same fingerprint."""
    assert search_fingerprint("dental clinics", "78701", 5000) == search_fingerprint(
        "dental clinics", "78701", 5000
    )


def test_search_fingerprint_ignores_case_and_padding() -> None:
    """Casing and whitespace do not change the fingerprint."""
    assert search_fingerprint("Dental Clinics", "78701", 5000) == search_fingerprint(
        "  dental clinics  ", "78701", 5000
    )


def test_search_fingerprint_varies_with_parameters() -> None:
    """Different searches fingerprint differently."""
    base = search_fingerprint("dental clinics", "78701", 5000)

    assert base != search_fingerprint("law firms", "78701", 5000)
    assert base != search_fingerprint("dental clinics", "10001", 5000)
    assert base != search_fingerprint("dental clinics", "78701", 10000)
