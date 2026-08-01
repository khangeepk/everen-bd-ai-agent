"""Lead trigger-event signals: job postings, business status changes, review jumps.

Two tables, deliberately split the same way ``OutreachDraft.status`` (current
state) is split from ``OutreachAuditLog`` (append-only history):

* :class:`LeadSignal` is the append-only event log -- one new row every time a
  change is actually detected. Mirrors :class:`app.db.models.lead_score.LeadScore`'s
  "new row per computation, never updated in place" pattern, so a rep can see
  every trigger event that ever fired for a lead.
* :class:`SignalCheckpoint` is mutable, upserted-in-place current state: the
  fingerprint a scan compares the next check against. It is not history and is
  not meant to be read directly by a rep.

COMPLIANCE NOTE on ``fingerprint_hash`` -- read before adding a column here.

Two of the three signal types are derived from Google Places data
(business status, review count). Per ``app/services/places_policy.py``,
raw Places fields beyond ``place_id``/coordinates are Google Maps Content and
must never be persisted verbatim. ``SignalCheckpoint.fingerprint_hash`` is a
keyed HMAC-SHA256 (see ``app/services/signal_detection.py``) of a *bucketed,
per-lead-namespaced* derived value -- e.g. "which 10-review bucket" or "which
of 3 status enum values" -- never the literal rating, review count, or status
string. Do not add a column that stores those raw values here or on
:class:`LeadSignal`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SignalType(str, enum.Enum):
    """A kind of trigger event this system can detect for a lead."""

    #: New or changed content on a likely careers/jobs page on the lead's own
    #: website. Free to check (no Places involvement) -- see
    #: app/services/job_signals.py.
    JOB_POSTING = "job_posting"
    #: The lead's Google Business Profile operational status changed (e.g.
    #: operational -> temporarily closed). Requires a Place Details call.
    BUSINESS_STATUS_CHANGE = "business_status_change"
    #: The lead's Google review count moved up by at least one bucket since
    #: the last check. Requires a Place Details call. Decreases are tracked
    #: (checkpoint still updates) but do not themselves fire a signal --
    #: "jump" means growth, which is the BD-relevant trigger event.
    REVIEW_COUNT_JUMP = "review_count_jump"


class LeadSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One detected trigger event for a lead."""

    __tablename__ = "lead_signals"
    __table_args__ = (
        Index("ix_lead_signals_lead_detected", "lead_id", "detected_at"),
        Index("ix_lead_signals_type_detected", "signal_type", "detected_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[SignalType] = mapped_column(
        SAEnum(SignalType, name="lead_signal_type"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Human-readable, non-Google-Maps-Content description of what changed
    #: (e.g. "Review volume increased by roughly 1 bucket of 10" or "New
    #: content detected on a careers/jobs page"). Never contains a raw Places
    #: rating, review count, or business-status string -- see the module
    #: docstring and app/services/signal_detection.py.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which URL a job-posting signal was detected on, or which place_id a
    #: Places-derived signal came from -- for a rep to go verify by hand.
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Set once a rep has seen/acted on this signal. Used to decide whether it
    #: still boosts the lead in the outreach queue (app/api/v1/leads.py) --
    #: an old, already-actioned signal shouldn't keep a lead pinned at the top
    #: indefinitely.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SignalCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current-state fingerprint per lead+signal_type, for detecting the next change.

    Mutable and upserted in place -- this is current state, not an audit
    trail (:class:`LeadSignal` is the audit trail). One row per
    ``(lead_id, signal_type)``.
    """

    __tablename__ = "lead_signal_checkpoints"
    __table_args__ = (
        UniqueConstraint("lead_id", "signal_type", name="uq_signal_checkpoints_lead_type"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[SignalType] = mapped_column(
        SAEnum(SignalType, name="lead_signal_type"), nullable=False
    )
    #: Keyed HMAC-SHA256 of a bucketed/derived, per-lead-namespaced value.
    #: NEVER the raw Places value -- see the module docstring.
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
