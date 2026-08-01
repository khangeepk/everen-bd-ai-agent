"""Google Maps Platform data-retention policy enforcement.

This module is the single place where Google's caching rules are encoded, so
the constraint is testable and auditable rather than scattered through the
persistence layer. Standard library only.

Policy basis -- Google Maps Platform Service Specific Terms, section 10
(Places API), as published 2026-07-20:

    10.3 Caching. Customer may temporarily cache latitude and longitude values
    from the Places API for up to 30 consecutive calendar days, after which
    Customer must delete the cached latitude and longitude values.

and the Places API policies page:

    Note that the place ID, used to uniquely identify a place, is exempt from
    the caching restrictions. You can therefore store place ID values
    indefinitely.

Everything else returned by the Places API -- ``displayName``,
``formattedAddress``, ``nationalPhoneNumber``, ``websiteUri``, ``rating``,
``types``, photos, and reviews -- is Google Maps Content and MUST NOT be
written to durable storage. Such fields may be held in memory and returned to
the caller for immediate display, but never persisted.

This is not legal advice. Confirm against the current terms before launch:
https://cloud.google.com/maps-platform/terms/maps-service-terms
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Places fields that may be stored indefinitely.
PERSISTABLE_INDEFINITELY: frozenset[str] = frozenset({"place_id"})

#: Places fields that may be stored only for a bounded window.
PERSISTABLE_WITH_TTL: frozenset[str] = frozenset({"latitude", "longitude"})

#: Every Places-derived field this system is permitted to persist.
PERSISTABLE_FIELDS: frozenset[str] = PERSISTABLE_INDEFINITELY | PERSISTABLE_WITH_TTL

#: Fields that are Google Maps Content and must never reach durable storage.
#: Present for clear error messages and as executable documentation.
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "display_name",
        "displayName",
        "name",
        "formatted_address",
        "formattedAddress",
        "short_formatted_address",
        "shortFormattedAddress",
        "address_components",
        "addressComponents",
        "national_phone_number",
        "nationalPhoneNumber",
        "international_phone_number",
        "internationalPhoneNumber",
        "website_uri",
        "websiteUri",
        "rating",
        "user_rating_count",
        "userRatingCount",
        "types",
        "primary_type",
        "primaryType",
        "business_status",
        "businessStatus",
        "photos",
        "reviews",
        "editorial_summary",
        "editorialSummary",
        "opening_hours",
        "regularOpeningHours",
        "price_level",
        "priceLevel",
    }
)

#: Maximum retention for latitude/longitude, per Service Specific Terms 10.3.
MAX_COORDINATE_TTL_DAYS = 30

_POSTAL_CLEAN = re.compile(r"[^A-Za-z0-9]")
_US_ZIP = re.compile(r"^\d{5}(\d{4})?$")


class PolicyViolationError(RuntimeError):
    """Raised when a write would persist restricted Google Maps Content.

    Deliberately an error rather than a warning: silently dropping the field
    would hide a bug that could otherwise ship a compliance breach.
    """


def assert_persistable(payload: dict[str, Any]) -> None:
    """Verify that a payload contains only persistable Places fields.

    Args:
        payload: Field mapping about to be written to durable storage.

    Raises:
        PolicyViolationError: If any key is restricted Google Maps Content or
            is not on the persistable allowlist.
    """
    keys = set(payload)

    forbidden = keys & FORBIDDEN_FIELDS
    if forbidden:
        raise PolicyViolationError(
            "Refusing to persist Google Maps Content: "
            f"{sorted(forbidden)}. Places API Service Specific Terms 10.3 permits "
            "storing only place_id (indefinitely) and latitude/longitude (30 days)."
        )

    unknown = keys - PERSISTABLE_FIELDS
    if unknown:
        raise PolicyViolationError(
            f"Fields {sorted(unknown)} are not on the Places persistable allowlist "
            f"{sorted(PERSISTABLE_FIELDS)}. Add them explicitly only after "
            "confirming the terms permit storage."
        )


def filter_to_persistable(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop every non-persistable field from a Places payload.

    Use when handling an API response whose shape is not fully controlled.
    Prefer :func:`assert_persistable` for internally-built payloads, where an
    unexpected field indicates a bug worth surfacing.

    Args:
        payload: Raw field mapping.

    Returns:
        A new mapping containing only allowlisted fields.
    """
    kept = {key: value for key, value in payload.items() if key in PERSISTABLE_FIELDS}
    dropped = set(payload) - set(kept)
    if dropped:
        logger.info(
            "Dropped non-persistable Places fields before write",
            extra={"dropped_count": len(dropped)},
        )
    return kept


