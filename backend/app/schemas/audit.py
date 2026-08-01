"""Pydantic v2 schemas for website audits and social reviews."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.db.models.audit import AuditStatus
from app.services.audit_scoring import FindingCategory, Severity
from app.services.social_review import Platform, PostingCadence


class AuditRequest(BaseModel):
    """Request to audit a prospect's website.

    The audit crawls a third party's site, so it is always tied to a lead and
    to the requesting user for accountability.
    """

    url: HttpUrl
    lead_id: uuid.UUID | None = None
    include_social: bool = Field(
        default=True,
        description="Fold in any social profile reviews already recorded for the lead.",
    )


class SocialReviewRequest(BaseModel):
    """A reviewer's observations about one public social profile.

    Every field is something visible on a public profile page. Nothing here is
    scraped -- a human looks and fills this in.
    """

    platform: Platform
    profile_url: HttpUrl | None = None
    profile_exists: bool = False
    has_profile_image: bool = False
    has_cover_image: bool = False
    has_description: bool = False
    has_website_link: bool = False
    has_contact_details: bool = False
    cadence: PostingCadence = PostingCadence.NONE
    follower_band: str | None = Field(default=None, max_length=50, examples=["100-1k"])
    reviewer_notes: str | None = None


class SocialReviewResponse(SocialReviewRequest):
    """A stored social review."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    completeness_score: float = Field(ge=0.0, le=1.0)
    reviewed_at: datetime


class FindingResponse(BaseModel):
    """One audit finding."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    category: FindingCategory
    severity: Severity
    title: str
    detail: str
    evidence: str | None
    score: float | None
    mapped_service_id: uuid.UUID | None


class AuditResponse(BaseModel):
    """A website audit run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID | None
    url: str
    status: AuditStatus
    performance_score: float | None
    seo_score: float | None
    accessibility_score: float | None
    best_practices_score: float | None
    mobile_score: float | None
    ssl_valid: bool | None
    ssl_expires_at: datetime | None
    contact_form_found: bool | None
    contact_form_reachable: bool | None
    pages_crawled: int
    links_checked: int
    broken_link_count: int
    robots_blocked: bool
    health_score: float | None
    error_detail: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ReportResponse(BaseModel):
    """The generated business-friendly report.

    A document, not outreach. Sending it to the prospect is a separate action
    that goes through the approval gate in AGENTS.md section 8.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: uuid.UUID
    lead_id: uuid.UUID | None
    headline: str
    summary: str
    body_markdown: str
    generated_by_agent: str
    used_fallback: bool
    social_score: float | None


class AuditDetailResponse(BaseModel):
    """An audit together with its findings and report."""

    audit: AuditResponse
    findings: list[FindingResponse]
    report: ReportResponse | None
