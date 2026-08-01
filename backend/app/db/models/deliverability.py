"""Deliverability check ORM model.

One row per run of the SPF/DKIM/DMARC checker
(`app/services/deliverability_checker.py`). Insert-only, like
`WebsiteAudit` -- a check is a fact about what DNS returned at a point in
time, not something later edited, and keeping history lets a rep see when a
record changed (or when it started/stopped passing).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.services.deliverability import CheckStatus


class DeliverabilityCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One SPF/DKIM/DMARC check run against a sending domain."""

    __tablename__ = "deliverability_checks"
    __table_args__ = (
        Index("ix_deliverability_checks_domain_created", "domain", "created_at"),
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)

    spf_status: Mapped[CheckStatus] = mapped_column(
        SAEnum(CheckStatus, name="deliverability_check_status"), nullable=False
    )
    spf_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    spf_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    dmarc_status: Mapped[CheckStatus] = mapped_column(
        SAEnum(CheckStatus, name="deliverability_check_status"), nullable=False
    )
    dmarc_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    dmarc_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    dkim_status: Mapped[CheckStatus] = mapped_column(
        SAEnum(CheckStatus, name="deliverability_check_status"), nullable=False
    )
    #: Comma-separated selectors this run tried (see
    #: settings.sendgrid_dkim_selectors) -- kept on the row so a stale
    #: report is self-explanatory about what it did and didn't check.
    dkim_selectors_checked: Mapped[str] = mapped_column(String(500), nullable=False)
    dkim_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    overall_status: Mapped[CheckStatus] = mapped_column(
        SAEnum(CheckStatus, name="deliverability_check_status"), nullable=False
    )
    checked_by_agent: Mapped[str] = mapped_column(String(100), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
