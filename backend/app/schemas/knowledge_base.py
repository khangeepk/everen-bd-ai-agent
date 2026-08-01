"""Pydantic v2 schemas for the Services Knowledge Base API."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.knowledge_base import PricingModel


class ServiceBase(BaseModel):
    """Fields shared by service create and read schemas."""

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=200, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: str = Field(min_length=2, max_length=100)
    summary: str = Field(min_length=10, max_length=500)
    description: str = Field(min_length=20)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    pricing_model: PricingModel = PricingModel.PROJECT_RANGE
    typical_duration_weeks: int | None = Field(default=None, ge=1, le=520)

    @model_validator(mode="after")
    def _check_price_range(self) -> "ServiceBase":
        """Ensure the pricing range is ordered.

        Returns:
            The validated model.

        Raises:
            ValueError: If ``price_min`` exceeds ``price_max``.
        """
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                raise ValueError("price_min must not exceed price_max")
        return self


class ServiceCreate(ServiceBase):
    """Request body for creating a service."""

    is_active: bool = True


class ServiceResponse(ServiceBase):
    """A service as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool


class PortfolioItemCreate(BaseModel):
    """Request body for creating a portfolio item."""

    service_id: uuid.UUID | None = None
    client_name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=20)
    outcome: str | None = None
    is_public: bool = True


class PortfolioItemResponse(PortfolioItemCreate):
    """A portfolio item as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class RecommendationRequest(BaseModel):
    """A natural-language description of what a prospect needs."""

    query: str = Field(min_length=3, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.15, ge=-1.0, le=1.0)


class RecommendedService(BaseModel):
    """One recommended service with its supporting evidence."""

    service: ServiceResponse
    score: float = Field(ge=-1.0, le=1.0)
    rationale: str
    supporting_excerpts: list[str] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    """The full result of a recommendation query."""

    query: str
    recommendations: list[RecommendedService]
    retrieved_chunk_count: int


class ReindexResponse(BaseModel):
    """Summary of a knowledge-base rebuild."""

    services: int
    portfolio_items: int
    chunks: int
