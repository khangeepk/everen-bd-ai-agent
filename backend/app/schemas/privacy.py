"""Pydantic v2 schemas for the public GDPR/CCPA privacy routes."""

from __future__ import annotations

from pydantic import BaseModel


class DeleteRequestResponse(BaseModel):
    """Confirmation that an erasure request was processed."""

    message: str
    erased: bool
