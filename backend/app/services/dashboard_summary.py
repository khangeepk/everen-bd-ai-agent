"""Aggregation queries backing the frontend B2B Deal Flow dashboard.

Every number here is computed from real rows -- no placeholder or
client-invented data. Where the frontend needs something this schema doesn't
store directly (a dollar "deal value" per lead), it is estimated from the
same revenue component the real scoring formula already computes
(``app/services/lead_scoring.py``), scaled by the configured deal-size range
(``settings.lead_score_revenue_scale_min/max``) -- never a fabricated number.

"Follow-ups due" reuses the exact pure cadence logic
(`app/services/campaign_cadence.py::is_follow_up_due`) that
`app/services/campaign_followup_scanner.py` uses to decide whether to draft
one, so the dashboard count and the scanner's actual behavior can never
disagree about what "due" means.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.lead import Lead, LeadStatus
from app.db.models.lead_score import LeadScore
from app.db.models.outreach import DraftStatus, OutreachChannel, OutreachDraft
from app.db.models.pipeline import InboundMessage
from app.services.campaign_cadence import is_follow_up_due
from app.services.pipeline import PipelineStage

logger = logging.getLogger(__name__)

#: Kanban columns in the order the frontend renders them
#: (frontend/src/components/dashboard/KanbanFunnel.tsx's COLUMN_STAGE map).
#: MEETING_BOOKED has no dedicated column in that existing 6-column layout,
#: so it is folded into HOT ("negotiation") -- a lead with a meeting booked
#: is, if anything, further along than a merely-hot one, not a different
#: bucket the frontend has a slot for.
_KANBAN_STAGE_ORDER: tuple[tuple[str, str, tuple[PipelineStage, ...]], ...] = (
    ("prospecting", "Prospecting", (PipelineStage.NEW,)),
    ("qualification", "Qualification", (PipelineStage.CONTACTED,)),
    ("proposal", "Proposal", (PipelineStage.INTERESTED,)),
    ("negotiation", "Negotiation", (PipelineStage.HOT, PipelineStage.MEETING_BOOKED)),
    ("won", "Won", (PipelineStage.CONVERTED,)),
    ("lost", "Lost", (PipelineStage.LOST,)),
)

#: How many deal cards to show per Kanban column -- a dashboard glance, not a
#: full export. Most recently created first within a column.
_DEALS_PER_COLUMN = 12

#: Channels a campaign-cadence follow-up can be due on -- mirrors
#: campaign_followup_scanner.py's _FOLLOW_UP_CHANNELS (CALL_SCRIPT is
#: excluded there because nothing tracks whether/when a call happened).
_FOLLOW_UP_CHANNELS: tuple[OutreachChannel, ...] = (OutreachChannel.EMAIL, OutreachChannel.WHATSAPP)


@dataclass(frozen=True)
class DealCard:
    """One lead as shown on a Kanban card."""

    id: str
    account_name: str
    deal_value_label: str
    score: float | None
    score_reasons: tuple[str, ...]
    compliance_state: str | None


@dataclass(frozen=True)
class KanbanColumn:
    """One Kanban column with its cards."""

    column_id: str
    title: str
    deals: tuple[DealCard, ...]


@dataclass(frozen=True)
class KpiMetric:
    """One top-row KPI card."""

    metric_id: str
    label: str
    value: str
    change_label: str | None = None
    trend: str | None = None


@dataclass(frozen=True)
class DashboardSummary:
    """Everything the dashboard's KPI row, Kanban board, and next-action
    banner need, computed in one pass."""

    kpis: tuple[KpiMetric, ...]
    kanban_columns: tuple[KanbanColumn, ...]
    drafts_awaiting_approval: int
    hot_leads_to_review: int
    replies_to_classify: int
    follow_ups_due: int


def _estimated_deal_value(revenue_score: float | None) -> float:
    """Estimate a dollar deal value from the revenue score component.

    Args:
        revenue_score: The lead's most recent revenue component score
            (0.0-1.0), or None if the lead has never been scored.

    Returns:
        A dollar estimate within the configured deal-size range, or the
        range's floor if the lead has no score yet (the conservative
        assumption, not the average).
    """
    lo = settings.lead_score_revenue_scale_min
    hi = settings.lead_score_revenue_scale_max
    if revenue_score is None:
        return lo
    return lo + revenue_score * (hi - lo)


def _format_usd(amount: float) -> str:
    """Format a dollar amount for a KPI/card display, e.g. '$45,000'."""
    return f"${amount:,.0f}"


def compliance_state_for_lead(lead: Lead) -> str | None:
    """Derive the frontend's ComplianceState from real Lead fields.

    Mirrors the states frontend/src/lib/plainLanguage.ts already knows how to
    render (unsubscribed/hard_bounce/spam_complaint/manual/do_not_contact/
    gdpr_deleted), matched against the exact reason strings this codebase
    itself writes into do_not_contact_reason (app/api/v1/outreach.py's
    unsubscribe and bounce-webhook handlers).

    Args:
        lead: The lead to classify.

    Returns:
        A ComplianceState string, or None if the lead is not blocked.
    """
    if lead.pii_erased_at is not None:
        return "gdpr_deleted"
    if not lead.do_not_contact:
        return None

    reason = (lead.do_not_contact_reason or "").lower()
    if "unsubscribe" in reason:
        return "unsubscribed"
    if "hard" in reason:
        return "hard_bounce"
    if "complaint" in reason:
        return "spam_complaint"
    if reason:
        return "manual"
    return "do_not_contact"


async def latest_scores_by_lead(
    db: AsyncSession, lead_ids: list[uuid.UUID]
) -> dict[uuid.UUID, LeadScore]:
    """Fetch each lead's most recently computed score.

    Args:
        db: Active session.
        lead_ids: Leads to look up.

    Returns:
        A mapping of lead_id -> its latest LeadScore row. Leads with no
        score yet are simply absent from the mapping.
    """
    if not lead_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(LeadScore)
                .where(LeadScore.lead_id.in_(lead_ids))
                .order_by(LeadScore.lead_id, LeadScore.computed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[uuid.UUID, LeadScore] = {}
    for row in rows:
        if row.lead_id not in latest:  # first row per lead_id is the newest, per ORDER BY
            latest[row.lead_id] = row
    return latest


async def _build_kanban(db: AsyncSession) -> tuple[KanbanColumn, ...]:
    """Build every Kanban column with its deal cards.

    Args:
        db: Active session.

    Returns:
        The columns in display order.
    """
    columns: list[KanbanColumn] = []
    for column_id, title, stages in _KANBAN_STAGE_ORDER:
        leads = (
            (
                await db.execute(
                    select(Lead)
                    .where(Lead.pipeline_stage.in_(stages))
                    .order_by(Lead.created_at.desc())
                    .limit(_DEALS_PER_COLUMN)
                )
            )
            .scalars()
            .all()
        )
        scores = await latest_scores_by_lead(db, [lead.id for lead in leads])

        cards: list[DealCard] = []
        for lead in leads:
            score_row = scores.get(lead.id)
            reasons: tuple[str, ...] = ()
            if score_row is not None:
                reasons = tuple(
                    r
                    for r in (
                        (score_row.need_reasons or "").split("\n")
                        + (score_row.fit_reasons or "").split("\n")
                    )
                    if r
                )[:3]
            cards.append(
                DealCard(
                    id=str(lead.id),
                    account_name=lead.name,
                    deal_value_label=_format_usd(
                        _estimated_deal_value(score_row.revenue_score if score_row else None)
                    ),
                    score=score_row.total_score if score_row else None,
                    score_reasons=reasons,
                    compliance_state=compliance_state_for_lead(lead),
                )
            )
        columns.append(KanbanColumn(column_id=column_id, title=title, deals=tuple(cards)))
    return columns


async def _count_follow_ups_due(db: AsyncSession) -> int:
    """Count CONTACTED leads whose next cadence follow-up is due now.

    Mirrors app/services/campaign_followup_scanner.py's candidate selection
    (pipeline_stage == CONTACTED) and due-check (is_follow_up_due), as a
    read-only count -- this never drafts anything, unlike the scanner.

    Args:
        db: Active session.

    Returns:
        How many contacted, non-responding leads have a follow-up due.
    """
    leads = (
        (
            await db.execute(
                select(Lead).where(Lead.pipeline_stage == PipelineStage.CONTACTED)
            )
        )
        .scalars()
        .all()
    )
    if not leads:
        return 0

    now = datetime.now(timezone.utc)
    due_count = 0
    for lead in leads:
        # Most recent SENT draft for this lead, on a followable channel.
        last_sent = (
            (
                await db.execute(
                    select(OutreachDraft)
                    .where(
                        OutreachDraft.lead_id == lead.id,
                        OutreachDraft.status == DraftStatus.SENT,
                        OutreachDraft.channel.in_(_FOLLOW_UP_CHANNELS),
                    )
                    .order_by(OutreachDraft.sent_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if last_sent is None or last_sent.sent_at is None:
            continue
        if is_follow_up_due(
            lead.campaign_type, last_sent.follow_up_sequence, last_sent.sent_at, now
        ):
            due_count += 1
    return due_count


async def get_dashboard_summary(db: AsyncSession) -> DashboardSummary:
    """Compute every real number the dashboard's top row and Kanban need.

    Args:
        db: Active session.

    Returns:
        The full summary.
    """
    total_leads = (await db.execute(select(func.count()).select_from(Lead))).scalar_one()

    hot_leads = (
        await db.execute(
            select(func.count())
            .select_from(Lead)
            .where(Lead.pipeline_stage == PipelineStage.HOT)
        )
    ).scalar_one()

    won_count = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.WON)
        )
    ).scalar_one()
    lost_count = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.LOST)
        )
    ).scalar_one()
    decided_count = won_count + lost_count
    win_rate_pct = round((won_count / decided_count) * 100) if decided_count else 0

    pending_review = (
        await db.execute(
            select(func.count())
            .select_from(OutreachDraft)
            .where(OutreachDraft.status == DraftStatus.PENDING_REVIEW)
        )
    ).scalar_one()

    replies_to_classify = (
        await db.execute(
            select(func.count())
            .select_from(InboundMessage)
            .where(InboundMessage.classified_intent.is_(None))
        )
    ).scalar_one()

    # Average estimated deal value across every scored lead, using the same
    # revenue-component estimate as the Kanban cards. Ordered oldest-first so
    # each later iteration's dict write for a given lead_id overwrites an
    # earlier one, leaving only the most recent score per lead.
    revenue_scores = (
        await db.execute(
            select(LeadScore.lead_id, LeadScore.revenue_score).order_by(LeadScore.computed_at.asc())
        )
    ).all()
    latest_revenue_by_lead: dict[uuid.UUID, float] = {}
    for lead_id, revenue_score in revenue_scores:
        latest_revenue_by_lead[lead_id] = revenue_score
    estimates = [_estimated_deal_value(v) for v in latest_revenue_by_lead.values()]
    avg_deal_value = sum(estimates) / len(estimates) if estimates else 0.0
    total_pipeline_value = sum(estimates)

    kpis = (
        KpiMetric(
            metric_id="pipeline",
            label="Total pipeline (est.)",
            value=_format_usd(total_pipeline_value),
        ),
        KpiMetric(
            metric_id="avg-deal",
            label="Avg deal size (est.)",
            value=_format_usd(avg_deal_value) if estimates else "No scored leads yet",
        ),
        KpiMetric(
            metric_id="win-rate",
            label="Win rate",
            value=(
                f"{win_rate_pct}% — from {decided_count} deal"
                f"{'s' if decided_count != 1 else ''}"
                if decided_count
                else "No closed deals yet"
            ),
        ),
        KpiMetric(metric_id="leads", label="Leads in system", value=str(total_leads)),
        KpiMetric(
            metric_id="pending-approval",
            label="Pending approvals",
            value=str(pending_review),
        ),
    )

    kanban_columns = await _build_kanban(db)
    follow_ups_due = await _count_follow_ups_due(db)

    logger.info(
        "Dashboard summary computed",
        extra={
            "total_leads": total_leads,
            "hot_leads": hot_leads,
            "pending_review": pending_review,
            "replies_to_classify": replies_to_classify,
            "follow_ups_due": follow_ups_due,
        },
    )

    return DashboardSummary(
        kpis=kpis,
        kanban_columns=kanban_columns,
        drafts_awaiting_approval=int(pending_review),
        hot_leads_to_review=int(hot_leads),
        replies_to_classify=int(replies_to_classify),
        follow_ups_due=follow_ups_due,
    )
