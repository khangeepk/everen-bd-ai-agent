"""Analytics ORM models: prompt version log, email open tracking.

`PromptVersion` is the "old vs new prompt" log: every system prompt an agent
has ever used for a channel is a row here, with `is_active` marking which
one(s) are currently live. Two active rows sharing an `experiment_group` are
an A/B test -- see `app/services/ab_testing.py` for how a draft is bucketed
between them, and `app/agents/outreach.py` for where that happens.

`EmailOpenEvent` backs the dashboard's open rate. Logged by a tracking pixel
(see `app/api/v1/outreach.py`'s `/track/open/{draft_id}.gif` route) -- a
privacy-relevant "tracking technology" under EU ePrivacy rules, appropriate
here as internal B2B outreach analytics on our own sent mail, but worth
keeping in mind if this is ever extended to track links or content beyond a
simple open signal.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.services.outreach_policy import OutreachChannel


class PromptVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of an agent's system prompt for a given channel."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        Index("ix_prompt_versions_agent_channel_active", "agent_name", "channel", "is_active"),
        Index("ix_prompt_versions_experiment_group", "experiment_group"),
    )

    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[OutreachChannel | None] = mapped_column(
        SAEnum(OutreachChannel, name="outreach_channel"), nullable=True
    )
    #: Human label, e.g. "v1", "v2-shorter-subject". Not required to be
    #: unique -- a rep might reuse a label across unrelated experiments.
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Two active rows sharing this value are treated as an A/B test. Null
    #: means "just the live prompt for this agent+channel," not part of a
    #: deliberate split.
    experiment_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Free-text performance tags, e.g. 'higher reply rate, promoted 2026-08-01'.",
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class EmailOpenEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One recorded open of a sent outreach email.

    Insert-only. A single email can be opened multiple times (forwarded,
    re-read); each open is its own row so both "was this ever opened" and
    "how many times" are answerable.
    """

    __tablename__ = "email_open_events"
    __table_args__ = (Index("ix_email_open_events_draft_opened", "draft_id", "opened_at"),)

    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outreach_drafts.id", ondelete="CASCADE"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Hashed, not raw -- avoids storing a directly identifying IP address
    #: while still letting us dedup repeat opens from the same client if
    #: needed later.
    client_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
