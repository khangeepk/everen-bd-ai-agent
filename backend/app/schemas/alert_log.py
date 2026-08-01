"""Pydantic v2 schemas for the SendGrid deliverability alert webhook.

These models are used exclusively by the machine-to-machine
``POST /api/v1/outreach/pause`` endpoint called by the n8n health-monitor
workflow. They are intentionally separate from the human-facing outreach
schemas in ``app/schemas/outreach.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Alert type literals
# ---------------------------------------------------------------------------

AlertType = Literal[
    "bounce_rate_exceeded",
    "spam_rate_exceeded",
    "open_rate_drop",
]


# ---------------------------------------------------------------------------
# Request / Response for POST /api/v1/outreach/pause
# ---------------------------------------------------------------------------


class DomainPauseRequest(BaseModel):
    """Payload sent by n8n when a deliverability threshold is breached.

    Attributes:
        domain: The sending domain whose metric crossed the threshold,
            e.g. ``"mail.everen.io"``.
        alert_type: Which metric triggered the alert.
        metric_value: The computed metric at trigger time. For rates this is
            a ratio in [0, 1] (e.g. 0.063 = 6.3%). For ``open_rate_drop``
            this is the percentage-point delta between the 7-day rolling
            average and the 30-day baseline (positive = drop).
        threshold_value: The configured threshold that was crossed (same
            units as ``metric_value``).
    """

    domain: str = Field(
        ...,
        min_length=1,
        max_length=253,
        description="Sending domain, e.g. 'mail.everen.io'",
        examples=["mail.everen.io"],
    )
    alert_type: AlertType = Field(
        ...,
        description=(
            "Which deliverability metric triggered the alert. "
            "One of 'bounce_rate_exceeded', 'spam_rate_exceeded', or 'open_rate_drop'."
        ),
    )
    metric_value: float = Field(
        ...,
        description=(
            "The computed metric value at trigger time. "
            "A ratio 0-1 for bounce/spam rates; a pp delta for open_rate_drop."
        ),
        examples=[0.063],
    )
    threshold_value: float = Field(
        ...,
        description="The threshold that was crossed (same units as metric_value).",
        examples=[0.05],
    )


class DomainPauseResponse(BaseModel):
    """Response returned after pausing outreach for a domain.

    Attributes:
        alert_log_id: UUID of the newly created ``AlertLog`` row.
        domain: The domain that was paused.
        drafts_paused: Number of ``OutreachDraft`` rows moved to ``PAUSED``.
        message: Human-readable summary.
    """

    alert_log_id: uuid.UUID
    domain: str
    drafts_paused: int
    message: str


# ---------------------------------------------------------------------------
# Read schema for future GET /api/v1/outreach/alerts
# ---------------------------------------------------------------------------


class AlertLogResponse(BaseModel):
    """A single alert log row returned by a read endpoint.

    Attributes:
        id: UUID primary key.
        alert_type: Category of alert.
        domain: Sending domain.
        metric_value: Value that triggered the alert.
        threshold_value: Threshold that was crossed.
        triggered_at: UTC timestamp of the event.
        resolved_at: NULL until a human marks it resolved.
        drafts_paused_count: How many drafts were paused.
    """

    id: uuid.UUID
    alert_type: str
    domain: str
    metric_value: float
    threshold_value: float
    triggered_at: datetime
    resolved_at: datetime | None
    drafts_paused_count: int

    model_config = {"from_attributes": True}
