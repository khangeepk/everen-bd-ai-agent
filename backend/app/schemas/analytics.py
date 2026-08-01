"""Pydantic v2 schemas for the analytics dashboard."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderBudgetResponse(BaseModel):
    """Today's spend standing for one cost-guarded provider."""

    provider: str
    daily_budget_usd: float
    spent_usd: float
    remaining_usd: float
    fraction_spent: float
    past_alert_threshold: bool
    exhausted: bool


class CostStatusResponse(BaseModel):
    """Today's API cost-guard standing across all guarded providers."""

    providers: list[ProviderBudgetResponse]


class OverviewResponse(BaseModel):
    """Top-line dashboard metrics for a date range.

    See `app/services/analytics.py` for exact metric definitions -- in
    particular, open_rate will read 0 until outreach email is sent as HTML
    (currently plain text), and reply_rate is a lead-level approximation,
    not per-draft attribution.
    """

    period_start: datetime | None
    period_end: datetime | None
    emails_sent: int
    opens: int
    open_rate: float = Field(ge=0.0)
    replies: int
    reply_rate: float = Field(ge=0.0)
    meetings_booked: int
    deals_won: int


class RankedItemResponse(BaseModel):
    """One entry in a top-N ranking."""

    label: str
    count: int


class TopIndustriesResponse(BaseModel):
    """Top industries by won deals."""

    period_start: datetime | None
    period_end: datetime | None
    items: list[RankedItemResponse]


class TopServicesResponse(BaseModel):
    """Top services by won deals."""

    period_start: datetime | None
    period_end: datetime | None
    items: list[RankedItemResponse]


class VariantPerformanceResponse(BaseModel):
    """Rolled-up performance for one prompt version / A/B variant."""

    variant_id: str
    label: str
    sent: int
    opened: int
    replied: int
    meetings_booked: int
    deals_won: int
    open_rate: float = Field(ge=0.0)
    reply_rate: float = Field(ge=0.0)
    meeting_rate: float = Field(ge=0.0)
    win_rate: float = Field(ge=0.0)


class PromptVersionPerformanceListResponse(BaseModel):
    """All prompt version / A/B variant performance buckets for a period."""

    period_start: datetime | None
    period_end: datetime | None
    variants: list[VariantPerformanceResponse]


class CampaignPerformanceListResponse(BaseModel):
    """All campaign-type performance buckets for a period.

    Reuses `VariantPerformanceResponse`'s shape -- each entry's `variant_id`/
    `label` carry the campaign type's value (e.g. "cold") rather than a
    prompt-version id. See app/services/analytics.py::get_campaign_performance.
    """

    period_start: datetime | None
    period_end: datetime | None
    campaigns: list[VariantPerformanceResponse]


class CreatePromptVersionRequest(BaseModel):
    """Request to log a new prompt version, optionally as part of an A/B test."""

    agent_name: str = Field(min_length=1, max_length=100)
    channel: str | None = Field(
        default=None, description="e.g. 'email', 'whatsapp', 'call_script'. Null applies agent-wide."
    )
    label: str = Field(min_length=1, max_length=100)
    prompt_text: str = Field(min_length=1)
    is_active: bool = True
    experiment_group: str | None = Field(
        default=None,
        description=(
            "Set on two or more active versions to run an A/B test between them. "
            "Leave null for a plain rollout with no split."
        ),
    )
    notes: str | None = None


class PromptVersionResponse(BaseModel):
    """A logged prompt version."""

    id: str
    agent_name: str
    channel: str | None
    label: str
    prompt_text: str
    is_active: bool
    experiment_group: str | None
    notes: str | None
    created_at: datetime


class LanguagePerformanceResponse(BaseModel):
    """Rolled-up performance metrics for drafts written in one language."""

    language: str = Field(
        description="BCP-47 language code, e.g. 'es', 'fr', or 'en' (default)."
    )
    drafts_sent: int = Field(ge=0)
    opens: int = Field(ge=0)
    open_rate: float = Field(ge=0.0)
    replies: int = Field(ge=0)
    reply_rate: float = Field(ge=0.0)
