"""Booked-meeting ORM model.

Records a real, confirmed calendar booking made through the public booking
link (see app/api/v1/booking.py, app/services/google_calendar.py,
app/services/booking_token.py, app/services/booking_slots.py). Distinct
from :class:`app.db.models.pipeline.CallCenterCard` -- a Meeting is the
fact that a specific slot was booked on the shared sales calendar; a
CallCenterCard is a briefing generated for a rep once a lead reaches
:attr:`app.services.pipeline.PipelineStage.HOT`, which may or may not ever
result in a Meeting.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EncryptedString


class MeetingStatus(str, enum.Enum):
    """Status of a booked meeting.

    Only BOOKED and CANCELLED exist today -- there is no separate
    "completed" status because nothing in this codebase currently detects
    whether a booked call actually happened (that would need a calendar
    webhook or a rep action neither of which is built yet).
    """

    BOOKED = "booked"
    CANCELLED = "cancelled"


class Meeting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A meeting booked on the shared sales calendar via a booking link.

    Insert-mostly, like :class:`app.db.models.pipeline.PipelineEvent` --
    a booking is a fact of record. ``status`` is the one field expected to
    change in place (BOOKED -> CANCELLED), everything else is a snapshot
    of what was true at booking time.
    """

    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_lead_scheduled_start", "lead_id", "scheduled_start"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    #: The reply that triggered generation of this booking link, if known --
    #: see app.services.booking_link_scanner. Nullable because a booking
    #: link could in principle be (re)issued without a specific triggering
    #: reply, e.g. by a future manual "send booking link" action.
    triggering_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_messages.id", ondelete="SET NULL"), nullable=True
    )

    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Encrypted at rest, like Lead.contact_email (app/db/types.py). This is
    #: the address the calendar invite was actually sent to, which may
    #: differ from the lead's on-file contact_email if the prospect replied
    #: from a different address. No blind-index hash column -- nothing in
    #: this codebase looks a Meeting up by attendee email; add one the same
    #: way Lead.contact_email_hash was added if that changes.
    attendee_email: Mapped[str] = mapped_column(EncryptedString(320), nullable=False)

    #: Google Calendar's event id and viewable link, for cross-reference
    #: and manual lookup -- not shown to the prospect, who receives
    #: Google's own invite email (see GoogleCalendarClient.create_event).
    calendar_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_event_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[MeetingStatus] = mapped_column(
        SAEnum(MeetingStatus, name="meeting_status"),
        nullable=False,
        default=MeetingStatus.BOOKED,
    )
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
