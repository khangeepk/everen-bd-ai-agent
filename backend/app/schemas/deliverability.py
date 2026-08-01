"""Pydantic v2 schemas for the deliverability checklist: SPF/DKIM/DMARC
checks, warmup schedules, and the combined readiness report.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.deliverability import CheckStatus
from app.services.outreach_policy import OutreachChannel


class RunDeliverabilityCheckRequest(BaseModel):
    """Request to run a fresh SPF/DKIM/DMARC check."""

    domain: str | None = Field(
        default=None,
        description=(
            "Domain to check. Defaults to DELIVERABILITY_CHECK_DOMAIN, or the "
            "domain half of OUTREACH_FROM_EMAIL if that isn't set either."
        ),
    )
    dkim_selectors: list[str] | None = Field(
        default=None,
        description="DKIM selectors to try. Defaults to SENDGRID_DKIM_SELECTORS.",
    )


class DeliverabilityCheckResponse(BaseModel):
    """One SPF/DKIM/DMARC check run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain: str
    spf_status: CheckStatus
    spf_record: str | None
    spf_detail: str | None
    dmarc_status: CheckStatus
    dmarc_record: str | None
    dmarc_detail: str | None
    dkim_status: CheckStatus
    dkim_selectors_checked: str
    dkim_detail: str | None
    overall_status: CheckStatus
    checked_by_agent: str
    checked_at: datetime


class CreateWarmupScheduleRequest(BaseModel):
    """Request to create (and activate) a new warmup schedule."""

    channel: OutreachChannel = Field(default=OutreachChannel.EMAIL)
    start_date: date
    start_volume: int = Field(ge=1, description="Sends permitted on day 0.")
    target_daily_volume: int = Field(
        ge=1, description="Sends permitted once the ramp completes."
    )
    ramp_days: int = Field(ge=1, description="How many days the ramp spans.")


class WarmupScheduleResponse(BaseModel):
    """A configured warmup schedule."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: OutreachChannel
    start_date: date
    start_volume: int
    target_daily_volume: int
    ramp_days: int
    is_active: bool
    created_by_id: uuid.UUID | None
    created_at: datetime


class WarmupDayStatusResponse(BaseModel):
    """One day's planned-vs-actual standing under a warmup schedule."""

    model_config = ConfigDict(from_attributes=True)

    check_date: date
    planned_cap: int
    actual_sent: int
    within_cap: bool


class WarmupStatusResponse(BaseModel):
    """A channel's current warmup standing."""

    schedule: WarmupScheduleResponse | None
    today: WarmupDayStatusResponse | None
    ramp_complete: bool
    history: list[WarmupDayStatusResponse]


class ReadinessSectionResponse(BaseModel):
    """One section of the pre-launch readiness report."""

    status: CheckStatus
    messages: list[str]


class ReadinessReportResponse(BaseModel):
    """The combined pre-launch readiness report."""

    domain: str
    deliverability: DeliverabilityCheckResponse
    warmup: WarmupStatusResponse
    sender_identity: ReadinessSectionResponse
    sandbox_mode: ReadinessSectionResponse
    overall_status: CheckStatus
