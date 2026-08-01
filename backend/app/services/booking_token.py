"""HMAC-signed, expiring booking tokens for the public calendar-booking link.

Unlike the unsubscribe/erasure tokens in :mod:`app.services.canspam` --
which never expire, because an opt-out request must remain honoured
indefinitely -- a booking link is a standing, unauthenticated *write*
capability against the one shared sales calendar (see
:mod:`app.services.google_calendar`). Anyone holding the link can put an
event on that calendar for as long as it keeps working, so every token
carries its own expiry and there is no way to construct one that doesn't.

The token encodes and signs its own payload (lead id, the inbound message
that triggered it, and its expiry) rather than just hashing known values,
so verification never needs a database lookup, and the expiry is inside the
signed payload itself rather than a side channel a caller could forget to
check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FIELD_SEPARATOR = ":"


class InvalidBookingTokenError(ValueError):
    """Raised when a booking token is malformed, forged, or expired."""


@dataclass(frozen=True)
class BookingTokenPayload:
    """The decoded, verified contents of a booking token.

    Attributes:
        lead_id: The lead this booking link was issued for.
        triggering_message_id: The inbound message that caused the link to
            be generated, or None if it wasn't tied to a specific reply.
        expires_at: When this token stops being accepted.
    """

    lead_id: str
    triggering_message_id: str | None
    expires_at: datetime


def make_booking_token(
    lead_id: str,
    triggering_message_id: str | None,
    expires_at: datetime,
    secret: str,
) -> str:
    """Build a signed, self-describing, expiring booking token.

    Args:
        lead_id: The lead this booking link is being issued for.
        triggering_message_id: The inbound message that triggered
            generation, if any.
        expires_at: When the token should stop being accepted. Must be
            timezone-aware.
        secret: Application signing secret.

    Returns:
        A URL-safe token string of the form ``"<payload>.<signature>"``.

    Raises:
        ValueError: If ``secret`` is blank or ``expires_at`` is naive.
    """
    if not secret.strip():
        raise ValueError("a non-empty signing secret is required for booking tokens")
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")

    payload = _FIELD_SEPARATOR.join(
        [lead_id, triggering_message_id or "", str(int(expires_at.timestamp()))]
    )
    payload_b64 = _b64encode(payload.encode("utf-8"))
    signature = _sign(payload_b64, secret)
    return f"{payload_b64}.{signature}"


def verify_booking_token(
    token: str, secret: str, *, now: datetime | None = None
) -> BookingTokenPayload:
    """Verify a booking token and decode its payload.

    Args:
        token: The token from the booking link.
        secret: Application signing secret.
        now: The current time, for expiry comparison. Defaults to the real
            current time -- overridable so tests can check expiry
            deterministically without sleeping or monkeypatching the clock.

    Returns:
        The decoded, verified payload.

    Raises:
        InvalidBookingTokenError: If the token is malformed, its signature
            doesn't match, or it has expired.
    """
    current_time = now if now is not None else datetime.now(timezone.utc)

    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidBookingTokenError("token is malformed") from exc

    try:
        expected_signature = _sign(payload_b64, secret)
    except ValueError as exc:
        raise InvalidBookingTokenError(str(exc)) from exc
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidBookingTokenError("token signature does not match")

    try:
        payload = _b64decode(payload_b64).decode("utf-8")
        lead_id, message_id_raw, expires_at_raw = payload.split(_FIELD_SEPARATOR, 2)
        expires_at = datetime.fromtimestamp(int(expires_at_raw), tz=timezone.utc)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidBookingTokenError("token payload is malformed") from exc

    if current_time >= expires_at:
        raise InvalidBookingTokenError("token has expired; request a new booking link")

    logger.info("Booking token verified", extra={"lead_id": lead_id})
    return BookingTokenPayload(
        lead_id=lead_id,
        triggering_message_id=message_id_raw or None,
        expires_at=expires_at,
    )


def build_booking_url(base_url: str, token: str) -> str:
    """Build the public booking link URL sent to a prospect.

    Args:
        base_url: Public base URL of the API, without a trailing slash.
        token: The signed booking token.

    Returns:
        An absolute booking URL. The token is carried as a path segment
        (it contains no reserved URL characters -- see :func:`_b64encode`)
        rather than a query parameter, so it reads as one resource
        identifier rather than something invitingly editable.
    """
    return f"{base_url.rstrip('/')}/api/v1/booking/{token}"


def _sign(payload_b64: str, secret: str) -> str:
    """HMAC-SHA256 sign an already-encoded payload.

    Args:
        payload_b64: The base64url-encoded payload.
        secret: Application signing secret.

    Returns:
        A hex digest signature.

    Raises:
        ValueError: If ``secret`` is blank.
    """
    if not secret.strip():
        raise ValueError("a non-empty signing secret is required for booking tokens")
    return hmac.new(
        secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _b64encode(raw: bytes) -> str:
    """URL-safe base64 encode with padding stripped.

    Padding ("=") is redundant in a URL path segment and would need
    percent-encoding anyway, so it's dropped here and restored on decode.

    Args:
        raw: The bytes to encode.

    Returns:
        A URL-safe base64 string with no padding.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(encoded: str) -> bytes:
    """Reverse of :func:`_b64encode`, restoring stripped padding.

    Args:
        encoded: The URL-safe base64 string, without padding.

    Returns:
        The decoded bytes.
    """
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)
