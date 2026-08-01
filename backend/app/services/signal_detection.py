"""Hash-only change detection for Places-derived lead-signal facts.

Two of the three signal types this system detects (business status change,
review count jump) are derived from Google Places data. Per
``app/services/places_policy.py``, raw Places fields beyond ``place_id``/
coordinates are Google Maps Content and must never be persisted verbatim --
including a business's rating, review count, or operational status.

So this module never stores those raw values. Instead it:

1. Discretizes the raw value into a small, coarse bucket (a review count
   into buckets of :data:`REVIEW_COUNT_BUCKET_SIZE`; a business status into
   one of a handful of known enum values).
2. Computes a keyed HMAC-SHA256 of that bucket, namespaced by lead ID (so two
   leads both "OPERATIONAL" don't produce the same hash) via
   ``app/services/pii.py``'s existing blind-index primitive -- reused here
   for the same reason it exists there: a deterministic, non-reversible
   fingerprint that still supports equality comparison.
3. Only that hash is ever persisted (see
   ``app/db/models/signal.py::SignalCheckpoint``).

To still answer "did it go *up*, and by how much" -- not just "did it
change" -- from an opaque hash, :func:`recover_review_bucket` and
:func:`recover_business_status` brute-force the (deliberately small) space of
possible buckets/enum values and report which one's hash matches the stored
checkpoint. This recovers the previous value's *meaning* without ever having
stored it in plaintext.

Standard library only, aside from reusing ``app.services.pii.blind_index``
(itself stdlib ``hmac``/``hashlib`` plus a settings read) -- no database or
network access, so this is testable offline like the rest of this codebase's
pure business-logic modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.pii import blind_index

logger = logging.getLogger(__name__)

#: Review counts are bucketed to the nearest multiple of this many reviews
#: before hashing -- coarse enough that the hash cannot be used to recover
#: the exact review count, while still detecting meaningful growth.
REVIEW_COUNT_BUCKET_SIZE = 10

#: Generous ceiling for brute-force bucket recovery (see
#: recover_review_bucket). At bucket size 10, 500 buckets covers up to 5,000
#: reviews -- comfortably beyond any small/medium local business -- and
#: hashing 500 candidates is cheap (no I/O involved).
MAX_REVIEW_COUNT_FOR_RECOVERY = 5000

#: Every operational-status value the Places API can return, plus a fallback
#: for anything unrecognized. Small and fixed, so brute-force recovery is
#: trivial.
BUSINESS_STATUS_VALUES: tuple[str, ...] = (
    "OPERATIONAL",
    "CLOSED_TEMPORARILY",
    "CLOSED_PERMANENTLY",
    "UNKNOWN",
)


class SignalDetectionError(ValueError):
    """Raised for invalid inputs to signal-detection helpers."""


def review_count_bucket(count: int, *, bucket_size: int = REVIEW_COUNT_BUCKET_SIZE) -> int:
    """Discretize a raw review count into a coarse bucket index.

    Args:
        count: Raw review count (never persisted -- held in memory only).
        bucket_size: Reviews per bucket.

    Returns:
        The bucket index (0 = 0..bucket_size-1 reviews, 1 = next range, ...).

    Raises:
        SignalDetectionError: If ``count`` or ``bucket_size`` is invalid.
    """
    if count < 0:
        raise SignalDetectionError("count must not be negative")
    if bucket_size <= 0:
        raise SignalDetectionError("bucket_size must be positive")
    return count // bucket_size


def _fingerprint(value: str, *, lead_id: str, signal_type: str) -> str:
    """Compute the keyed hash stored in SignalCheckpoint.fingerprint_hash.

    Namespaced by both ``signal_type`` and ``lead_id`` so a hash from one
    signal type/lead can never collide with or be replayed against another --
    same reasoning as app.services.pii.blind_index's ``purpose`` namespacing.

    Args:
        value: The bucketed/derived value, as a string (never the raw Places
            field).
        lead_id: The lead this fingerprint belongs to.
        signal_type: Which signal type this fingerprint is for.

    Returns:
        A 64-character hex digest.
    """
    return blind_index(value, purpose=f"signal:{signal_type}:{lead_id}")


def review_bucket_fingerprint(bucket: int, *, lead_id: str) -> str:
    """Fingerprint a review-count bucket for storage.

    Args:
        bucket: The bucket index from :func:`review_count_bucket`.
        lead_id: The lead this fingerprint belongs to.

    Returns:
        The keyed hash to store in ``SignalCheckpoint.fingerprint_hash``.
    """
    return _fingerprint(str(bucket), lead_id=lead_id, signal_type="review_count_jump")


def business_status_fingerprint(status: str, *, lead_id: str) -> str:
    """Fingerprint a business-status value for storage.

    Args:
        status: The raw status string (e.g. ``"OPERATIONAL"``), normalized
            internally -- never persisted itself.
        lead_id: The lead this fingerprint belongs to.

    Returns:
        The keyed hash to store in ``SignalCheckpoint.fingerprint_hash``.
    """
    normalized = (status or "").strip().upper() or "UNKNOWN"
    if normalized not in BUSINESS_STATUS_VALUES:
        logger.warning(
            "Unrecognized Places business status; treating as UNKNOWN",
            extra={"raw_status": normalized},
        )
        normalized = "UNKNOWN"
    return _fingerprint(normalized, lead_id=lead_id, signal_type="business_status_change")


def recover_review_bucket(
    fingerprint_hash: str, *, lead_id: str, max_count: int = MAX_REVIEW_COUNT_FOR_RECOVERY
) -> int | None:
    """Recover which review-count bucket a stored fingerprint represents.

    Brute-forces the small, bounded space of possible buckets rather than
    ever having stored the bucket itself -- see the module docstring.

    Args:
        fingerprint_hash: The stored checkpoint hash.
        lead_id: The lead the fingerprint belongs to.
        max_count: Ceiling on review counts considered.

    Returns:
        The bucket index if a match is found, else None (the previous value
        predates this feature, or exceeds ``max_count``).
    """
    max_bucket = max_count // REVIEW_COUNT_BUCKET_SIZE
    for candidate in range(0, max_bucket + 1):
        if review_bucket_fingerprint(candidate, lead_id=lead_id) == fingerprint_hash:
            return candidate
    return None


def recover_business_status(fingerprint_hash: str, *, lead_id: str) -> str | None:
    """Recover which business-status value a stored fingerprint represents.

    Args:
        fingerprint_hash: The stored checkpoint hash.
        lead_id: The lead the fingerprint belongs to.

    Returns:
        The status string if a match is found, else None.
    """
    for candidate in BUSINESS_STATUS_VALUES:
        if business_status_fingerprint(candidate, lead_id=lead_id) == fingerprint_hash:
            return candidate
    return None


@dataclass(frozen=True)
class ReviewCountComparison:
    """Outcome of comparing a freshly-fetched review count against a checkpoint.

    Attributes:
        is_first_observation: True if there was no prior checkpoint to
            compare against (a baseline, not a change).
        bucket_delta: How many buckets the count moved, positive for growth.
            None when the previous bucket could not be recovered (predates
            this feature, or exceeds the recovery ceiling).
        is_jump: True if this should fire a REVIEW_COUNT_JUMP signal --
            requires a recovered, positive bucket_delta.
    """

    is_first_observation: bool
    bucket_delta: int | None
    is_jump: bool


def compare_review_count(
    current_count: int,
    *,
    lead_id: str,
    previous_fingerprint: str | None,
    min_bucket_increase: int = 1,
) -> tuple[ReviewCountComparison, str]:
    """Compare a fresh review count against a stored checkpoint fingerprint.

    Args:
        current_count: Freshly fetched raw review count (never persisted).
        lead_id: The lead being checked.
        previous_fingerprint: The lead's stored checkpoint hash, or None if
            this is the first check.
        min_bucket_increase: Minimum upward bucket movement to count as a jump.

    Returns:
        A tuple of (comparison result, the new fingerprint to persist as the
        checkpoint).
    """
    current_bucket = review_count_bucket(current_count)
    new_fingerprint = review_bucket_fingerprint(current_bucket, lead_id=lead_id)

    if previous_fingerprint is None:
        return (
            ReviewCountComparison(is_first_observation=True, bucket_delta=None, is_jump=False),
            new_fingerprint,
        )

    if previous_fingerprint == new_fingerprint:
        return (
            ReviewCountComparison(is_first_observation=False, bucket_delta=0, is_jump=False),
            new_fingerprint,
        )

    previous_bucket = recover_review_bucket(previous_fingerprint, lead_id=lead_id)
    if previous_bucket is None:
        logger.warning(
            "Could not recover previous review-count bucket from checkpoint hash; "
            "treating as an unquantified change",
            extra={"lead_id": lead_id},
        )
        return (
            ReviewCountComparison(is_first_observation=False, bucket_delta=None, is_jump=True),
            new_fingerprint,
        )

    delta = current_bucket - previous_bucket
    return (
        ReviewCountComparison(
            is_first_observation=False,
            bucket_delta=delta,
            is_jump=delta >= min_bucket_increase,
        ),
        new_fingerprint,
    )


@dataclass(frozen=True)
class BusinessStatusComparison:
    """Outcome of comparing a freshly-fetched business status against a checkpoint.

    Attributes:
        is_first_observation: True if there was no prior checkpoint.
        previous_status: The recovered previous status, if any.
        current_status: The normalized current status.
        changed: True if the status differs from the previous checkpoint.
    """

    is_first_observation: bool
    previous_status: str | None
    current_status: str
    changed: bool


def compare_business_status(
    current_status: str,
    *,
    lead_id: str,
    previous_fingerprint: str | None,
) -> tuple[BusinessStatusComparison, str]:
    """Compare a fresh business status against a stored checkpoint fingerprint.

    Args:
        current_status: Freshly fetched raw status string (never persisted).
        lead_id: The lead being checked.
        previous_fingerprint: The lead's stored checkpoint hash, or None if
            this is the first check.

    Returns:
        A tuple of (comparison result, the new fingerprint to persist).
    """
    normalized = (current_status or "").strip().upper() or "UNKNOWN"
    if normalized not in BUSINESS_STATUS_VALUES:
        normalized = "UNKNOWN"
    new_fingerprint = business_status_fingerprint(normalized, lead_id=lead_id)

    if previous_fingerprint is None:
        return (
            BusinessStatusComparison(
                is_first_observation=True,
                previous_status=None,
                current_status=normalized,
                changed=False,
            ),
            new_fingerprint,
        )

    if previous_fingerprint == new_fingerprint:
        return (
            BusinessStatusComparison(
                is_first_observation=False,
                previous_status=normalized,
                current_status=normalized,
                changed=False,
            ),
            new_fingerprint,
        )

    previous_status = recover_business_status(previous_fingerprint, lead_id=lead_id)
    return (
        BusinessStatusComparison(
            is_first_observation=False,
            previous_status=previous_status,
            current_status=normalized,
            changed=True,
        ),
        new_fingerprint,
    )


def content_change_fingerprint(text: str, *, lead_id: str, purpose: str) -> str:
    """Fingerprint arbitrary text content for change detection (e.g. a careers page).

    Unlike the Places-derived fingerprints above, this has no Google Maps
    Content restriction -- it hashes content from the lead's OWN website. It
    is still hashed rather than stored verbatim so a checkpoint never grows
    into an unbounded copy of a prospect's web page.

    Args:
        text: The content to fingerprint (e.g. cleaned page text).
        lead_id: The lead this fingerprint belongs to.
        purpose: A namespace string, e.g. the URL checked.

    Returns:
        A 64-character hex digest.

    Raises:
        SignalDetectionError: If ``text`` is blank.
    """
    if not text.strip():
        raise SignalDetectionError("text must not be blank")
    # blind_index lowercases/strips and requires a non-blank value -- reuse
    # it directly rather than duplicating that normalization.
    return blind_index(text, purpose=f"job_posting:{purpose}:{lead_id}")
