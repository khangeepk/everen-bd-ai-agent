"""Email-enrichment attempt history.

Append-only, mirroring app.db.models.lead_score.LeadScore's "new row per
computation, never updated in place" pattern -- every candidate email the
fallback chain (app/services/email_enrichment.py, email_discovery.py,
email_enrichment_scanner.py) ever found for a lead is a row here, not just
the one that got applied to Lead.contact_email. Lets a rep see every
alternative the chain considered, not only the top pick.

``candidate_email`` is encrypted at rest (EncryptedString) -- a guessed or
scraped email is still a real person's email address, and gets the same
protection as Lead.contact_email rather than a lesser standard just because
it's unconfirmed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EncryptedString
from app.services.email_enrichment import EmailSource


class EmailEnrichmentAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One candidate email the enrichment chain found for a lead."""

    __tablename__ = "email_enrichment_attempts"
    __table_args__ = (
        Index("ix_email_enrichment_attempts_lead_detected", "lead_id", "detected_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[EmailSource] = mapped_column(
        SAEnum(EmailSource, name="email_source"), nullable=False
    )
    #: Encrypted at rest -- same treatment as Lead.contact_email. No blind
    #: index: nothing looks up an attempt by its candidate email.
    candidate_email: Mapped[str] = mapped_column(EncryptedString(320), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: True if this candidate was the one applied to Lead.contact_email.
    #: Exactly one attempt per lead should be True at a time in practice
    #: (the scanner only ever applies its single best pick), but this is not
    #: enforced by a DB constraint -- it's a display convenience, not a
    #: source of truth (Lead.contact_email/contact_email_source/
    #: contact_email_confidence are the source of truth for the lead's
    #: current working address).
    was_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