def coordinate_expiry(
    discovered_at: datetime, ttl_days: int = MAX_COORDINATE_TTL_DAYS
) -> datetime:
    """Compute when cached coordinates must be deleted.

    Args:
        discovered_at: When the coordinates were retrieved. Must be
            timezone-aware.
        ttl_days: Retention window in days. Capped at
            :data:`MAX_COORDINATE_TTL_DAYS`.

    Returns:
        The UTC instant at which the coordinates must be purged.

    Raises:
        ValueError: If ``discovered_at`` is naive, or ``ttl_days`` is not
            positive or exceeds the policy maximum.
    """
    if discovered_at.tzinfo is None:
        raise ValueError("discovered_at must be timezone-aware")
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    if ttl_days > MAX_COORDINATE_TTL_DAYS:
        raise ValueError(
            f"ttl_days={ttl_days} exceeds the {MAX_COORDINATE_TTL_DAYS}-day maximum "
            "permitted by Places API Service Specific Terms 10.3"
        )
    return discovered_at.astimezone(timezone.utc) + timedelta(days=ttl_days)


def is_coordinate_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
    """Whether cached coordinates are past their retention window.

    Args:
        expires_at: The stored expiry instant, or None if no coordinates are
            held.
        now: Current time, for deterministic testing. Defaults to now in UTC.

    Returns:
        True when coordinates exist and must be purged. False when no
        coordinates are stored.
    """
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return current >= expires_at


def normalize_postal_code(raw: str) -> str:
    """Normalize a user-supplied postal code for querying and storage.

    The postal code is the caller's own search input, not Google Maps Content,
    so it is freely storable.

    Args:
        raw: Postal code as typed.

    Returns:
        Uppercased, alphanumeric-only postal code.

    Raises:
        ValueError: If nothing usable remains after stripping punctuation.
    """
    cleaned = _POSTAL_CLEAN.sub("", raw).upper()
    if not cleaned:
        raise ValueError(f"Postal code {raw!r} contains no usable characters")
    return cleaned


def is_us_zip(postal_code: str) -> bool:
    """Whether a normalized postal code looks like a US ZIP or ZIP+4.

    Args:
        postal_code: An already-normalized postal code.

    Returns:
        True for 5-digit or 9-digit all-numeric codes.
    """
    return bool(_US_ZIP.match(postal_code))


def build_text_query(industry: str, postal_code: str, country: str | None = None) -> str:
    """Build the ``textQuery`` sent to Places API searchText.

    Args:
        industry: Industry or business category, e.g. ``"dental clinics"``.
        postal_code: Normalized postal code.
        country: Optional country name or code to disambiguate.

    Returns:
        A natural-language query string.

    Raises:
        ValueError: If ``industry`` is blank.
    """
    trimmed = industry.strip()
    if not trimmed:
        raise ValueError("industry must not be blank")

    query = f"{trimmed} in {postal_code}"
    if country:
        query = f"{query}, {country.strip()}"
    return query


def dedup_key(place_id: str) -> str:
    """Return the canonical dedup identity for a place.

    ``place_id`` is Google's own stable unique identifier and is exempt from
    the caching restrictions, which makes it both the compliant and the
    technically correct dedup key -- it survives a business renaming or Google
    reformatting its address string, neither of which a name+address key
    handles.

    Args:
        place_id: The Places API ``place_id`` / resource ``id``.

    Returns:
        The trimmed place ID.

    Raises:
        ValueError: If ``place_id`` is blank.
    """
    trimmed = place_id.strip()
    if not trimmed:
        raise ValueError("place_id must not be blank")
    return trimmed


def search_fingerprint(industry: str, postal_code: str, radius_meters: int) -> str:
    """Build a stable fingerprint identifying a search's parameters.

    Lets repeat searches be recognized without storing the raw query twice.
    Inputs are all caller-supplied, so no Google Maps Content is involved.

    Args:
        industry: Industry term as searched.
        postal_code: Normalized postal code.
        radius_meters: Search radius.

    Returns:
        A hex SHA-256 digest.
    """
    material = f"{industry.strip().lower()}|{postal_code.upper()}|{radius_meters}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
