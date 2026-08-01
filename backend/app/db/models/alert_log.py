"""AlertLog ORM model.

Records every deliverability threshold breach detected by the n8n
SendGrid health-monitor workflow. One row is written per webhook call —
i.e. per (domain, alert_type) event — regardless of how many drafts
were paused in that call.

The ``resolved_at`` column is NULL until a human operator marks the
alert resolved; the ``drafts_paused_count`` records how many
``OutreachDraft`` rows were transitioned to ``PAUSED`` at trigger time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, utcnow


class AlertLog(UUIDPrimaryKeyMixin, Base):
    """A single deliverability alert event logged by the health-monitor.

    Attributes:
        id: UUID primary key.
        alert_type: Machine-readable alert category. One of
            ``"bounce_rate_exceeded"``, ``"spam_rate_exceeded"``, or
            ``"open_rate_drop"``.
        domain: The sending domain that triggered the alert,
            e.g. ``"mail.example.com"``.
        metric_value: The computed metric value at trigger time (a ratio
            0–1 for rates, or a percentage-point delta for open-rate trend).
        threshold_value: The configured threshold that was crossed.
        triggered_at: UTC timestamp of when the alert was recorded.
        resolved_at: NULL until a human explicitly resolves the alert via
            the admin UI or API. Not set automatically.
        drafts_paused_count: Number of ``OutreachDraft`` rows that were
            transitioned to ``PAUSED`` as a result of this alert.
    """

    __tablename__ = "alert_log"

    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    drafts_paused_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    __table_args__ = (
        # Fast lookup: "show me all alerts for domain X, newest first"
        Index("ix_alert_log_domain_triggered_at", "domain", "triggered_at"),
        # Fast lookup: "show me all unresolved bounce-rate alerts"
        Index("ix_alert_log_type_triggered_at", "alert_type", "triggered_at"),
    )
