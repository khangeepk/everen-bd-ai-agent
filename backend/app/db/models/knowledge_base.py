"""Services Knowledge Base ORM models.

Stores Everen Techno's service catalogue, pricing ranges, and portfolio
evidence. Long-form text is split into :class:`KnowledgeChunk` rows, each
carrying a pgvector embedding used for RAG-based service recommendation.

See AGENTS.md section 9.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EmbeddingVector


class PricingModel(str, enum.Enum):
    """How a service is billed."""

    FIXED = "fixed"
    HOURLY = "hourly"
    MONTHLY_RETAINER = "monthly_retainer"
    PROJECT_RANGE = "project_range"
    CUSTOM = "custom"


class ChunkSourceType(str, enum.Enum):
    """Which record a knowledge chunk was derived from."""

    SERVICE = "service"
    PORTFOLIO = "portfolio"


class Service(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A service Everen Techno offers to clients.

    Pricing is stored as an inclusive range in minor-unit-safe ``Numeric``
    columns. ``price_min`` and ``price_max`` may both be null for services
    quoted entirely case-by-case.
    """

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_services_slug"),
        CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_services_price_range_ordered",
        ),
        CheckConstraint("price_min IS NULL OR price_min >= 0", name="ck_services_price_min_nonneg"),
        Index("ix_services_category_active", "category", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    pricing_model: Mapped[PricingModel] = mapped_column(
        SAEnum(PricingModel, name="pricing_model"),
        nullable=False,
        default=PricingModel.PROJECT_RANGE,
    )
    typical_duration_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    portfolio_items: Mapped[list["PortfolioItem"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )

    def price_range_label(self) -> str:
        """Render the pricing range as a human-readable string.

        Returns:
            A label such as ``"USD 5,000 - 15,000"``, ``"From USD 5,000"``, or
            ``"Contact for pricing"`` when no bounds are set.
        """
        curr = self.currency or "USD"
        if self.price_min is None and self.price_max is None:
            return "Contact for pricing"
        if self.price_max is None:
            return f"From {curr} {self.price_min:,.0f}"
        if self.price_min is None:
            return f"Up to {curr} {self.price_max:,.0f}"
        return f"{curr} {self.price_min:,.0f} - {self.price_max:,.0f}"


class PortfolioItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A delivered project used as social proof for a service."""

    __tablename__ = "portfolio_items"
    __table_args__ = (
        Index("ix_portfolio_items_service_id", "service_id"),
    )

    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(nullable=False, default=True)

    service: Mapped["Service | None"] = relationship(back_populates="portfolio_items")


class KnowledgeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An embedded text fragment retrievable by vector similarity.

    One row per chunk of a service description or portfolio write-up. The
    ``embedding`` column is a pgvector column indexed with HNSW for cosine
    distance (AGENTS.md section 9.1).
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "chunk_index", name="uq_knowledge_chunks_source_index"
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index_nonneg"),
        Index("ix_knowledge_chunks_source", "source_type", "source_id"),
    )

    source_type: Mapped[ChunkSourceType] = mapped_column(
        SAEnum(ChunkSourceType, name="chunk_source_type"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(
        EmbeddingVector(settings.embedding_dimension), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
