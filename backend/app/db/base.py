"""SQLAlchemy declarative base and shared column mixins.

All tables use UUID primary keys and timezone-aware UTC timestamps per
AGENTS.md section 9.1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        The current UTC instant.
    """
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key column named ``id``."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` TIMESTAMPTZ columns in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
