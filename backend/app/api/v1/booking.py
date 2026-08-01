"""Calendar booking routes.

Two public, token-verified routes (no login, reached from the link a
booking-link draft sends -- see app.services.booking_link_scanner) that let
a prospect see available slots and confirm one, plus one authenticated
sub-route so staff can see what's been booked on a lead.

Confirming a booking creates a real event on the one shared sales calendar
and sends Google's own invite email to the prospect (``sendUpdates=all`` --
see app.services.google_calendar.GoogleCalendarClient.create_event). This
is NOT subject to AGENTS.md section 8's human-approval-before-send gate:
that gate governs agent-generated outreach content (drafts an LLM writes,
which a human must review before anything goes out), not a transactional
calendar invite triggered by the prospect's own affirmative action on a
token-scoped confirmation endpoint they reached themselves. Nothing here
drafts or sends marketing content -- the only thing generated is a
calendar event whose time, attendee, and existence the prospect just chose.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.models.meeting import Meeting, MeetingStatus
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.booking import (
    BookingSlotResponse,
    BookingSlotsResponse,
    ConfirmBookingRequest,
    ConfirmBookingResponse,
    MeetingResponse,
)
from app.services.booking_slots import SlotComputationConfig, compute_available_slots
from app.services.booking_token import InvalidBookingTokenError, verify_booking_token
from app.services.google_calendar import (
    GoogleCalendarClient,
    GoogleCalendarError,
    GoogleCalendarNotConfiguredError,
)
from app.services.pipeline_transitions import advance_on_meeting_booked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/booking", tags=["booking"])
#: Separate router for the one authenticated, lead-scoped read -- mounted
#: under /leads alongside app.api.v1.pipeline and app.api.v1.signals, which
#: use the same prefix split for their own lead sub-resources.
leads_router = APIRouter(prefix="/leads", tags=["booking"])

#: Deliberately simple: only used to reject an obviously-malformed
#: attendee_email before it reaches Google's API (which would reject it
#: anyway, just with a less friendly error). Not CAN-SPAM validation --
#: this is a transactional calendar invite, not a commercial email, so
#: app.services.canspam does not apply here.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_INVALID_LINK_DETAIL = (
    "This booking link is invalid or has expired. Please ask for a new one."
)
_CALENDAR_UNAVAILABLE_DETAIL = (
    "Booking isn't available right now. Please try again shortly or contact us directly."
)


def _slot_config() -> SlotComputationConfig:
    """Build the slot-computation config from the current settings.

    Returns:
        The configuration :func:`app.services.booking_slots.compute_available_slots`
        needs, mirrored from the ``booking_*`` settings.
    """
    return SlotComputationConfig(
        timezone_name=settings.booking_timezone,
        slot_duration_minutes=settings.booking_slot_duration_minutes,
        working_hour_start=settings.booking_working_hour_start,
        working_hour_end=settings.booking_working_hour_end,
        lookahead_days=settings.booking_lookahead_days,
        min_lead_time_minutes=settings.booking_min_lead_time_minutes,
    )


async def _verify_token_or_400(token: str) -> tuple[str, str | None, datetime]:
    """Verify a booking token, raising the same 400 the UI shows either way.

    Args:
        token: The token from the booking link.

    Returns:
        A ``(lead_id, triggering_message_id, expires_at)`` tuple.

    Raises:
        HTTPException: 400 if the token is malformed, forged, or expired.
            Deliberately not distinguished from "lead not found" below --
            neither should tell an attacker which case they hit.
    """
    try:
        payload = verify_booking_token(token, settings.secret_key)
    except InvalidBookingTokenError:
        logger.info("Invalid or expired booking token presented")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_LINK_DETAIL
        ) from None
    return payload.lead_id, payload.triggering_message_id, payload.expires_at


async def _get_lead_or_400(db: AsyncSession, lead_id: str) -> Lead:
    """Fetch the lead a verified token names, or reject the link.

    Args:
        db: Active database session.
        lead_id: The lead id decoded from the token.

    Returns:
        The lead.

    Raises:
        HTTPException: 400 if the lead no longer exists (e.g. deleted since
            the link was issued) -- same generic message as an invalid
            token, so this never confirms or denies a specific lead id to
            an anonymous caller.
    """
    try:
        lead = await db.get(Lead, uuid.UUID(lead_id))
    except ValueError:
        lead = None
    if lead is None:
        logger.info("Booking token named a lead that no longer exists")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_LINK_DETAIL
        )
    return lead


@router.get(
    "/{token}/slots",
    response_model=BookingSlotsResponse,
    summary="Get available booking slots for a booking link",
    description=(
        "Public, unauthenticated -- reached from the booking link a booking-link "
        "draft sends. Verifies the signed, expiring token, then checks the shared "
        "sales calendar's real availability."
    ),
)
async def get_booking_slots(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> BookingSlotsResponse:
    """Compute and return available slots for a booking link.

    Args:
        token: The signed booking token from the link.
        db: Active database session.

    Returns:
        The lead's display name, available slots, and when this link itself
        expires.

    Raises:
        HTTPException: 400 if the token is invalid/expired or names a lead
            that no longer exists; 503 if the shared calendar isn't
            configured or couldn't be reached.
    """
    lead_id, _triggering_message_id, expires_at = await _verify_token_or_400(token)
    lead = await _get_lead_or_400(db, lead_id)

    client = GoogleCalendarClient()
    now = datetime.now(timezone.utc)
    try:
        busy = await client.get_busy_intervals(
            now, now + timedelta(days=settings.booking_lookahead_days)
        )
    except GoogleCalendarNotConfiguredError:
        logger.error("Booking slots requested but the shared calendar is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CALENDAR_UNAVAILABLE_DETAIL,
        ) from None
    except GoogleCalendarError:
        logger.exception("Could not fetch shared calendar availability")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CALENDAR_UNAVAILABLE_DETAIL,
        ) from None

    slots = compute_available_slots(_slot_config(), busy, now=now)
    logger.info(
        "Booking slots served",
        extra={"lead_id": str(lead.id), "slot_count": len(slots)},
    )
    return BookingSlotsResponse(
        lead_name=lead.name,
        slots=[BookingSlotResponse(start=s.start, end=s.end) for s in slots],
        link_expires_at=expires_at,
    )


@router.post(
    "/{token}/confirm",
    response_model=ConfirmBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm a booking slot",
    description=(
        "Public, unauthenticated. Re-checks the requested slot is still genuinely "
        "free (defends against two prospects racing for the same slot, or a "
        "stale/tampered request naming a slot outside working hours or lead time), "
        "then creates the calendar event, records the Meeting, and advances the "
        "lead's pipeline stage to Meeting Booked."
    ),
)
async def confirm_booking(
    token: str,
    body: ConfirmBookingRequest,
    db: AsyncSession = Depends(get_db),
) -> ConfirmBookingResponse:
    """Confirm a slot, book it on the shared calendar, and record the meeting.

    Args:
        token: The signed booking token from the link.
        body: The chosen slot and the prospect's contact email.
        db: Active database session.

    Returns:
        The booked meeting's id, confirmed time, and calendar link.

    Raises:
        HTTPException: 400 for an invalid token, missing lead, or malformed
            attendee email; 409 if the requested slot is no longer
            available; 503 if the shared calendar isn't configured or
            couldn't be reached.
    """
    if not _EMAIL_PATTERN.match(body.attendee_email.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="attendee_email is not a valid email address.",
        )
    if body.slot_start.tzinfo is None or body.slot_end.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="slot_start and slot_end must include a timezone offset.",
        )
    if body.slot_start >= body.slot_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="slot_start must be before slot_end.",
        )

    lead_id, triggering_message_id, _expires_at = await _verify_token_or_400(token)
    lead = await _get_lead_or_400(db, lead_id)

    client = GoogleCalendarClient()
    now = datetime.now(timezone.utc)
    try:
        busy = await client.get_busy_intervals(
            now, now + timedelta(days=settings.booking_lookahead_days)
        )
    except GoogleCalendarNotConfiguredError:
        logger.error("Booking confirm attempted but the shared calendar is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CALENDAR_UNAVAILABLE_DETAIL,
        ) from None
    except GoogleCalendarError:
        logger.exception("Could not re-check shared calendar availability before confirming")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CALENDAR_UNAVAILABLE_DETAIL,
        ) from None

    # Re-derive the available slots exactly as GET /slots would right now,
    # then require the requested slot to be one of them -- this is both the
    # race defense (someone else booked it in the meantime) and the
    # tamper defense (a crafted request naming a time outside working
    # hours, past the lead-time cutoff, or on a weekend, none of which
    # compute_available_slots would ever have offered in the first place).
    available = compute_available_slots(_slot_config(), busy, now=now)
    if not any(s.start == body.slot_start and s.end == body.slot_end for s in available):
        logger.info(
            "Requested booking slot is no longer available",
            extra={"lead_id": str(lead.id), "slot_start": body.slot_start.isoformat()},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That time is no longer available. Please go back and pick another.",
        )

    try:
        event = await client.create_event(
            summary=f"Call with {lead.name}",
            description=(
                f"Booked via the Everen Techno scheduling link by {body.attendee_email}."
            ),
            start=body.slot_start,
            end=body.slot_end,
            attendee_email=body.attendee_email,
        )
    except (GoogleCalendarNotConfiguredError, GoogleCalendarError):
        logger.exception("Failed to create the calendar event for a confirmed booking")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CALENDAR_UNAVAILABLE_DETAIL,
        ) from None

    triggering_uuid = uuid.UUID(triggering_message_id) if triggering_message_id else None
    meeting = Meeting(
        lead_id=lead.id,
        triggering_message_id=triggering_uuid,
        scheduled_start=body.slot_start,
        scheduled_end=body.slot_end,
        attendee_email=body.attendee_email,
        calendar_event_id=event.event_id,
        calendar_event_link=event.html_link or None,
        status=MeetingStatus.BOOKED,
        booked_at=utcnow(),
    )
    db.add(meeting)
    await db.flush()

    await advance_on_meeting_booked(db, lead, inbound_message_id=triggering_uuid)

    logger.info(
        "Meeting booked",
        extra={
            "lead_id": str(lead.id),
            "meeting_id": str(meeting.id),
            "calendar_event_id": event.event_id,
        },
    )
    return ConfirmBookingResponse(
        meeting_id=meeting.id,
        scheduled_start=meeting.scheduled_start,
        scheduled_end=meeting.scheduled_end,
        calendar_event_link=meeting.calendar_event_link,
        message="Your call is booked. You'll receive a calendar invite by email shortly.",
    )


async def _get_lead_or_404(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    """Fetch a lead or raise 404, for the authenticated meetings route.

    Args:
        db: Active database session.
        lead_id: The lead to fetch.

    Returns:
        The lead.

    Raises:
        HTTPException: 404 if it does not exist.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


@leads_router.get(
    "/{lead_id}/meetings",
    response_model=list[MeetingResponse],
    summary="List a lead's booked meetings",
)
async def list_meetings(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MeetingResponse]:
    """List every meeting booked for a lead, most recent first.

    Args:
        lead_id: The lead to look up.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The lead's meetings, most recently scheduled first.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    await _get_lead_or_404(db, lead_id)
    meetings = (
        (
            await db.execute(
                select(Meeting)
                .where(Meeting.lead_id == lead_id)
                .order_by(Meeting.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    logger.info(
        "Meetings listed",
        extra={
            "lead_id": str(lead_id),
            "count": len(meetings),
            "user_id": str(user.id),
        },
    )
    return [MeetingResponse.model_validate(m) for m in meetings]
