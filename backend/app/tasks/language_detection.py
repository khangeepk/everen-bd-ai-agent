"""Celery background task for lead language detection.

Detects the business language for a lead using the heuristic language
detection service (:mod:`app.services.language_detection`) and saves the
result in ``lead.detected_language``.

Triggered asynchronously after lead creation or update when website/country
changes, keeping HTTP route handlers fast and unblocked by website scraping.

See AGENTS.md section 6 for logging policies and section 7 for docstring rules.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.db.models.lead import Lead
from app.db.session import SessionFactory
from app.services.language_detection import detect_language

logger = logging.getLogger(__name__)


async def _async_detect_and_store(lead_id: uuid.UUID) -> str | None:
    """Run language detection for a single lead inside an async DB session.

    Args:
        lead_id: UUID of the lead to process.

    Returns:
        The detected BCP-47 language code, or None if undetermined / missing.
    """
    async with SessionFactory() as db:
        lead = await db.get(Lead, lead_id)
        if lead is None:
            logger.warning(
                "Language detection task: lead not found",
                extra={"lead_id": str(lead_id)},
            )
            return None

        # Skip if a manual override is already set
        if lead.language_override:
            logger.info(
                "Language detection skipped: manual override present",
                extra={"lead_id": str(lead_id), "language_override": lead.language_override},
            )
            return lead.language_override

        detected = await detect_language(lead.website, lead.country)
        lead.detected_language = detected
        await db.commit()

        logger.info(
            "Language detection task complete",
            extra={"lead_id": str(lead_id), "detected_language": detected},
        )
        return detected


def detect_and_store_language_task(lead_id_str: str) -> str | None:
    """Celery task entrypoint: detect language for a lead and save to DB.

    Args:
        lead_id_str: String representation of the lead UUID.

    Returns:
        The detected BCP-47 language code, or None.

    Raises:
        Exception: Re-raised after logging so Celery records task failure.
    """
    try:
        lead_id = uuid.UUID(lead_id_str)
        return asyncio.run(_async_detect_and_store(lead_id))
    except Exception:
        logger.exception(
            "Language detection background task failed",
            extra={"lead_id": lead_id_str},
        )
        raise
