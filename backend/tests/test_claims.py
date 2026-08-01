"""Tests for :mod:`app.core.claims`.

Covers both Clerk-shaped and Auth.js-shaped token payloads.
"""

from __future__ import annotations

import pytest

from app.core.claims import ClaimError, normalize_claims


def _base_payload(**overrides: object) -> dict:
    """Build a minimal valid payload with optional overrides.

    Args:
        **overrides: Claims to add or replace.

    Returns:
        A decoded-token-shaped dict.
    """
    payload = {
        "sub": "user_2abc",
        "iss": "https://everen.clerk.accounts.dev",
        "email": "sami@everen.example",
    }
    payload.update(overrides)
    return payload


def test_authjs_flat_claims_are_normalized() -> None:
    """A standard Auth.js payload maps cleanly onto IdentityClaims."""
    claims = normalize_claims(
        _base_payload(name="Sami Khan", role="bd_manager")
    )

    assert claims.subject == "user_2abc"
    assert claims.email == "sami@everen.example"
    assert claims.full_name == "Sami Khan"
    assert claims.role == "bd_manager"


def test_clerk_email_addresses_array_is_read() -> None:
    """Clerk's nested email_addresses array is flattened."""
    payload = _base_payload()
    del payload["email"]
    payload["email_addresses"] = [{"email_address": "ops@everen.example"}]

    assert normalize_claims(payload).email == "ops@everen.example"


def test_clerk_email_addresses_array_of_strings_is_read() -> None:
    """Some Clerk templates emit plain strings rather than objects."""
    payload = _base_payload()
    del payload["email"]
    payload["email_addresses"] = ["ops@everen.example"]

    assert normalize_claims(payload).email == "ops@everen.example"


def test_email_is_lowercased() -> None:
    """Emails normalize to lowercase so lookups are case-insensitive."""
    claims = normalize_claims(_base_payload(email="Sami.Khan@Everen.Example"))
    assert claims.email == "sami.khan@everen.example"


def test_whitespace_is_stripped_from_subject() -> None:
    """Padded subject claims are trimmed rather than stored raw."""
    assert normalize_claims(_base_payload(sub="  user_2abc  ")).subject == "user_2abc"


def test_missing_subject_is_rejected() -> None:
    """A token without 'sub' cannot identify a user."""
    payload = _base_payload()
    del payload["sub"]

    with pytest.raises(ClaimError, match="sub"):
        normalize_claims(payload)


def test_blank_subject_is_rejected() -> None:
    """An empty 'sub' is treated as missing."""
    with pytest.raises(ClaimError, match="sub"):
        normalize_claims(_base_payload(sub="   "))


def test_non_string_subject_is_rejected() -> None:
    """A numeric 'sub' is not accepted silently."""
    with pytest.raises(ClaimError, match="sub"):
        normalize_claims(_base_payload(sub=12345))


def test_missing_email_is_rejected() -> None:
    """Email is required for the approval queue's audit trail."""
    payload = _base_payload()
    del payload["email"]

    with pytest.raises(ClaimError, match="email"):
        normalize_claims(payload)


def test_missing_issuer_is_rejected() -> None:
    """The issuer must be recorded on the resolved identity."""
    payload = _base_payload()
    del payload["iss"]

    with pytest.raises(ClaimError, match="iss"):
        normalize_claims(payload)


def test_absent_optional_claims_become_none() -> None:
    """Name and role are optional and default to None."""
    claims = normalize_claims(_base_payload())

    assert claims.full_name is None
    assert claims.role is None


def test_name_claim_priority_order() -> None:
    """'name' wins over 'full_name' and 'given_name'."""
    claims = normalize_claims(
        _base_payload(name="Preferred", full_name="Secondary", given_name="Tertiary")
    )
    assert claims.full_name == "Preferred"


def test_claims_are_immutable() -> None:
    """IdentityClaims is frozen so a verified identity cannot be mutated."""
    claims = normalize_claims(_base_payload())

    with pytest.raises(Exception):
        claims.subject = "attacker"  # type: ignore[misc]
