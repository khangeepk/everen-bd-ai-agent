"""Tests for :mod:`app.services.booking_token`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.booking_token import (
    InvalidBookingTokenError,
    build_booking_url,
    make_booking_token,
    verify_booking_token,
)

_SECRET = "test-signing-secret"
_LEAD_ID = "11111111-1111-1111-1111-111111111111"
_MESSAGE_ID = "22222222-2222-2222-2222-222222222222"
_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
_FUTURE = _NOW + timedelta(days=21)


# ---------------------------------------------------------------------------
# make_booking_token / verify_booking_token round-trip
# ---------------------------------------------------------------------------


def test_round_trip_decodes_the_same_lead_and_message() -> None:
    """A freshly made token verifies and decodes back to what was encoded."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    payload = verify_booking_token(token, _SECRET, now=_NOW)
    assert payload.lead_id == _LEAD_ID
    assert payload.triggering_message_id == _MESSAGE_ID
    assert payload.expires_at == _FUTURE


def test_round_trip_with_no_triggering_message_decodes_to_none() -> None:
    """A booking link with no known triggering reply round-trips message_id as None."""
    token = make_booking_token(_LEAD_ID, None, _FUTURE, _SECRET)
    payload = verify_booking_token(token, _SECRET, now=_NOW)
    assert payload.triggering_message_id is None


def test_make_token_rejects_blank_secret() -> None:
    """A token can't be signed without a real signing secret."""
    with pytest.raises(ValueError):
        make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, "")


def test_make_token_rejects_naive_expiry() -> None:
    """An expiry without a timezone is ambiguous and must be rejected."""
    with pytest.raises(ValueError):
        make_booking_token(_LEAD_ID, _MESSAGE_ID, datetime(2026, 8, 21), _SECRET)


# ---------------------------------------------------------------------------
# Tamper resistance
# ---------------------------------------------------------------------------


def test_verify_rejects_a_token_with_flipped_signature_bytes() -> None:
    """Corrupting the signature half of the token must fail verification."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    payload_part, signature_part = token.rsplit(".", 1)
    flipped_char = "0" if signature_part[-1] != "0" else "1"
    tampered = f"{payload_part}.{signature_part[:-1]}{flipped_char}"
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token(tampered, _SECRET, now=_NOW)


def test_verify_rejects_wrong_secret() -> None:
    """A token signed with one secret must not verify under a different one."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token(token, "a-different-secret", now=_NOW)


def test_verify_rejects_a_token_with_no_separator() -> None:
    """A string with no '.' separator can't be a valid token."""
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token("not-a-real-token", _SECRET, now=_NOW)


def test_verify_rejects_garbage_payload_that_still_has_a_dot() -> None:
    """A malformed (non-base64, or wrong field count) payload is rejected, not crashed on."""
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token("///not-valid-base64.deadbeef", _SECRET, now=_NOW)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_verify_rejects_an_expired_token() -> None:
    """A token is rejected once 'now' reaches its expiry."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token(token, _SECRET, now=_FUTURE + timedelta(seconds=1))


def test_verify_rejects_a_token_exactly_at_its_expiry_instant() -> None:
    """Expiry is treated as an exclusive upper bound -- >=, not >, expires_at fails."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    with pytest.raises(InvalidBookingTokenError):
        verify_booking_token(token, _SECRET, now=_FUTURE)


def test_verify_accepts_a_token_one_second_before_expiry() -> None:
    """Right up until the instant of expiry, the token is still good."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    payload = verify_booking_token(token, _SECRET, now=_FUTURE - timedelta(seconds=1))
    assert payload.lead_id == _LEAD_ID


# ---------------------------------------------------------------------------
# build_booking_url
# ---------------------------------------------------------------------------


def test_build_booking_url_strips_trailing_slash_from_base() -> None:
    """A base URL with a trailing slash must not produce a double slash."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    url = build_booking_url("https://api.example.com/", token)
    assert url == f"https://api.example.com/api/v1/booking/{token}"


def test_build_booking_url_embeds_the_exact_token() -> None:
    """The URL must carry the token byte-for-byte, since verification depends on it."""
    token = make_booking_token(_LEAD_ID, _MESSAGE_ID, _FUTURE, _SECRET)
    url = build_booking_url("https://api.example.com", token)
    assert url.endswith(token)
