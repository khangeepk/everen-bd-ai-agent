"""Scheduled retention enforcement for cached Places coordinates.

Google permits caching Places latitude/longitude for at most 30 consecutive
calendar days. This task deletes coordinates past that window.

It must be scheduled. Without it, compliance decays silently -- rows keep
coordinates indefinitely and nothing surfaces the problem.

Register with Celery beat, for example::

    beat_schedule = {
        "purge-expired-places-coordinates": {
            "task": "app.tasks.places_retention.purge_expired_place_coordinates",
            "schedule": crontab(hour=3, minute=0),
        },
    }

This task only deletes data. It sends nothing and touches no outreach object,
so it is unaffected by the human-approval gate in AGENTS.md section 8.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import SessionFactory
from app.services.places import PlaceDiscoveryService

logger = logging.getLogger(__name__)


async def _purge() -> int:
    """Run one purge pass inside its own session.

    Returns:
        The number of rows redacted.
    """
    async with SessionFactory() as session:
        service = PlaceDiscoveryService(db=session, client=None)  # type: ignore[arg-type]
        purged = await service.purge_expired_coordinates()
        await session.commit()
        return purged


def purge_expired_place_coordinates() -> int:
    """Celery entrypoint: delete Places coordinates past their retention window.

    Returns:
        The number of rows redacted.

    Raises:
        Exception: Re-raised after logging so the failure is visible to Celery
            rather than silently reducing compliance coverage.
    """
    try:
        purged = asyncio.run(_purge())
    except Exception:
        logger.exception("Places coordinate retention sweep failed")
        raise

    logger.info("Places coordinate retention sweep complete", extra={"rows_purged": purged})
    return purged
