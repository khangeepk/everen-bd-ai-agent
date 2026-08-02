"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def _configure_sentry() -> None:
    """Initialize Sentry error alerting, if a DSN is configured.

    A no-op when ``settings.sentry_dsn`` is blank (the default), so this
    dependency is inert until a real Sentry project DSN is set -- see
    DEPLOYMENT.md for account setup. Import is deferred into the function
    body so environments that never configure Sentry never pay the import
    cost, and so a missing/broken sentry_sdk install can't break app startup
    for everyone else.
    """
    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN not set; Sentry error alerting is disabled")
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    from app.core.sentry_scrub import scrub_pii_from_event

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        # PII (contact_email/contact_phone, etc.) must never leave this
        # system via a third-party error-tracking payload -- see AGENTS.md
        # section 9's encryption-at-rest rationale, which this preserves in
        # spirit for anything crossing a network boundary too. Three layers:
        #   1. send_default_pii=False   -> no auto request/user/cookie capture
        #   2. include_local_variables=False -> don't capture stack-frame locals
        #      (a frame could hold contact_email="..." etc.)
        #   3. before_send scrubber     -> redact any email/phone still present
        #      in exception messages, extra context, or breadcrumbs.
        send_default_pii=False,
        include_local_variables=False,
        before_send=scrub_pii_from_event,
    )
    logger.info("Sentry error alerting enabled", extra={"app_env": settings.app_env})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, error alerting on startup and log shutdown.

    Args:
        app: The FastAPI application.

    Yields:
        Control back to the server for the lifetime of the app.
    """
    configure_logging()
    _configure_sentry()
    logger.info("Application starting", extra={"app_env": settings.app_env})
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="Everen BD Agent API",
    version="0.1.0",
    description="Business development automation with a human-approval gate on all outreach.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.include_router(api_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Propagate an ``X-Request-ID`` header for cross-service traceability.

    Args:
        request: The incoming request.
        call_next: The next handler in the middleware chain.

    Returns:
        The response, with ``X-Request-ID`` set.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler that logs the stack trace and returns a safe payload.

    Args:
        request: The request that failed.
        exc: The unhandled exception.

    Returns:
        A 500 response with no internal detail leaked.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "code": "INTERNAL_ERROR",
            "request_id": request_id,
        },
    )


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Report that the process is alive.

    Used as the Docker ``HEALTHCHECK`` and Render's ``healthCheckPath`` --
    deliberately does not touch the database, so a slow/degraded DB does not
    cause the orchestrator to kill and restart an otherwise-healthy process.

    Returns:
        A status payload.
    """
    return {"status": "ok", "env": settings.app_env}


@app.get("/health/ready", tags=["system"], summary="Readiness probe")
async def health_ready() -> JSONResponse:
    """Report whether the process can actually serve traffic.

    Unlike ``/health``, this executes a trivial query against the database,
    so an uptime monitor (see DEPLOYMENT.md's UptimeRobot setup) hitting this
    endpoint instead of ``/health`` will actually catch "process is up but
    the database is unreachable" -- the failure mode a pure liveness probe
    cannot see.

    Returns:
        200 with ``{"status": "ready"}`` if the database responds, or 503
        with ``{"status": "not_ready", "reason": ...}`` otherwise.
    """
    from sqlalchemy import text

    from app.db.session import SessionFactory

    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any DB failure means "not ready"
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"database error: {exc.__class__.__name__}"},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "env": settings.app_env})
