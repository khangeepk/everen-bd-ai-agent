"""Pydantic v2 schemas for the email-enrichment API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.services.email_enrichment import EmailSource


class EmailCandidateResponse(BaseModel):
    """A candidate email found during one enrichment run (not yet persisted-shaped)."""

    email: str
    source: EmailSource
    confidence_score: float
    evidence: str | None = None


class EmailEnrichmentAttemptResponse(BaseModel):
    """A persisted enrichment attempt, as returned by the history endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    source: EmailSource
    candidate_email: str
    confidence_score: float
    evidence: str | None
    was_applied: bool
    detected_at: datetime


class EmailEnrichmentScanResponse(BaseModel):
    """Result of an on-demand email-enrichment run for one lead."""

    lead_id: uuid.UUID
    candidates: list[EmailCandidateResponse]
    applied: EmailCandidateResponse | None = None
    skipped_reason: str | None = None


class EmailVerifyResponse(BaseModel):
    """Result of manually confirming a lead's contact email."""

    lead_id: uuid.UUID
    contact_email_verified: bool
