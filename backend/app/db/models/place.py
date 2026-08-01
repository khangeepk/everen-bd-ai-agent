"""Places discovery staging tables.

COMPLIANCE NOTICE -- read before adding a column here.

These tables hold Google Places output. Under the Google Maps Platform Service
Specific Terms section 10.3, the only Places fields this system may persist are:

* ``place_id``            -- indefinitely (exempt from caching restrictions)
* ``latitude``/``longitude`` -- at most 30 consecutive calendar days

Business name, formatted address, phone number, website, rating, and types are
Google Maps Content and MUST NOT be added as columns. Those values may be
fetched and returned to a caller for immediate display, but never written here.

Dedup therefore keys on ``place_id`` rather than name+address. That is also the
better key: it is stable across a business renaming or Google reformatting its
address string.

See :mod:`app.services.places_policy` for the enforced allowlist.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CandidateStatus(str, enum.Enum):
    """Lifecycle of a discovered place candidate."""

    NEW = "new"
    REVIEWED = "reviewed"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class PlaceSearch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An executed location+industry search.

    Every field here is the caller's own search input, not Google Maps Content,
    so all of it is freely storable. This row is the provenance record that
    gives each candidate its ``source`` and timestamp.
    """

    __tablename__ = "place_searches"
    __table_args__ = (
        CheckConstraint("radius_meters > 0", name="ck_place_searches_radius_positive"),
        CheckConstraint("result_count >= 0", name="ck_place_searches_result_count_nonneg"),
        Index("ix_place_searches_fingerprint_executed", "fingerprint", "executed_at"),
    )

    industry: Mapped[str] = mapped_column(String(200), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    radius_meters: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="google_places")

    executed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    candidates: Mapped[list["PlaceCandidate"]] = relationship(back_populates="search")


class PlaceCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A discovered place, stored under Places retention limits.

    Deliberately carries no business name, address, phone, or website. To show
    those to a user, re-fetch them live from Places Details using ``place_id``.
    """

    __tablename__ = "place_candidates"
    __table_args__ = (
        UniqueConstraint("place_id", name="uq_place_candidates_place_id"),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_place_candidates_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_place_candidates_longitude_range",
        ),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_place_candidates_confidence_range",
        ),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_place_candidates_coordinates_paired",
        ),
        Index("ix_place_candidates_status_discovered", "status", "discovered_at"),
        Index("ix_place_candidates_coordinates_expire", "coordinates_expire_at"),
    )

    #: Google's stable identifier. Exempt from caching restrictions, so this is
    #: the one Places field safe to keep forever -- and the dedup key.
    place_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    #: Retained for at most 30 days, then nulled by the expiry sweeper.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    coordinates_expire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    search_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("place_searches.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="google_places")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[CandidateStatus] = mapped_column(
        SAEnum(CandidateStatus, name="candidate_status"),
        nullable=False,
        default=CandidateStatus.NEW,
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )

    search: Mapped["PlaceSearch | None"] = relationship(back_populates="candidates")

    def has_coordinates(self) -> bool:
        """Whether usable coordinates are currently held.

        Returns:
            True when both latitude and longitude are present.
        """
        return self.latitude is not None and self.longitude is not None

    def redact_coordinates(self) -> bool:
        """Delete cached coordinates to satisfy the 30-day retention limit.

        Idempotent -- calling it on an already-redacted row is a no-op.

        Returns:
            True if coordinates were present and have now been cleared.
        """
        if not self.has_coordinates():
            return False
        self.latitude = None
        self.longitude = None
        self.coordinates_expire_at = None
        return True
