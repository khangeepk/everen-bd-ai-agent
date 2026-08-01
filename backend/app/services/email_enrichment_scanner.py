"""Orchestrates the email-enrichment fallback chain for one lead.

Rep-triggered, mirroring app/services/signal_scanner.py's on-demand
pattern (which itself mirrors the website audit agent) -- no bulk/automatic
path. Nothing in this codebase schedules a run.

Chain order (strict fallback, not "try both and merge"):

1. Website contact/footer page (app.services.email_discovery).
2. Common-pattern guess (app.services.email_enrichment.guess_pattern_emails)
   -- only attempted if step 1 found nothing.

Every candidate from whichever step ran is recorded as an
:class:`~app.db.models.email_enrichment.EmailEnrichmentAttempt` (full
history, even the ones not picked). The highest-confidence candidate is
applied to ``Lead.contact_email`` via ``Lead.set_contact_email()`` with
``verified=False`` -- which, per the change in
``app/services/outreach_policy.py``, blocks email draft generation for this
lead until a human confirms it via ``POST /leads/{id}/email/verify``. A lead
that already has a verified email is left untouched -- this chain only ever
fills a gap, never silently overwrites a trusted address.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.email_enrichment import EmailEnrichmentAttempt
from app.db.models.lead import Lead
from app.services import email_discovery
from app.services.email_enrichment import (
    EmailCandidate,
    EmailEnrichmentError,
    guess_pattern_emails,
)

logger = logging.getLogger(__name__)


@dataclass
class EmailEnrichmentOutcome:
    """What happened during one enrichment run.

    Attributes:
        candidates: Every candidate found this run, across whichever chain
            step(s) actually ran.
        applied: The candidate written to Lead.contact_email, if any.
        skipped_reason: Why nothing ran or nothing was applied, if applicable.
    """

    candidates: list[EmailCandidate] = field(default_factory=list)
    applied: EmailCandidate | None = None
    skipped_reason: str | None = None


async def enrich_lead_email(db: AsyncSession, lead: Lead) -> EmailEnrichmentOutcome:
    """Run the fallback email-discovery chain for one lead.

    Args:
        db: Active database session. Caller is responsible for committing
            (this only flushes, per this codebase's service-layer convention).
        lead: The lead to enrich.

    Returns:
        The outcome: every candidate found, and which one (if any) was
        applied.
    """
    outcome = EmailEnrichmentOutcome()

    if lead.contact_email and lead.contact_email_verified:
        outcome.skipped_reason = "Lead already has a verified email on file; not overwriting it."
        logger.info(
            "Email enrichment skipped: already has a verified email",
            extra={"lead_id": str(lead.id)},
        )
        return outcome

    if not lead.website:
        outcome.skipped_reason = "Lead has no website on file to search."
        logger.info("Email enrichment skipped: no website on file", extra={"lead_id": str(lead.id)})
        return outcome

    now = utcnow()

    try:
        candidates = await email_discovery.find_contact_page_emails(str(lead.website))
    except EmailEnrichmentError:
        logger.warning(
            "Email enrichment: contact-page step could not reach the site",
            extra={"lead_id": str(lead.id)},
        )
        candidates = []

    chain_step = "website_contact_page"
    if not candidates:
        candidates = guess_pattern_emails(str(lead.website), lead.contact_name)
        chain_step = "pattern_guess"

    outcome.candidates = candidates

    if not candidates:
        outcome.skipped_reason = (
            "No contact-page email found and no pattern could be guessed "
            "(no contact name on file)."
        )
        logger.info(
            "Email enrichment found no candidates",
            extra={"lead_id": str(lead.id), "chain_step_tried": chain_step},
        )
        return outcome

    attempt_rows: list[EmailEnrichmentAttempt] = []
    for candidate in candidates:
        row = EmailEnrichmentAttempt(
            lead_id=lead.id,
            source=candidate.source,
            candidate_email=candidate.email,
            confidence_score=candidate.confidence_score,
            evidence=candidate.evidence,
            was_applied=False,
            detected_at=now,
        )
        db.add(row)
        attempt_rows.append(row)

    best = max(candidates, key=lambda c: c.confidence_score)
    attempt_rows[candidates.index(best)].was_applied = True

    lead.set_contact_email(
        best.email, source=best.source, confidence_score=best.confidence_score, verified=False
    )
    outcome.applied = best

    await db.flush()
    logger.info(
        "Email enrichment applied a candidate",
        extra={
            "lead_id": str(lead.id),
            "source": best.source.value,
            "confidence_score": best.confidence_score,
            "candidates_found": len(candidates),
        },
    )
    return outcome
