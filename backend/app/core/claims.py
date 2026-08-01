"""Identity-provider claim normalization and validation.

Deliberately dependency-free (standard library only) so the rules that decide
who a caller is can be unit-tested without a database, an HTTP client, or a
live identity provider.

Both Clerk and Auth.js issue RS256 JWTs but spell the profile claims
differently; :func:`normalize_claims` collapses those differences into one
shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Claim names that may carry the user's email, in priority order.
_EMAIL_CLAIMS: tuple[str, ...] = (
    "email",
    "email_address",
    "primary_email_address",
)

#: Claim names that may carry the user's display name, in priority order.
_NAME_CLAIMS: tuple[str, ...] = ("name", "full_name", "given_name")

#: Claim names that may carry an application role, in priority order.
_ROLE_CLAIMS: tuple[str, ...] = ("role", "public_role", "org_role")


class ClaimError(ValueError):
    """Raised when a token's claims are unusable for identifying a caller."""


@dataclass(frozen=True)
class IdentityClaims:
    """Provider-agnostic view of an authenticated caller.

    Attributes:
        subject: Stable provider-side user identifier (the ``sub`` claim).
        email: Primary email address, lowercased.
        full_name: Display name, if the provider supplied one.
        role: Raw role string from the token, if present.
        issuer: The ``iss`` claim the token was validated against.
    """

    subject: str
    email: str
    full_name: str | None
    role: str | None
    issuer: str


def _first_claim(payload: dict[str, Any], names: tuple[str, ...]) -> str | None:
    """Return the first non-empty string claim from ``names``.

    Nested Clerk shapes such as ``{"email_addresses": [...]}`` are not walked
    here; the caller flattens those before invoking this helper.

    Args:
        payload: Decoded JWT payload.
        names: Candidate claim names in priority order.

    Returns:
        The first non-empty string value found, else None.
    """
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_email(payload: dict[str, Any]) -> str | None:
    """Pull an email address out of a decoded token payload.

    Handles the flat claims used by Auth.js and the ``email_addresses`` array
    Clerk includes in some token templates.

    Args:
        payload: Decoded JWT payload.

    Returns:
        A lowercased email address, or None if none could be found.
    """
    flat = _first_claim(payload, _EMAIL_CLAIMS)
    if flat:
        return flat.lower()

    addresses = payload.get("email_addresses")
    if isinstance(addresses, list):
        for entry in addresses:
            if isinstance(entry, str) and entry.strip():
                return entry.strip().lower()
            if isinstance(entry, dict):
                candidate = entry.get("email_address") or entry.get("email")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip().lower()
    return None


def normalize_claims(payload: dict[str, Any]) -> IdentityClaims:
    """Convert a decoded JWT payload into :class:`IdentityClaims`.

    Signature verification, expiry, audience, and issuer checks are the
    responsibility of the caller (see :mod:`app.core.security`). This function
    assumes the token is already cryptographically trusted and only handles
    shape.

    Args:
        payload: Decoded and already-verified JWT payload.

    Returns:
        The normalized identity.

    Raises:
        ClaimError: If ``sub`` or an email address is missing. Both are
            required -- ``sub`` keys the local user row and email is the
            human-readable handle shown in the approval queue.
    """
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise ClaimError("Token is missing a usable 'sub' claim")

    email = _extract_email(payload)
    if not email:
        raise ClaimError(f"Token for sub={subject} carries no email claim")

    issuer = payload.get("iss")
    if not isinstance(issuer, str) or not issuer.strip():
        raise ClaimError(f"Token for sub={subject} is missing an 'iss' claim")

    return IdentityClaims(
        subject=subject.strip(),
        email=email,
        full_name=_first_claim(payload, _NAME_CLAIMS),
        role=_first_claim(payload, _ROLE_CLAIMS),
        issuer=issuer.strip(),
    )
