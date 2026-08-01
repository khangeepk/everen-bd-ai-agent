"""Pydantic v2 schemas for the Leads API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from app.db.models.lead import LeadSource, LeadStatus
from app.db.models.signal import SignalType
from app.services.email_enrichment import EmailSource
from app.services.outreach_policy import CampaignType


class LeadBase(BaseModel):
    """Fields shared by lead create and read schemas."""

    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_title: str | None = Field(default=None, max_length=150)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=50)
    website: HttpUrl | None = None
    linkedin_url: HttpUrl | None = None
    country: str | None = Field(default=None, max_length=100)
    #: BCP-47 language code auto-detected from the lead's website or country.
    #: Null means unknown — the draft generator will write in English.
    detected_language: str | None = Field(default=None, max_length=10)
    #: Manual override for detected_language. Wins when set.
    language_override: str | None = Field(default=None, max_length=10)
    source: LeadSource = LeadSource.MANUAL
    source_detail: str | None = Field(default=None, max_length=500)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    campaign_type: CampaignType = Field(
        default=CampaignType.COLD,
        description=(
            "Which kind of relationship this lead is being pursued under. Drives "
            "draft tone and follow-up cadence -- see "
            "app/services/outreach_policy.py::CampaignType."
        ),
    )
    notes: str | None = None
    do_not_contact: bool = Field(
        default=False,
        description=(
            "Hard suppression flag. True means this lead must never be contacted "
            "-- feeds the ComplianceRisk gate in the scoring engine."
        ),
    )
    do_not_contact_reason: str | None = Field(default=None, max_length=300)
    consent_basis: str | None = Field(
        default=None,
        max_length=50,
        examples=["consent", "legitimate_interest", "public_task"],
        description="Lawful basis recorded for holding/contacting this lead's data.",
    )
    gdpr_consent: bool = Field(
        default=False,
        description=(
            "Whether this person affirmatively opted in, specifically. Distinct from "
            "consent_basis, which records which of GDPR Article 6's lawful bases "
            "applies -- consent is only one of several, so a lead can be lawfully "
            "held under a different basis without this ever being true."
        ),
    )
    gdpr_consent_source: str | None = Field(
        default=None, max_length=200, examples=["signup form", "verbal at trade show"]
    )


class LeadCreate(LeadBase):
    """Request body for creating a lead."""


class LeadUpdate(BaseModel):
    """Partial update for an existing lead. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_title: str | None = Field(default=None, max_length=150)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=50)
    website: HttpUrl | None = None
    linkedin_url: HttpUrl | None = None
    country: str | None = Field(default=None, max_length=100)
    source_detail: str | None = Field(default=None, max_length=500)
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    status: LeadStatus | None = None
    campaign_type: CampaignType | None = None
    notes: str | None = None
    do_not_contact: bool | None = None
    do_not_contact_reason: str | None = Field(default=None, max_length=300)
    consent_basis: str | None = Field(default=None, max_length=50)
    gdpr_consent: bool | None = None
    gdpr_consent_source: str | None = Field(default=None, max_length=200)
    detected_language: str | None = Field(default=None, max_length=10)
    language_override: str | None = Field(default=None, max_length=10)


class LeadResponse(LeadBase):
    """A lead as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: LeadStatus
    gdpr_consent_recorded_at: datetime | None
    pii_erased_at: datetime | None = Field(
        default=None,
        description="Set once a GDPR/CCPA erasure request has been fulfilled for this lead.",
    )
    active_signal_count: int = Field(
        default=0,
        description=(
            "Unacknowledged trigger-event signals (job posting, business status "
            "change, review count jump) detected for this lead. Leads with 1+ sort "
            "to the top of GET /leads, ahead of confidence_score -- see "
            "app/services/signal_queue.py."
        ),
    )
    latest_signal_type: SignalType | None = Field(
        default=None, description="Most recent signal detected for this lead, if any."
    )
    latest_signal_at: datetime | None = Field(
        default=None, description="When the most recent signal was detected, if any."
    )
    contact_email_source: EmailSource = Field(
        default=EmailSource.MANUAL,
        description="How contact_email was obtained. See app/services/email_enrichment.py.",
    )
    contact_email_confidence: float | None = Field(
        default=None,
        description="Confidence score for an enrichment-sourced email. Null for a manual entry.",
    )
    contact_email_verified: bool = Field(
        default=True,
        description=(
            "Whether contact_email is trusted enough to draft/send to. False only for an "
            "unconfirmed enrichment-sourced address -- see POST /leads/{id}/email/verify "
            "and app/services/outreach_policy.py."
        ),
    )
    created_at: datetime
    updated_at: datetime


class PaginatedLeads(BaseModel):
    """A page of leads.

    Page size is capped at 100 per AGENTS.md section 9.3.
    """

    items: list[LeadResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
