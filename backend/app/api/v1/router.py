"""Aggregates all v1 routers under the ``/api/v1`` prefix."""

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    audits,
    booking,
    deliverability,
    dev_auth,
    email_enrichment,
    lead_scores,
    leads,
    outreach,
    pipeline,
    places,
    privacy,
    services,
    signals,
)

api_router = APIRouter(prefix="/api/v1")
# Always mounted (dev/staging/production alike) so its 404-in-production
# behavior is exercised for real rather than relying on the route never
# being registered -- see dev_auth.py's _require_non_production().
api_router.include_router(dev_auth.router)
api_router.include_router(services.router)
api_router.include_router(leads.router)
api_router.include_router(places.router)
api_router.include_router(audits.router)
api_router.include_router(lead_scores.router)
api_router.include_router(outreach.router)
api_router.include_router(pipeline.router)
api_router.include_router(analytics.router)
api_router.include_router(privacy.router)
api_router.include_router(signals.router)
api_router.include_router(email_enrichment.router)
api_router.include_router(deliverability.router)
api_router.include_router(booking.router)
api_router.include_router(booking.leads_router)

__all__ = ["api_router"]
