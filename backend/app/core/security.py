"""JWT verification against a remote JWKS endpoint.

Works unchanged with Clerk and Auth.js -- both publish RS256 public keys at a
JWKS URL. Point ``AUTH_JWKS_URL`` / ``AUTH_ISSUER`` at whichever provider is in
use; no vendor SDK is required. This satisfies the JWT requirement in
AGENTS.md section 2 without hardcoding a provider.

Keys are cached in-process for ``AUTH_JWKS_CACHE_SECONDS`` and refreshed once
on an unknown ``kid`` (handles provider key rotation).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.claims import ClaimError, IdentityClaims, normalize_claims
from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired authentication token",
    headers={"WWW-Authenticate": "Bearer"},
)


class JWKSCache:
    """Time-bounded cache around a :class:`jwt.PyJWKClient`.

    ``PyJWKClient`` does its own caching but has no TTL; wrapping it lets us
    force a refresh when a provider rotates signing keys.
    """

    def __init__(self, jwks_url: str, ttl_seconds: int) -> None:
        """Initialize the cache.

        Args:
            jwks_url: The provider's JWKS endpoint.
            ttl_seconds: How long a fetched key set stays valid.
        """
        self._jwks_url = jwks_url
        self._ttl = ttl_seconds
        self._client: PyJWKClient | None = None
        self._fetched_at: float = 0.0

    def _is_stale(self) -> bool:
        """Whether the cached client has aged past its TTL.

        Returns:
            True if a refresh is due.
        """
        return self._client is None or (time.monotonic() - self._fetched_at) > self._ttl

    def get_client(self, *, force_refresh: bool = False) -> PyJWKClient:
        """Return a JWKS client, refreshing it if stale or forced.

        Args:
            force_refresh: Rebuild the client even if it is still fresh.

        Returns:
            A :class:`jwt.PyJWKClient` for the configured JWKS URL.
        """
        if force_refresh or self._is_stale():
            logger.info("Refreshing JWKS", extra={"jwks_url": self._jwks_url})
            self._client = PyJWKClient(self._jwks_url, cache_keys=True)
            self._fetched_at = time.monotonic()
        assert self._client is not None
        return self._client

    def signing_key(self, token: str) -> Any:
        """Resolve the signing key for a token, retrying once on rotation.

        Args:
            token: The encoded JWT.

        Returns:
            The matching public signing key.

        Raises:
            jwt.PyJWKClientError: If no key matches even after a refresh.
        """
        try:
            return self.get_client().get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError:
            logger.warning("Unknown JWKS kid; forcing refresh")
            return self.get_client(force_refresh=True).get_signing_key_from_jwt(token).key


_jwks_cache = JWKSCache(settings.auth_jwks_url, settings.auth_jwks_cache_seconds)


def decode_token(token: str, config: Settings | None = None) -> dict[str, Any]:
    """Verify a JWT's signature and standard claims.

    Args:
        token: The encoded JWT from the ``Authorization`` header.
        config: Settings override, primarily for tests.

    Returns:
        The decoded payload.

    Raises:
        HTTPException: 401 if the token is malformed, expired, or fails
            signature, audience, or issuer validation.
    """
    cfg = config or settings
    try:
        key = _jwks_cache.signing_key(token)
        return jwt.decode(
            token,
            key=key,
            algorithms=cfg.auth_algorithms,
            audience=cfg.auth_audience,
            issuer=cfg.auth_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        logger.info("Rejected expired token")
        raise _UNAUTHORIZED from None
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        logger.warning("Rejected invalid token: %s", type(exc).__name__)
        raise _UNAUTHORIZED from exc
    except httpx.HTTPError as exc:
        logger.exception("JWKS endpoint unreachable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication provider unreachable",
        ) from exc


async def get_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> IdentityClaims:
    """FastAPI dependency returning the verified caller identity.

    Args:
        credentials: Bearer credentials extracted from the request.

    Returns:
        The normalized :class:`IdentityClaims`.

    Raises:
        HTTPException: 401 when the header is absent or the token is invalid.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED

    payload = decode_token(credentials.credentials)
    try:
        claims = normalize_claims(payload)
    except ClaimError as exc:
        logger.warning("Token verified but claims unusable: %s", exc)
        raise _UNAUTHORIZED from exc

    logger.info("Request authenticated", extra={"subject": claims.subject})
    return claims


def verify_sendgrid_webhook_signature(
    public_key_pem_or_b64: str,
    raw_body: bytes,
    signature_header: str | None,
    timestamp_header: str | None,
) -> bool:
    """Verify Twilio SendGrid Signed Event Webhook ECDSA signature.

    Uses standard cryptography hazmat primitives (ECDSA SHA256).
    The verification payload is ``timestamp_header.encode('utf-8') + raw_body``.

    Args:
        public_key_pem_or_b64: SendGrid verification key (PEM format or base64 DER string).
        raw_body: Raw, untouched HTTP request body bytes.
        signature_header: Value of ``X-Twilio-Email-Event-Webhook-Signature`` header.
        timestamp_header: Value of ``X-Twilio-Email-Event-Webhook-Timestamp`` header.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature_header or not timestamp_header or not public_key_pem_or_b64:
        return False

    try:
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key_str = public_key_pem_or_b64.strip()
        if not key_str.startswith("-----BEGIN"):
            key_pem = (
                f"-----BEGIN PUBLIC KEY-----\n{key_str}\n-----END PUBLIC KEY-----"
            ).encode("utf-8")
        else:
            key_pem = key_str.encode("utf-8")

        public_key = serialization.load_pem_public_key(key_pem)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False

        payload = timestamp_header.encode("utf-8") + raw_body
        signature = base64.b64decode(signature_header)

        public_key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        logger.warning("SendGrid webhook ECDSA signature verification raised exception")
        return False

