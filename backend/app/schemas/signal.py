"""Pydantic v2 schemas for the lead-signals API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.signal import SignalType


class LeadSignalResponse(BaseModel):
    """One detected trigger event, as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    signal_type: SignalType
    detected_at: datetime
    detail: str | None
    source_reference: str | None
    acknowledged_at: datetime | None
    created_at: datetime


class SignalScanResponse(BaseModel):
    """Result of an on-demand signal scan for one lead."""

    lead_id: uuid.UUID
    new_signals: list[LeadSignalResponse]
    checked: list[SignalType] = Field(
        description="Signal types actually evaluated this run."
    )
    skipped: dict[str, str] = Field(
        description="Signal types not evaluated this run, with a short reason each."
    )
