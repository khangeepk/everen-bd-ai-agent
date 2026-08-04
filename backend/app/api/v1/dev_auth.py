"""Local development session endpoint.

Lets a developer running the stack without a Clerk/Auth.js account obtain a
genuinely verified session (see app.core.dev_auth) instead of a static bearer
token or a disabled auth check. Hard-disabled in production: see
``_require_non_production`` below, which returns 404 rather than 403 so the
endpoint's existence isn't even disclosed to a production caller.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.dev_auth import DEV_SESSION_TTL_MINUTES, mint_dev_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dev", tags=["dev"])


class DevSessionResponse(BaseModel):
    """A locally-minted session token for development use only."""

    access_token: str = Field(..., description="Bearer token for the Authorization header.")
    token_type: str = Field(default="bearer")
    expires_in_minutes: int = Field(..., description="Token lifetime from issuance.")


def _require_non_production() -> None:
    """Raise 404 if running in production.

    404 (not 403) so this route's existence is indistinguishable from a
    route that was never defined, in case this router is ever mistakenly
    left mounted against a production deployment.

    Raises:
        HTTPException: 404 when ``settings.is_production`` is True.
    """
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.post(
    "/session",
    response_model=DevSessionResponse,
    summary="Mint a local development session (non-production only)",
    description=(
        "Returns a short-lived bearer token for local development, so the "
        "frontend can call protected endpoints without a Clerk/Auth.js "
        "account. Always 404s when APP_ENV=production."
    ),
)
async def create_dev_session() -> DevSessionResponse:
    """Issue a local dev session token.

    Returns:
        A bearer token valid for DEV_SESSION_TTL_MINUTES.
    """
    _require_non_production()
    token = mint_dev_session_token()
    logger.info("Issued local dev session")
    return DevSessionResponse(access_token=token, expires_in_minutes=DEV_SESSION_TTL_MINUTES)


@router.get(
    "/sentry-test",
    summary="Deliberately raise an error to verify Sentry capture (non-production only)",
    description=(
        "Raises an unhandled exception on purpose. If SENTRY_DSN is set (see "
        "app.main._configure_sentry), Sentry's Starlette/FastAPI integration "
        "captures it automatically -- check your Sentry project's Issues tab "
        "afterward. If SENTRY_DSN is blank, Sentry is a no-op and this just "
        "produces a normal 500, which is also a valid thing to confirm. "
        "Always 404s when APP_ENV=production."
    ),
)
async def trigger_sentry_test_error() -> None:
    """Deliberately raise to verify Sentry error capture end-to-end.

    Raises:
        HTTPException: 404 when ``settings.is_production`` is True.
        RuntimeError: Always, in every other environment -- that's the point.
    """
    _require_non_production()
    logger.info("Deliberately triggering a test error for Sentry verification")
    raise RuntimeError(
        "Deliberate test error from GET /api/v1/dev/sentry-test -- if this "
        "shows up in Sentry's Issues tab, PII-scrubbed error capture is "
        "confirmed working end-to-end."
    )
