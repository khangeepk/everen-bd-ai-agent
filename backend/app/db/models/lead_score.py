"""Lead score ORM model.

Stores every computed score as a new row rather than updating one row in
place, so the scoring history for a lead is auditable -- useful both for BD
reporting ("this lead went from Warm to Hot after the audit") and for
compliance review ("why did this lead show as Hot before it was suppressed").

See app/services/lead_scoring.py for the formula and gate this table records
the output of.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.services.lead_scoring import ScoreLabel


class LeadScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One computed score for one lead at one point in time."""

    __tablename__ = "lead_scores"
    __table_args__ = (
        CheckConstraint(
            "need_score >= 0.0 AND need_score <= 1.0 "
            "AND fit_score >= 0.0 AND fit_score <= 1.0 "
            "AND contactability_score >= 0.0 AND contactability_score <= 1.0 "
            "AND revenue_score >= 0.0 AND revenue_score <= 1.0 "
            "AND compliance_score >= 0.0 AND compliance_score <= 1.0 "
            "AND total_score >= 0.0 AND total_score <= 1.0",
            name="ck_lead_scores_component_ranges",
        ),
        Index("ix_lead_scores_lead_computed", "lead_id", "computed_at"),
        Index("ix_lead_scores_label", "label"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )

    # Component scores, each in [0.0, 1.0]. Higher is always better -- for
    # compliance_score this means lower risk.
    need_score: Mapped[float] = mapped_column(Float, nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, nullable=False)
    contactability_score: Mapped[float] = mapped_column(Float, nullable=False)
    revenue_score: Mapped[float] = mapped_column(Float, nullable=False)
    compliance_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Evidence, newline-joined, for display next to each component.
    need_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    contactability_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    revenue_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)

    gate_triggered: Mapped[bool] = mapped_column(nullable=False, default=False)
    gate_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[ScoreLabel] = mapped_column(
        SAEnum(ScoreLabel, name="lead_score_label"), nullable=False
    )
    formula_version: Mapped[str] = mapped_column(String(20), nullable=False)

    computed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
