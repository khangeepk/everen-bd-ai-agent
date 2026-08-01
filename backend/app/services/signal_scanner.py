"""Orchestrates a full trigger-event signal scan for one lead.

Checks three things:

* **Job posting** -- has the lead's careers/jobs page changed? Free, no
  Places involvement (app.services.job_signals).
* **Business status change** -- has the lead's Google Business Profile
  operational status changed? Requires a Place Details call.
* **Review count jump** -- has the lead's Google review count grown by at
  least one bucket? Requires the same Place Details call as above.

Rep-triggered per lead, mirroring the website audit agent's on-demand
pattern (app/agents/auditor.py: "Audits are rep-triggered per lead only --
no bulk/automatic path"). Nothing here runs on a schedule -- there is no
Celery beat wiring anywhere in this codebase yet (same known gap as the
Places coordinate-retention sweeper, app/tasks/places_retention.py).

COMPLIANCE NOTE, read before touching this file: the business-status and
review-count checks derive from Google Places data, which
app/services/places_policy.py forbids persisting verbatim. This module never
writes a raw status string, rating, or review count anywhere -- not to
LeadSignal.detail, not to a log line, not to an API response. Only the fact
that something changed (and, for reviews, that it grew) is ever surfaced.
See app/services/signal_detection.py for the hash-only comparison mechanism
this relies on.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.place import PlaceCandidate
from app.db.models.signal import LeadSignal, SignalCheckpoint, SignalType
from app.services import job_signals, signal_detection
from app.services.cost_guard import (
    PLACE_DETAILS_COST_PER_CALL_USD,
    BudgetExceededError,
    CostProvider,
)
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.places import (
    GooglePlacesClient,
    PlacesClient,
    PlacesError,
    PlacesTestModeLimitExceeded,
    enforce_places_test_mode_cap,
)

logger = logging.getLogger(__name__)

#: Minimum upward bucket movement in review count to count as a "jump" --
#: see app.services.signal_detection.compare_review_count.
REVIEW_JUMP_MIN_BUCKET_INCREASE = 1


@dataclass
class SignalScanOutcome:
    """What happened during one scan.

    Attributes:
        new_signals: Newly detected LeadSignal rows (already added to the
            session; caller flushes/commits).
        checked: Which signal types were actually evaluated this run.
        skipped: Signal types not evaluated, keyed by signal type value, with
            a short reason (e.g. "No website on file.").
    """

    new_signals: list[LeadSignal] = field(default_factory=list)
    checked: list[SignalType] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


async def _get_checkpoint(
    db: AsyncSession, lead_id: uuid.UUID, signal_type: SignalType
) -> SignalCheckpoint | None:
    """Fetch the current checkpoint for a lead+signal_type, if any.

    Args:
        db: Active database session.
        lead_id: The lead being scanned.
        signal_type: Which signal type's checkpoint to fetch.

    Returns:
        The checkpoint row, or None if this lead has never been checked for
        this signal type before.
    """
    result = await db.execute(
        select(SignalCheckpoint).where(
            SignalCheckpoint.lead_id == lead_id, SignalCheckpoint.signal_type == signal_type
        )
    )
    return result.scalar_one_or_none()


def _upsert_checkpoint(
    db: AsyncSession,
    lead_id: uuid.UUID,
    signal_type: SignalType,
    fingerprint_hash: str,
    now: datetime,
    existing: SignalCheckpoint | None,
) -> None:
    """Write the latest fingerprint as the checkpoint, creating it if needed.

    Always updates to the newest fingerprint regardless of whether a signal
    fired -- comparisons are always "since the last check," not against a
    historical peak, so every check (jump, no-op, or decrease) must move the
    checkpoint forward.

    Args:
        db: Active database session.
        lead_id: The lead being scanned.
        signal_type: Which signal type this checkpoint is for.
        fingerprint_hash: The newly computed fingerprint.
        now: Timestamp for this check.
        existing: The prior checkpoint row, if one exists.
    """
    if existing is not None:
        existing.fingerprint_hash = fingerprint_hash
        existing.last_checked_at = now
    else:
        db.add(
            SignalCheckpoint(
                lead_id=lead_id,
                signal_type=signal_type,
                fingerprint_hash=fingerprint_hash,
                last_checked_at=now,
            )
        )


async def _scan_job_posting(
    db: AsyncSession, lead: Lead, now: datetime, outcome: SignalScanOutcome
) -> None:
    """Check for a change on the lead's careers/jobs page.

    Args:
        db: Active database session.
        lead: The lead being scanned.
        now: Timestamp for this scan.
        outcome: Mutated in place with the result.
    """
    if not lead.website:
        outcome.skipped[SignalType.JOB_POSTING.value] = "No website on file."
        return

    outcome.checked.append(SignalType.JOB_POSTING)
    try:
        snapshot = await job_signals.check_careers_page(str(lead.website))
    except job_signals.JobSignalError:
        logger.warning("Job-posting scan could not reach the site", extra={"lead_id": str(lead.id)})
        outcome.skipped[SignalType.JOB_POSTING.value] = "Website unreachable during scan."
        return

    if not snapshot.found:
        outcome.skipped[SignalType.JOB_POSTING.value] = "No careers/jobs page found."
        return

    checkpoint = await _get_checkpoint(db, lead.id, SignalType.JOB_POSTING)
    new_fingerprint = signal_detection.content_change_fingerprint(
        snapshot.text_excerpt or "", lead_id=str(lead.id), purpose=snapshot.url or "careers"
    )

    if checkpoint is not None and checkpoint.fingerprint_hash == new_fingerprint:
        _upsert_checkpoint(db, lead.id, SignalType.JOB_POSTING, new_fingerprint, now, checkpoint)
        return

    if checkpoint is None:
        # First time this lead's careers page has been checked -- this
        # establishes the baseline, not a detected change.
        _upsert_checkpoint(db, lead.id, SignalType.JOB_POSTING, new_fingerprint, now, None)
        logger.info(
            "Job-posting baseline recorded", extra={"lead_id": str(lead.id), "url": snapshot.url}
        )
        return

    detail = "New or changed content detected on the careers/jobs page."
    if snapshot.looks_like_job_posting:
        detail += " The page appears to be actively advertising open roles."
    detail += (
        " This is a content-change heuristic, not confirmation of a specific new "
        "posting -- verify by viewing the page."
    )

    signal = LeadSignal(
        lead_id=lead.id,
        signal_type=SignalType.JOB_POSTING,
        detected_at=now,
        detail=detail,
        source_reference=snapshot.url,
    )
    db.add(signal)
    outcome.new_signals.append(signal)
    _upsert_checkpoint(db, lead.id, SignalType.JOB_POSTING, new_fingerprint, now, checkpoint)
    logger.info("Job-posting signal detected", extra={"lead_id": str(lead.id), "url": snapshot.url})


async def _find_place_id(db: AsyncSession, lead: Lead) -> str | None:
    """Find the place_id of the PlaceCandidate this lead was promoted from, if any.

    Args:
        db: Active database session.
        lead: The lead being scanned.

    Returns:
        The place_id, or None if this lead has no linked Places candidate
        (e.g. it was created manually or from another source).
    """
    result = await db.execute(
        select(PlaceCandidate.place_id).where(PlaceCandidate.lead_id == lead.id)
    )
    return result.scalar_one_or_none()


async def _scan_places_signals(
    db: AsyncSession,
    lead: Lead,
    now: datetime,
    outcome: SignalScanOutcome,
    places_client: PlacesClient,
) -> None:
    """Check for a business-status change and a review-count jump.

    Both come from a single Place Details call, cost-guarded the same way
    Text Search is guarded in app.services.places.PlaceDiscoveryService --
    the daily dollar budget and the test-mode request cap both apply.

    Args:
        db: Active database session.
        lead: The lead being scanned.
        now: Timestamp for this scan.
        outcome: Mutated in place with the result.
        places_client: Places provider to query.
    """
    place_id = await _find_place_id(db, lead)
    if place_id is None:
        reason = "Lead has no linked Places candidate."
        outcome.skipped[SignalType.BUSINESS_STATUS_CHANGE.value] = reason
        outcome.skipped[SignalType.REVIEW_COUNT_JUMP.value] = reason
        return

    try:
        enforce_places_test_mode_cap()
        await enforce_budget_before_call(
            db,
            CostProvider.PLACES,
            settings.cost_guard_daily_budget_places_usd,
            estimated_cost_usd=PLACE_DETAILS_COST_PER_CALL_USD,
        )
        details = await places_client.get_place_details(place_id)
        await record_spend(
            db,
            CostProvider.PLACES,
            "places.get_place_details",
            PLACE_DETAILS_COST_PER_CALL_USD,
            daily_budget_usd=settings.cost_guard_daily_budget_places_usd,
        )
    except PlacesTestModeLimitExceeded:
        reason = "Places test-mode request cap reached."
        logger.warning(
            "Signal scan skipped Places-derived checks: test-mode cap reached",
            extra={"lead_id": str(lead.id)},
        )
        outcome.skipped[SignalType.BUSINESS_STATUS_CHANGE.value] = reason
        outcome.skipped[SignalType.REVIEW_COUNT_JUMP.value] = reason
        return
    except BudgetExceededError:
        reason = "Places daily budget exhausted."
        logger.warning(
            "Signal scan skipped Places-derived checks: daily budget exhausted",
            extra={"lead_id": str(lead.id)},
        )
        outcome.skipped[SignalType.BUSINESS_STATUS_CHANGE.value] = reason
        outcome.skipped[SignalType.REVIEW_COUNT_JUMP.value] = reason
        return
    except PlacesError:
        reason = "Place Details lookup failed."
        logger.warning(
            "Signal scan skipped Places-derived checks: provider error",
            extra={"lead_id": str(lead.id)},
        )
        outcome.skipped[SignalType.BUSINESS_STATUS_CHANGE.value] = reason
        outcome.skipped[SignalType.REVIEW_COUNT_JUMP.value] = reason
        return

    outcome.checked.append(SignalType.BUSINESS_STATUS_CHANGE)
    outcome.checked.append(SignalType.REVIEW_COUNT_JUMP)

    if details.business_status is not None:
        checkpoint = await _get_checkpoint(db, lead.id, SignalType.BUSINESS_STATUS_CHANGE)
        comparison, new_fingerprint = signal_detection.compare_business_status(
            details.business_status,
            lead_id=str(lead.id),
            previous_fingerprint=checkpoint.fingerprint_hash if checkpoint else None,
        )
        if not comparison.is_first_observation and comparison.changed:
            signal = LeadSignal(
                lead_id=lead.id,
                signal_type=SignalType.BUSINESS_STATUS_CHANGE,
                detected_at=now,
                detail=(
                    "Google Business Profile operational status changed since the last "
                    "check. Verify the current status directly on Google Maps before "
                    "acting -- the specific before/after values are not stored here "
                    "(see app/services/places_policy.py)."
                ),
                source_reference=f"place_id={place_id}",
            )
            db.add(signal)
            outcome.new_signals.append(signal)
            logger.info("Business-status-change signal detected", extra={"lead_id": str(lead.id)})
        _upsert_checkpoint(
            db, lead.id, SignalType.BUSINESS_STATUS_CHANGE, new_fingerprint, now, checkpoint
        )

    if details.review_count is not None:
        checkpoint = await _get_checkpoint(db, lead.id, SignalType.REVIEW_COUNT_JUMP)
        comparison, new_fingerprint = signal_detection.compare_review_count(
            details.review_count,
            lead_id=str(lead.id),
            previous_fingerprint=checkpoint.fingerprint_hash if checkpoint else None,
            min_bucket_increase=REVIEW_JUMP_MIN_BUCKET_INCREASE,
        )
        if not comparison.is_first_observation and comparison.is_jump:
            signal = LeadSignal(
                lead_id=lead.id,
                signal_type=SignalType.REVIEW_COUNT_JUMP,
                detected_at=now,
                detail=(
                    f"Review volume increased by at least "
                    f"{signal_detection.REVIEW_COUNT_BUCKET_SIZE} reviews since the last "
                    "check. Verify the current count directly on Google Maps -- the "
                    "specific before/after counts are not stored here (see "
                    "app/services/places_policy.py)."
                ),
                source_reference=f"place_id={place_id}",
            )
            db.add(signal)
            outcome.new_signals.append(signal)
            logger.info("Review-count-jump signal detected", extra={"lead_id": str(lead.id)})
        _upsert_checkpoint(
            db, lead.id, SignalType.REVIEW_COUNT_JUMP, new_fingerprint, now, checkpoint
        )


async def scan_lead_for_signals(
    db: AsyncSession, lead: Lead, *, places_client: PlacesClient | None = None
) -> SignalScanOutcome:
    """Run a full signal scan for one lead.

    Args:
        db: Active database session. The caller is responsible for
            committing (this function only flushes, per the convention used
            by the rest of this codebase's services).
        lead: The lead to scan.
        places_client: Injectable for tests; defaults to a real
            :class:`~app.services.places.GooglePlacesClient`.

    Returns:
        A summary of what was checked, skipped, and newly detected.
    """
    now = utcnow()
    outcome = SignalScanOutcome()

    await _scan_job_posting(db, lead, now, outcome)
    await _scan_places_signals(db, lead, now, outcome, places_client or GooglePlacesClient())

    await db.flush()
    logger.info(
        "Signal scan complete",
        extra={
            "lead_id": str(lead.id),
            "new_signals": len(outcome.new_signals),
            "checked": [item.value for item in outcome.checked],
            "skipped": list(outcome.skipped),
        },
    )
    return outcome
