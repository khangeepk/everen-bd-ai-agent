"""Pydantic v2 schemas for lead scoring."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.lead_scoring import ScoreLabel


class ComponentScoreResponse(BaseModel):
    """One weighted component of the total score."""

    value: float = Field(ge=0.0, le=1.0)
    reasons: list[str]


class LeadScoreResponse(BaseModel):
    """A computed lead score with its full breakdown."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    lead_id: uuid.UUID
    need: ComponentScoreResponse
    fit: ComponentScoreResponse
    contactability: ComponentScoreResponse
    revenue: ComponentScoreResponse
    compliance: ComponentScoreResponse
    gate_triggered: bool
    gate_reasons: list[str]
    total_score: float = Field(ge=0.0, le=1.0)
    label: ScoreLabel
    formula_version: str
    computed_at: datetime


class PaginatedLeadScores(BaseModel):
    """A page of historical scores for a lead, newest first."""

    items: list[LeadScoreResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
