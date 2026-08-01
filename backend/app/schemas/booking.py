"""Pydantic v2 schemas for the public calendar-booking flow and its
authenticated meetings-list sibling.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.meeting import MeetingStatus


class BookingSlotResponse(BaseModel):
    """One offerable meeting slot."""

    start: datetime
    end: datetime


class BookingSlotsResponse(BaseModel):
    """Available slots for a booking link, plus context for the page."""

    lead_name: str = Field(
        description="The business name, shown so the prospect confirms this is the right link."
    )
    slots: list[BookingSlotResponse]
    link_expires_at: datetime = Field(
        description="When this booking link itself stops working, independent of any "
        "individual slot's own timing."
    )


class ConfirmBookingRequest(BaseModel):
    """Request to confirm a specific slot from a booking link."""

    slot_start: datetime = Field(
        description="Must exactly match one of the slots GET /booking/{token}/slots "
        "returned -- see that route's response."
    )
    slot_end: datetime
    attendee_email: str = Field(
        min_length=3,
        max_length=320,
        description="Where the calendar invite is sent. Does not need to match the lead's "
        "on-file contact address -- the prospect may be replying from a different one.",
    )


class ConfirmBookingResponse(BaseModel):
    """Outcome of confirming a booking."""

    meeting_id: uuid.UUID
    scheduled_start: datetime
    scheduled_end: datetime
    calendar_event_link: str | None
    message: str


class MeetingResponse(BaseModel):
    """A booked meeting, for the authenticated lead-detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    triggering_message_id: uuid.UUID | None
    scheduled_start: datetime
    scheduled_end: datetime
    attendee_email: str
    calendar_event_id: str
    calendar_event_link: str | None
    status: MeetingStatus
    booked_at: datetime
