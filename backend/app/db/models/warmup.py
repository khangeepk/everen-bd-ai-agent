"""Warmup schedule ORM model.

The persisted configuration a rep sets up (`WarmupSchedule`); the pure ramp
curve math it drives lives in `app.services.warmup.WarmupPlan` (a plain
dataclass built from one of these rows -- see
`app.services.warmup_tracker.get_active_warmup_plan`). Named "Schedule" here
and "Plan" there deliberately, so the two are never confused for one another
despite describing the same ramp.

Follows the same "only one row counts as current" convention already
established by `PromptVersion.is_active` (app/db/models/analytics.py):
multiple historical rows may exist per channel, but only the most recent
`is_active=True` one drives the live send limit.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Enum as SAEnum, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.services.outreach_policy import OutreachChannel


class WarmupSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A configured sending-domain warmup ramp for one channel."""

    __tablename__ = "warmup_schedules"
    __table_args__ = (
        CheckConstraint("start_volume >= 1", name="ck_warmup_schedules_start_volume_positive"),
        CheckConstraint(
            "target_daily_volume >= start_volume",
            name="ck_warmup_schedules_target_gte_start",
        ),
        CheckConstraint("ramp_days >= 1", name="ck_warmup_schedules_ramp_days_positive"),
        Index("ix_warmup_schedules_channel_active", "channel", "is_active"),
    )

    channel: Mapped[OutreachChannel] = mapped_column(
        SAEnum(OutreachChannel, name="outreach_channel"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_volume: Mapped[int] = mapped_column(Integer, nullable=False)
    target_daily_volume: Mapped[int] = mapped_column(Integer, nullable=False)
    ramp_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
