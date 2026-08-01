"""Shared helper for writing to `OutreachAuditLog`.

Extracted from `app/api/v1/outreach.py` so the new event-triggered objection
scanner (`app/services/objection_response_scanner.py`) can log a draft's
creation identically to every human-initiated status change, rather than
duplicating this AGENTS.md section 8.5-required write. There is exactly one
place in this codebase that inserts an `OutreachAuditLog` row; this is it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.outreach import DraftStatus, OutreachAuditLog, OutreachDraft


async def log_draft_transition(
    db: AsyncSession,
    draft: OutreachDraft,
    old_status: DraftStatus | None,
    new_status: DraftStatus,
    user_id: uuid.UUID | None,
    note: str | None = None,
) -> None:
    """Record a draft status transition in the audit log.

    Required by AGENTS.md section 8.5.

    Args:
        db: Active database session.
        draft: The draft that changed.
        old_status: Status before the change, None on creation.
        new_status: Status after the change.
        user_id: Who made the change. None for a system-triggered action
            (e.g. an auto-generated objection-response draft), same as any
            other unattributed system action already logged in this table.
        note: Optional context.
    """
    db.add(
        OutreachAuditLog(
            draft_id=draft.id,
            old_status=old_status,
            new_status=new_status,
            changed_by_id=user_id,
            changed_at=utcnow(),
            note=note,
        )
    )
    await db.flush()
