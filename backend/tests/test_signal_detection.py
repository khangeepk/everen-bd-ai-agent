"""Tests for :mod:`app.services.signal_detection`.

These encode the compliance decision behind the whole signals feature:
business-status and review-count facts are Google Maps Content
(app.services.places_policy.FORBIDDEN_FIELDS) and must never be recoverable
from what's stored -- only a keyed hash of a bucketed value is. If a change
here starts making raw values recoverable from stored state without the
in-memory value being supplied, the fix is almost never to relax the test.
"""

from __future__ import annotations

import pytest

from app.services.signal_detection import (
    BUSINESS_STATUS_VALUES,
    REVIEW_COUNT_BUCKET_SIZE,
    SignalDetectionError,
    business_status_fingerprint,
    compare_business_status,
    compare_review_count,
    content_change_fingerprint,
    recover_business_status,
    recover_review_bucket,
    review_bucket_fingerprint,
    review_count_bucket,
)

LEAD_A = "11111111-1111-1111-1111-111111111111"
LEAD_B = "22222222-2222-2222-2222-222222222222"


# --- review_count_bucket -----------------------------------------------------


def test_review_count_bucket_floors_to_bucket_size() -> None:
    """A count is floored to its bucket, not rounded."""
    assert review_count_bucket(0) == 0
    assert review_count_bucket(REVIEW_COUNT_BUCKET_SIZE - 1) == 0
    assert review_count_bucket(REVIEW_COUNT_BUCKET_SIZE) == 1
    assert review_count_bucket(REVIEW_COUNT_BUCKET_SIZE * 4 + 3) == 4


def test_review_count_bucket_rejects_negative() -> None:
    """A negative count is nonsensical and must raise, not silently floor to 0."""
    with pytest.raises(SignalDetectionError):
        review_count_bucket(-1)


# --- fingerprint determinism and namespacing --------------------------------


def test_review_bucket_fingerprint_is_deterministic() -> None:
    """The same bucket and lead always produce the same fingerprint."""
    assert review_bucket_fingerprint(5, lead_id=LEAD_A) == review_bucket_fingerprint(
        5, lead_id=LEAD_A
    )


def test_review_bucket_fingerprint_is_lead_namespaced() -> None:
    """The same bucket for two different leads must not collide.

    Otherwise every lead sitting in, say, bucket 3 would be trivially
    grouped by inspecting the raw column -- a privacy leak the per-lead
    namespacing exists specifically to prevent.
    """
    assert review_bucket_fingerprint(5, lead_id=LEAD_A) != review_bucket_fingerprint(
        5, lead_id=LEAD_B
    )


def test_business_status_fingerprint_normalizes_case_and_whitespace() -> None:
    """Casing/whitespace differences must not produce different fingerprints."""
    assert business_status_fingerprint(
        "operational", lead_id=LEAD_A
    ) == business_status_fingerprint("  OPERATIONAL  ", lead_id=LEAD_A)


def test_business_status_fingerprint_unrecognized_value_falls_back_to_unknown() -> None:
    """An unrecognized status string is treated as UNKNOWN, not passed through raw.

    This matters: if an unrecognized (and therefore un-vetted) string were
    hashed as-is, a sufficiently weird value could in principle be
    round-tripped in ways this module's design doesn't anticipate. Folding
    to a fixed UNKNOWN keeps the input space to the fingerprint exactly
    BUSINESS_STATUS_VALUES.
    """
    assert business_status_fingerprint(
        "SOME_FUTURE_STATUS_GOOGLE_ADDS_LATER", lead_id=LEAD_A
    ) == business_status_fingerprint("UNKNOWN", lead_id=LEAD_A)


# --- recovery (brute-force over the known small domain) ---------------------


def test_recover_review_bucket_finds_the_right_bucket() -> None:
    """A fingerprint recovers to the exact bucket it was computed from."""
    fingerprint = review_bucket_fingerprint(17, lead_id=LEAD_A)
    assert recover_review_bucket(fingerprint, lead_id=LEAD_A) == 17


def test_recover_review_bucket_returns_none_for_unknown_hash() -> None:
    """A hash from a different lead's namespace cannot be recovered against this one."""
    fingerprint = review_bucket_fingerprint(17, lead_id=LEAD_B)
    assert recover_review_bucket(fingerprint, lead_id=LEAD_A) is None


def test_recover_business_status_finds_the_right_status() -> None:
    """A fingerprint recovers to the exact status it was computed from."""
    fingerprint = business_status_fingerprint("CLOSED_TEMPORARILY", lead_id=LEAD_A)
    assert recover_business_status(fingerprint, lead_id=LEAD_A) == "CLOSED_TEMPORARILY"


def test_all_business_status_values_are_recoverable() -> None:
    """Every known status value round-trips through fingerprint -> recover."""
    for status in BUSINESS_STATUS_VALUES:
        fingerprint = business_status_fingerprint(status, lead_id=LEAD_A)
        assert recover_business_status(fingerprint, lead_id=LEAD_A) == status


# --- compare_review_count -----------------------------------------------------


