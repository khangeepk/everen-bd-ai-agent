"""Lead signal summaries for the outreach queue (GET /leads).

A lead with at least one unacknowledged :class:`~app.db.models.signal.LeadSignal`
sorts ahead of ``confidence_score`` in the leads listing -- a trigger event
(new job posting, business status change, review count jump) is a
time-sensitive reason to reach out now, regardless of how the lead scored
when it was first discovered. Once a rep acknowledges every open signal on a
lead (``POST /leads/{id}/signals/{signal_id}/acknowledge``), it drops back to
sorting by score like any other lead.

``active_signal_count``/``latest_signal_type``/``latest_signal_at`` are not
mapped columns on ``Lead`` -- they are computed here and set as plain
attributes on the ORM instance, which ``LeadResponse`` (``from_attributes=True``)
then reads like any other attribute. Every route returning a ``LeadResponse``
must call :func:`attach_signal_summary` (or, for a listing, use
:func:`signal_summary_columns`) before validating, or these fields will be
missing.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.db.models.lead import Lead
from app.db.models.signal import LeadSignal


async def attach_signal_summary(db: AsyncSession, lead: Lead) -> None:
    """Set the signal-summary attributes on a single lead, in place.

    Two extra queries -- fine for a single-object response (create/get/
    update/promote); the leads *listing* endpoint uses
    :func:`signal_summary_columns` instead to do this in one query for the
    whole page.

    Args:
        db: Active database session.
        lead: The Lead ORM instance to annotate, in place.
    """
    active_count = (
        await db.execute(
            select(func.count(LeadSignal.id)).where(
                LeadSignal.lead_id == lead.id, LeadSignal.acknowledged_at.is_(None)
            )
        )
    ).scalar_one()

    latest = (
        await db.execute(
            select(LeadSignal.signal_type, LeadSignal.detected_at)
            .where(LeadSignal.lead_id == lead.id)
            .order_by(LeadSignal.detected_at.desc())
            .limit(1)
        )
    ).first()

    lead.active_signal_count = int(active_count)
    lead.latest_signal_type = latest[0] if latest is not None else None
    lead.latest_signal_at = latest[1] if latest is not None else None


def signal_summary_columns() -> tuple[ColumnElement, ColumnElement, ColumnElement]:
    """Build correlated-subquery columns for a leads listing query.

    Lets ``GET /leads`` compute each row's signal summary and sort by it in a
    single query, rather than one extra pair of queries per row on a page.

    Returns:
        A tuple of (active_signal_count, latest_signal_type, latest_signal_at)
        column expressions. Each correlates against ``Lead`` by ``id`` --
        the caller's base query must select from :class:`Lead`.
    """
    active_signal_count = (
        select(func.count(LeadSignal.id))
        .where(LeadSignal.lead_id == Lead.id, LeadSignal.acknowledged_at.is_(None))
        .correlate(Lead)
        .scalar_subquery()
    )
    latest_signal_type = (
        select(LeadSignal.signal_type)
        .where(LeadSignal.lead_id == Lead.id)
        .order_by(LeadSignal.detected_at.desc())
        .limit(1)
        .correlate(Lead)
        .scalar_subquery()
    )
    latest_signal_at = (
        select(LeadSignal.detected_at)
        .where(LeadSignal.lead_id == Lead.id)
        .order_by(LeadSignal.detected_at.desc())
        .limit(1)
        .correlate(Lead)
        .scalar_subquery()
    )
    return active_signal_count, latest_signal_type, latest_signal_at
