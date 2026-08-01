"""API cost tracking: one row per billed external call.

Backs the daily budget guard in app/services/cost_guard.py (pure formula) and
app/services/cost_tracking.py (the DB-aware spend ledger built on this
table).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CostProvider(str, enum.Enum):
    """An external, billed API this system calls. Mirrors app.services.cost_guard.CostProvider."""

    PLACES = "places"
    OPENAI = "openai"


class ApiCostEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One billed external API call.

    Insert-only, like PipelineEvent -- this is a ledger, not a mutable
    counter, so the daily total is always a straightforward SUM and the
    individual events remain available for later cost-attribution analysis
    (which agent/endpoint is actually driving spend).
    """

    __tablename__ = "api_cost_events"
    __table_args__ = (
        Index("ix_api_cost_events_provider_occurred", "provider", "occurred_at"),
    )

    provider: Mapped[CostProvider] = mapped_column(
        SAEnum(CostProvider, name="cost_provider"), nullable=False
    )
    #: Free-text call site, e.g. "places.discover", "outreach.draft_email",
    #: "auditor.generate_report" -- for attributing spend to a feature, not
    #: for machine parsing.
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