def test_review_count_first_observation_is_not_a_jump() -> None:
    """The very first check establishes a baseline, not a detected change."""
    comparison, _ = compare_review_count(42, lead_id=LEAD_A, previous_fingerprint=None)
    assert comparison.is_first_observation is True
    assert comparison.is_jump is False


def test_review_count_same_bucket_is_not_a_jump() -> None:
    """A count that stays in the same bucket is not a jump."""
    _, checkpoint = compare_review_count(42, lead_id=LEAD_A, previous_fingerprint=None)
    comparison, _ = compare_review_count(44, lead_id=LEAD_A, previous_fingerprint=checkpoint)
    assert comparison.bucket_delta == 0
    assert comparison.is_jump is False


def test_review_count_increase_across_a_bucket_is_a_jump() -> None:
    """Crossing into a higher bucket fires a jump with a positive delta."""
    _, checkpoint = compare_review_count(42, lead_id=LEAD_A, previous_fingerprint=None)
    comparison, _ = compare_review_count(55, lead_id=LEAD_A, previous_fingerprint=checkpoint)
    assert comparison.bucket_delta == 1
    assert comparison.is_jump is True


def test_review_count_decrease_is_not_a_jump() -> None:
    """A decrease is tracked (non-null delta) but must never fire a 'jump' signal."""
    _, checkpoint = compare_review_count(80, lead_id=LEAD_A, previous_fingerprint=None)
    comparison, _ = compare_review_count(55, lead_id=LEAD_A, previous_fingerprint=checkpoint)
    assert comparison.bucket_delta is not None
    assert comparison.bucket_delta < 0
    assert comparison.is_jump is False


def test_review_count_min_bucket_increase_threshold_is_respected() -> None:
    """Raising min_bucket_increase suppresses single-bucket jumps."""
    _, checkpoint = compare_review_count(42, lead_id=LEAD_A, previous_fingerprint=None)
    comparison, _ = compare_review_count(
        55, lead_id=LEAD_A, previous_fingerprint=checkpoint, min_bucket_increase=2
    )
    assert comparison.bucket_delta == 1
    assert comparison.is_jump is False


def test_review_count_unrecoverable_checkpoint_is_treated_as_a_jump() -> None:
    """A checkpoint from a different lead's namespace can't be recovered -- fail safe.

    Rather than silently treating an unrecoverable previous value as "no
    change" (which could mask a real jump), this errs toward surfacing it.
    """
    _, foreign_checkpoint = compare_review_count(42, lead_id=LEAD_B, previous_fingerprint=None)
    comparison, _ = compare_review_count(
        61, lead_id=LEAD_A, previous_fingerprint=foreign_checkpoint
    )
    assert comparison.bucket_delta is None
    assert comparison.is_jump is True


# --- compare_business_status --------------------------------------------------


def test_business_status_first_observation_is_not_a_change() -> None:
    """The first check establishes a baseline, not a detected change."""
    comparison, _ = compare_business_status(
        "OPERATIONAL", lead_id=LEAD_A, previous_fingerprint=None
    )
    assert comparison.is_first_observation is True
    assert comparison.changed is False


def test_business_status_same_value_is_not_a_change() -> None:
    """An unchanged status does not fire a signal."""
    _, checkpoint = compare_business_status(
        "OPERATIONAL", lead_id=LEAD_A, previous_fingerprint=None
    )
    comparison, _ = compare_business_status(
        "OPERATIONAL", lead_id=LEAD_A, previous_fingerprint=checkpoint
    )
    assert comparison.changed is False


def test_business_status_change_is_detected() -> None:
    """A genuinely different status fires changed=True."""
    _, checkpoint = compare_business_status(
        "OPERATIONAL", lead_id=LEAD_A, previous_fingerprint=None
    )
    comparison, _ = compare_business_status(
        "CLOSED_TEMPORARILY", lead_id=LEAD_A, previous_fingerprint=checkpoint
    )
    assert comparison.changed is True
    assert comparison.previous_status == "OPERATIONAL"
    assert comparison.current_status == "CLOSED_TEMPORARILY"


# --- content_change_fingerprint (job postings) -------------------------------


def test_content_change_fingerprint_rejects_blank_text() -> None:
    """A blank page excerpt must raise, not silently hash to an empty-string fingerprint."""
    with pytest.raises(SignalDetectionError):
        content_change_fingerprint("   ", lead_id=LEAD_A, purpose="https://example.com/careers")


def test_content_change_fingerprint_is_deterministic() -> None:
    """Identical text/lead/purpose always fingerprints the same."""
    first = content_change_fingerprint(
        "we are hiring a dentist", lead_id=LEAD_A, purpose="https://example.com/careers"
    )
    second = content_change_fingerprint(
        "we are hiring a dentist", lead_id=LEAD_A, purpose="https://example.com/careers"
    )
    assert first == second


def test_content_change_fingerprint_differs_on_changed_text() -> None:
    """Different page content produces a different fingerprint."""
    before = content_change_fingerprint(
        "no openings right now", lead_id=LEAD_A, purpose="https://example.com/careers"
    )
    after = content_change_fingerprint(
        "we are hiring a dentist", lead_id=LEAD_A, purpose="https://example.com/careers"
    )
    assert before != after
