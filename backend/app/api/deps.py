"""Shared FastAPI dependencies: authenticated user resolution and RBAC."""

from __future__ import annotations

import logging
import secrets

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.claims import IdentityClaims
from app.core.security import get_identity
from app.db.base import utcnow
from app.db.models.user import User, UserRole
from app.db.session import get_db

logger = logging.getLogger(__name__)


def _coerce_role(raw: str | None) -> UserRole:
    """Map a provider role string onto a :class:`UserRole`.

    Unrecognized or missing roles fall back to VIEWER -- the actual
    least-privileged role now that require_write_access enforces something
    for it to be least-privileged relative to (previously this defaulted to
    a write-capable role, which didn't fail closed against a role claim the
    identity provider never sent).

    Args:
        raw: Role string from the token, if any.

    Returns:
        The matching role, or ``UserRole.VIEWER``.
    """
    if not raw:
        return UserRole.VIEWER
    try:
        return UserRole(raw.strip().lower())
    except ValueError:
        logger.warning("Unrecognized role claim %r; defaulting to viewer", raw)
        return UserRole.VIEWER


async def get_current_user(
    claims: IdentityClaims = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the local :class:`User` row for the authenticated caller.

    Creates the row on first sight (just-in-time provisioning) and refreshes
    ``last_seen_at`` on every request.

    Args:
        claims: Verified identity claims.
        db: Active database session.

    Returns:
        The persisted :class:`User`.

    Raises:
        HTTPException: 403 if the user has been deactivated locally.
    """
    result = await db.execute(
        select(User).where(User.provider_subject == claims.subject)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            provider_subject=claims.subject,
            email=claims.email,
            full_name=claims.full_name,
            role=_coerce_role(claims.role),
        )
        db.add(user)
        await db.flush()
        logger.info(
            "Provisioned new user", extra={"user_id": str(user.id), "subject": claims.subject}
        )

    if not user.is_active:
        logger.warning("Deactivated user attempted access", extra={"user_id": str(user.id)})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated"
        )

    user.last_seen_at = utcnow()
    return user


async def require_write_access(user: User = Depends(get_current_user)) -> User:
    """Gate a mutating endpoint behind write-capable roles.

    Applied to every POST/PATCH/PUT/DELETE route in the API (except the
    public, token-verified routes -- unsubscribe, bounce webhook, the
    tracking pixel, and the erasure endpoint -- which are not
    role-authenticated at all). GET routes are left on plain
    ``get_current_user`` so VIEWER can still read everything.

    Args:
        user: The authenticated user.

    Returns:
        The same user, when authorized.

    Raises:
        HTTPException: 403 if the user's role is read-only (VIEWER).
    """
    if not user.can_write():
        logger.warning(
            "Write access denied for role", extra={"user_id": str(user.id), "role": user.role.value}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role has read-only access",
        )
    return user


async def require_approver(user: User = Depends(get_current_user)) -> User:
    """Gate an endpoint behind outreach-approval privileges.

    Used by the outreach approve/send routes (AGENTS.md section 8).

    Args:
        user: The authenticated user.

    Returns:
        The same user, when authorized.

    Raises:
        HTTPException: 403 if the user may not approve outreach.
    """
    if not user.can_approve_outreach():
        logger.warning(
            "Approval denied for role", extra={"user_id": str(user.id), "role": user.role.value}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role may not approve outreach drafts",
        )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate an endpoint behind the ADMIN role specifically.

    Used for things that are neither routine BD writes nor outreach
    approval: API cost/budget status, and anything else that's
    organization-administrative rather than sales-operational.

    Args:
        user: The authenticated user.

    Returns:
        The same user, when authorized.

    Raises:
        HTTPException: 403 if the user is not an admin.
    """
    if user.role is not UserRole.ADMIN:
        logger.warning(
            "Admin-only access denied for role",
            extra={"user_id": str(user.id), "role": user.role.value},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This endpoint is admin-only"
        )
    return user


def verify_webhook_secret(
    x_webhook_secret: str | None = Header(default=None),
) -> None:
    """Validate the ``X-Webhook-Secret`` header on machine-to-machine endpoints.

    Used exclusively by the n8n → FastAPI outreach-pause webhook
    (``POST /api/v1/outreach/pause``). This dependency is intentionally
    separate from the JWT-based human authentication flow because n8n
    cannot obtain a JWT without a full OAuth round-trip.

    Validation is skipped entirely when ``settings.n8n_webhook_secret``
    is blank — a deliberate dev/test convenience that must never be left
    blank in production (the ``Settings`` validator logs a warning in that
    case).

    Args:
        x_webhook_secret: Value of the ``X-Webhook-Secret`` request header,
            or ``None`` if the header is absent.

    Raises:
        HTTPException: 401 if the secret is wrong or the header is missing
            and a secret is configured.
    """
    from app.core.config import settings  # local import avoids circular dep

    configured = settings.n8n_webhook_secret
    if not configured:
        logger.warning(
            "N8N_WEBHOOK_SECRET is not set — webhook auth disabled. "
            "Set this in production."
        )
        return  # dev/test pass-through

    if not x_webhook_secret or not secrets.compare_digest(
        x_webhook_secret.encode(), configured.encode()
    ):
        logger.warning(
            "Webhook secret validation failed — request rejected",
            extra={"header_present": x_webhook_secret is not None},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Webhook-Secret header",
        )


async def verify_sendgrid_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Twilio-Email-Event-Webhook-Signature"),
    timestamp: str | None = Header(default=None, alias="X-Twilio-Email-Event-Webhook-Timestamp"),
) -> bytes:
    """Validate SendGrid event webhook signature against untouched raw body bytes.

    Fail-closed in production: if signature/timestamp/key is missing or invalid,
    raises 401 Unauthorized. In dev mode (app_env != 'production'), if
    sendgrid_webhook_verification_key is empty, logs a warning and allows bypass.

    Args:
        request: Incoming FastAPI Request.
        signature: Value of ``X-Twilio-Email-Event-Webhook-Signature`` header.
        timestamp: Value of ``X-Twilio-Email-Event-Webhook-Timestamp`` header.

    Returns:
        The verified untouched raw request body bytes.

    Raises:
        HTTPException: 401 Unauthorized if verification fails or unconfigured in production.
    """
    from app.core.config import settings
    from app.core.security import verify_sendgrid_webhook_signature

    raw_body = await request.body()
    key = settings.sendgrid_webhook_verification_key

    if not key:
        if settings.is_production:
            logger.error("SENDGRID_WEBHOOK_VERIFICATION_KEY is empty in production — failing closed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Webhook verification key unconfigured in production",
            )
        else:
            logger.warning("SENDGRID_WEBHOOK_VERIFICATION_KEY is empty — skipping signature verification in dev mode")
            return raw_body

    if not signature or not timestamp:
        logger.warning("SendGrid webhook rejected: missing signature or timestamp header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required SendGrid signature headers",
        )

    valid = verify_sendgrid_webhook_signature(key, raw_body, signature, timestamp)
    if not valid:
        logger.warning("SendGrid webhook rejected: ECDSA signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SendGrid webhook signature",
        )

    return raw_body

