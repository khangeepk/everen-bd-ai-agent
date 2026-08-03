"""Local-development-only auth session issuance.

This is NOT a replacement for real Clerk/Auth.js authentication (see
app.core.security) -- it exists so a developer running the stack on a laptop
with no identity-provider account can still get a genuinely verified session
to call protected endpoints, instead of either (a) pasting a hand-maintained
static bearer token into .env.local forever, or (b) disabling auth checks
altogether.

How it's kept safe for production:

* Every function here is a no-op / hard-fails when ``settings.is_production``
  is True -- see ``mint_dev_session_token`` and the router in
  ``app.api.v1.dev_auth``, which returns 404 (not just 403, so the endpoint's
  existence isn't even disclosed) in production.
* The token is signed with HS256 using ``settings.secret_key`` and a
  dev-only issuer string (``"everen-dev-local"``) that a real Clerk/Auth.js
  token can never carry (Clerk/Auth.js issue RS256 tokens with a provider
  URL as `iss`). ``app.core.security.get_identity`` only accepts this path
  when both the issuer matches AND the app is not running in production --
  two independent conditions, not one.
* Sessions are short-lived (``DEV_SESSION_TTL_MINUTES``) and re-issued by the
  frontend on demand -- there is no long-lived secret to leak.

See app.api.v1.dev_auth for the endpoint, and
frontend/src/lib/devSession.ts for how the frontend obtains and uses one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Marks a token as issued by this module rather than a real identity
#: provider. get_identity() checks for this exact issuer before ever trying
#: the HS256/secret_key verification path, so a token from a real provider
#: (which will never carry this iss) is unaffected.
DEV_ISSUER = "everen-dev-local"

#: How long a locally-minted dev session stays valid. Short enough that a
#: forgotten local instance doesn't hold a working session forever; long
#: enough that a developer isn't re-authenticating every few minutes.
DEV_SESSION_TTL_MINUTES = 720  # 12 hours


def mint_dev_session_token(
    *,
    subject: str = "dev-local-user",
    email: str = "dev@localhost",
    full_name: str = "Local Dev User",
    role: str = "admin",
) -> str:
    """Mint a short-lived local dev session token.

    Args:
        subject: Stable local user id. Defaults to a fixed value so repeated
            calls provision/reuse the same local User row rather than
            spawning a new one per session.
        email: Email claim -- normalize_claims() requires one.
        full_name: Display name claim.
        role: Role claim -- "admin" by default so a solo developer isn't
            blocked by RBAC while exploring the app locally.

    Returns:
        An encoded HS256 JWT, valid for DEV_SESSION_TTL_MINUTES.

    Raises:
        RuntimeError: If called while ``settings.is_production`` is True.
            This should be unreachable in practice because the router
            guards the same condition first, but the function fails closed
            on its own too rather than trusting the one caller to remember.
    """
    if settings.is_production:
        raise RuntimeError("mint_dev_session_token() must never run in production")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "email": email,
        "name": full_name,
        "role": role,
        "iss": DEV_ISSUER,
        "aud": settings.auth_audience,
        "iat": now,
        "exp": now + timedelta(minutes=DEV_SESSION_TTL_MINUTES),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    logger.info(
        "Minted local dev session token",
        extra={"subject": subject, "ttl_minutes": DEV_SESSION_TTL_MINUTES},
    )
    return token


def decode_dev_session_token(token: str) -> dict[str, object] | None:
    """Attempt to decode ``token`` as a locally-minted dev session.

    Args:
        token: The raw bearer token from the Authorization header.

    Returns:
        The decoded payload if this is a valid, unexpired dev-issued token;
        None if it's expired, malformed, wrongly signed, or (most commonly)
        simply a real provider token that was never signed with
        ``settings.secret_key`` in the first place -- None here just means
        "not a dev token", so the caller falls through to real JWKS
        verification rather than treating this as an error.
    """
    if settings.is_production:
        return None
    try:
        return jwt.decode(
            token,
            key=settings.secret_key,
            algorithms=["HS256"],
            audience=settings.auth_audience,
            issuer=DEV_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.InvalidTokenError:
        return None
