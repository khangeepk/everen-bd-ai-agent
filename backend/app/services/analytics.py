"""DB-aware analytics aggregation for the BD performance dashboard.

Pure arithmetic (rates, rankings) lives in `app/services/analytics_math.py`
and is unit-tested without a database; this module is the query layer that
feeds it from `OutreachDraft`, `PipelineEvent`, `InboundMessage`,
`EmailOpenEvent`, and `PromptVersion`.

Metric definitions (documented here since a dashboard number is only useful
if its definition is known):

* **Emails sent** -- `OutreachDraft` rows with channel=email, status=sent,
  `sent_at` within the period.
* **Open rate** -- distinct sent-email drafts with at least one
  `EmailOpenEvent`, divided by emails sent. NOTE: outreach emails are
  currently sent as plain text (see `app/services/canspam.py`), so the
  tracking pixel this depends on has nothing to attach to yet -- open rate
  will read as 0 until email sending is switched to HTML. The
  infrastructure (endpoint, model, this metric) is ready for when that
  change is made.
* **Reply rate** -- an approximation, not per-draft attribution: distinct
  leads with at least one `InboundMessage` in the period, divided by
  distinct leads with at least one sent email draft in the period. A precise
  "did lead X reply to draft Y specifically" would require matching reply
  headers/threading, which is out of scope here.
* **Meetings booked** -- count of `PipelineEvent` rows where the lead
  entered pipeline stage Meeting Booked (`to_stage=meeting_booked`,
  `from_stage != meeting_booked`) in the period -- i.e. a real slot was
  confirmed on the shared sales calendar via a booking link (see
  app.api.v1.booking, app.services.pipeline_transitions.
  advance_on_meeting_booked). Before the calendar-booking feature existed
  this metric proxied "entered Hot" (`to_stage=hot`); it was redefined to
  this stricter, ground-truth definition once Meeting Booked became a
  distinct pipeline stage a lead only reaches by a prospect actually
  confirming a time, not by a reply merely being classified as wanting one.
* **Deals won** -- count of `PipelineEvent` rows reaching `to_stage=converted`
  in the period.
* **Top industries / services** -- ranked by count of *won* deals whose lead
  has that `category` / whose sent email draft named that
  `source_service_id`, not by raw lead volume -- an industry you talk to a
  lot but never close is not what "top" should mean on a BD dashboard.
* **Campaign performance** -- sent/opened/replied/meetings/won rolled up by
  each sent email draft's own (snapshotted) `campaign_type`
  (cold/warm/re_engagement -- see `app/services/outreach_policy.py`), the
  same bucketing shape as prompt-version performance below, so a re-
  engagement campaign's reply rate can be compared directly against cold
  outreach's.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import EmailOpenEvent, PromptVersion
from app.db.models.knowledge_base import Service
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import InboundMessage, PipelineEvent
from app.services.analytics_math import RankedItem, VariantPerformance, safe_rate, top_n
from app.services.outreach_policy import CampaignType, OutreachChannel
from app.services.pipeline import PipelineStage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverviewMetrics:
    """Top-line dashboard numbers for a date range.

    Attributes:
        period_start: Inclusive start of the reporting window, if bounded.
        period_end: Exclusive end of the reporting window, if bounded.
        emails_sent: Sent email drafts in the period.
        opens: Distinct sent emails opened at least once.
        open_rate: ``opens / emails_sent``.
        replies: Distinct leads who sent at least one inbound message.
        reply_rate: ``replies / (distinct leads emailed)``.
        meetings_booked: Leads that entered pipeline stage Meeting Booked
            (a real calendar slot confirmed via a booking link).
        deals_won: Leads that reached pipeline stage Converted.
    """

    period_start: datetime | None
    period_end: datetime | None
    emails_sent: int
    opens: int
    open_rate: float
    replies: int
    reply_rate: float
    meetings_booked: int
    deals_won: int


@dataclass(frozen=True)
class LanguagePerformance:
    """Rolled-up performance metrics for drafts written in one language."""

    language: str
    drafts_sent: int
    opens: int
    open_rate: float
    replies: int
    reply_rate: float


def _period_filters(column, start: datetime | None, end: datetime | None) -> list:
    """Build inclusive-start/exclusive-end filters for an optional date range.

    Args:
        column: The timestamp column to filter.
        start: Inclusive lower bound, or None for unbounded.
        end: Exclusive upper bound, or None for unbounded.

    Returns:
        A list of SQLAlchemy filter expressions (possibly empty).
    """
    filters = []
    if start is not None:
        filters.append(column >= start)
    if end is not None:
        filters.append(column < end)
    return filters


async def get_overview(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> OverviewMetrics:
    """Compute the top-line dashboard metrics for a date range.

    Args:
        db: Active database session.
        start: Inclusive start of the reporting window, or None for all time.
        end: Exclusive end of the reporting window, or None for all time.

    Returns:
        The computed overview.
    """
    sent_filters = [
        OutreachDraft.channel == OutreachChannel.EMAIL,
        OutreachDraft.status == DraftStatus.SENT,
        OutreachDraft.sent_at.is_not(None),
        *_period_filters(OutreachDraft.sent_at, start, end),
    ]

    emails_sent = (
        await db.execute(select(func.count()).select_from(OutreachDraft).where(*sent_filters))
    ).scalar_one()

    opens = (
        await db.execute(
            select(func.count(func.distinct(EmailOpenEvent.draft_id)))
            .select_from(EmailOpenEvent)
            .join(OutreachDraft, OutreachDraft.id == EmailOpenEvent.draft_id)
            .where(*sent_filters)
        )
    ).scalar_one()

    emailed_leads = (
        await db.execute(
            select(func.count(func.distinct(OutreachDraft.lead_id))).where(*sent_filters)
        )
    ).scalar_one()

    reply_filters = _period_filters(InboundMessage.received_at, start, end)
    replied_leads = (
        await db.execute(
            select(func.count(func.distinct(InboundMessage.lead_id))).where(*reply_filters)
        )
    ).scalar_one()

    meetings_booked = (
        await db.execute(
            select(func.count())
            .select_from(PipelineEvent)
            .where(
                PipelineEvent.to_stage == PipelineStage.MEETING_BOOKED,
                PipelineEvent.from_stage != PipelineStage.MEETING_BOOKED,
                *_period_filters(PipelineEvent.changed_at, start, end),
            )
        )
    ).scalar_one()

    deals_won = (
        await db.execute(
            select(func.count())
            .select_from(PipelineEvent)
            .where(
                PipelineEvent.to_stage == PipelineStage.CONVERTED,
                *_period_filters(PipelineEvent.changed_at, start, end),
            )
        )
    ).scalar_one()

    overview = OverviewMetrics(
        period_start=start,
        period_end=end,
        emails_sent=int(emails_sent),
        opens=int(opens),
        open_rate=safe_rate(int(opens), int(emails_sent)),
        replies=int(replied_leads),
        reply_rate=safe_rate(int(replied_leads), int(emailed_leads)),
        meetings_booked=int(meetings_booked),
        deals_won=int(deals_won),
    )
    logger.info(
        "Computed analytics overview",
        extra={
            "emails_sent": overview.emails_sent,
            "open_rate": overview.open_rate,
            "reply_rate": overview.reply_rate,
            "meetings_booked": overview.meetings_booked,
            "deals_won": overview.deals_won,
        },
    )
    return overview


async def _won_lead_ids(
    db: AsyncSession, start: datetime | None, end: datetime | None
) -> list[uuid.UUID]:
    """Fetch the IDs of leads that reached Converted within a period.

    Args:
        db: Active database session.
        start: Inclusive start of the window, or None.
        end: Exclusive end of the window, or None.

    Returns:
        Distinct lead IDs.
    """
    rows = (
        await db.execute(
            select(func.distinct(PipelineEvent.lead_id)).where(
                PipelineEvent.to_stage == PipelineStage.CONVERTED,
                *_period_filters(PipelineEvent.changed_at, start, end),
            )
        )
    ).scalars()
    import uuid as _uuid
    return [_uuid.UUID(str(r)) if not isinstance(r, _uuid.UUID) else r for r in rows if r]


async def get_top_industries(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None, n: int = 5
) -> list[RankedItem]:
    """Rank lead industries (`Lead.category`) by count of won deals.

    Args:
        db: Active database session.
        start: Inclusive start of the window, or None.
        end: Exclusive end of the window, or None.
        n: How many entries to return.

    Returns:
        Up to ``n`` ranked industries.
    """
    won_ids = await _won_lead_ids(db, start, end)
    if not won_ids:
        return []

    rows = (
        await db.execute(
            select(Lead.category, func.count())
            .where(Lead.id.in_(won_ids))
            .group_by(Lead.category)
        )
    ).all()
    counts = {(category or ""): count for category, count in rows}
    return top_n(counts, n=n)


async def get_top_services(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None, n: int = 5
) -> list[RankedItem]:
    """Rank recommended services by count of won deals they were tied to.

    A deal is tied to a service via the `source_service_id` on its lead's
    sent email drafts -- the same signal used to draft the outreach in the
    first place.

    Args:
        db: Active database session.
        start: Inclusive start of the window, or None.
        end: Exclusive end of the window, or None.
        n: How many entries to return.

    Returns:
        Up to ``n`` ranked services.
    """
    won_ids = await _won_lead_ids(db, start, end)
    if not won_ids:
        return []

    rows = (
        await db.execute(
            select(Service.name, func.count(func.distinct(OutreachDraft.lead_id)))
            .select_from(OutreachDraft)
            .join(Service, Service.id == OutreachDraft.source_service_id)
            .where(
                OutreachDraft.lead_id.in_(won_ids),
                OutreachDraft.source_service_id.is_not(None),
            )
            .group_by(Service.name)
        )
    ).all()
    counts = {name: count for name, count in rows}
    return top_n(counts, n=n)


async def get_prompt_version_performance(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[VariantPerformance]:
    """Roll up performance per prompt version (and A/B variant) used on sent emails.

    Drafts generated before any `PromptVersion` existed have a null
    `prompt_version_id` and are grouped together under the label
    "(code-default prompt)" -- comparing that bucket's performance against a
    later, deliberately versioned prompt is exactly the "old vs new prompt"
    comparison this exists for.

    Args:
        db: Active database session.
        start: Inclusive start of the window, or None.
        end: Exclusive end of the window, or None.

    Returns:
        One `VariantPerformance` per distinct `(prompt_version_id, ab_variant)`
        pairing that has at least one sent email in the period.
    """
    sent_filters = [
        OutreachDraft.channel == OutreachChannel.EMAIL,
        OutreachDraft.status == DraftStatus.SENT,
        OutreachDraft.sent_at.is_not(None),
        *_period_filters(OutreachDraft.sent_at, start, end),
    ]

    drafts = (
        (
            await db.execute(
                select(OutreachDraft)
                .where(*sent_filters)
                .order_by(OutreachDraft.sent_at)
            )
        )
        .scalars()
        .all()
    )
    if not drafts:
        return []

    def _canonical_uuid(val: object) -> str:
        if isinstance(val, uuid.UUID):
            return str(val)
        try:
            return str(uuid.UUID(str(val)))
        except Exception:
            return str(val)

    draft_ids = [d.id for d in drafts]
    opened_ids = {
        _canonical_uuid(i)
        for i in (
            await db.execute(
                select(func.distinct(EmailOpenEvent.draft_id)).where(
                    EmailOpenEvent.draft_id.in_(draft_ids)
                )
            )
        )
        .scalars()
        if i
    }

    lead_ids = {d.lead_id for d in drafts}
    replied_lead_ids = {
        _canonical_uuid(i)
        for i in (
            await db.execute(
                select(func.distinct(InboundMessage.lead_id)).where(
                    InboundMessage.lead_id.in_(lead_ids)
                )
            )
        )
        .scalars()
        if i
    }
    meeting_booked_lead_ids = {
        _canonical_uuid(i)
        for i in (
            await db.execute(
                select(func.distinct(PipelineEvent.lead_id)).where(
                    PipelineEvent.lead_id.in_(lead_ids),
                    PipelineEvent.to_stage == PipelineStage.MEETING_BOOKED,
                    PipelineEvent.from_stage != PipelineStage.MEETING_BOOKED,
                )
            )
        )
        .scalars()
        if i
    }
    won_lead_ids = {
        _canonical_uuid(i)
        for i in (
            await db.execute(
                select(func.distinct(PipelineEvent.lead_id)).where(
                    PipelineEvent.lead_id.in_(lead_ids),
                    PipelineEvent.to_stage == PipelineStage.CONVERTED,
                )
            )
        )
        .scalars()
        if i
    }

    version_ids = {d.prompt_version_id for d in drafts if d.prompt_version_id is not None}
    labels_by_id: dict[uuid.UUID, str] = {}
    if version_ids:
        rows = (
            await db.execute(select(PromptVersion).where(PromptVersion.id.in_(version_ids)))
        ).scalars()
        for row in rows:
            labels_by_id[row.id] = row.label

    buckets: dict[tuple[uuid.UUID | None, str | None], dict[str, int]] = {}
    for draft in drafts:
        key = (draft.prompt_version_id, draft.ab_variant)
        bucket = buckets.setdefault(
            key, {"sent": 0, "opened": 0, "replied": 0, "meetings": 0, "won": 0}
        )
        bucket["sent"] += 1
        if str(draft.id) in opened_ids:
            bucket["opened"] += 1
        if str(draft.lead_id) in replied_lead_ids:
            bucket["replied"] += 1
        if str(draft.lead_id) in meeting_booked_lead_ids:
            bucket["meetings"] += 1
        if str(draft.lead_id) in won_lead_ids:
            bucket["won"] += 1

    results: list[VariantPerformance] = []
    for (version_id, ab_variant), counts in buckets.items():
        if version_id is None:
            label = "(code-default prompt)"
        else:
            base_label = labels_by_id.get(version_id, str(version_id))
            label = f"{base_label} ({ab_variant})" if ab_variant else base_label
        results.append(
            VariantPerformance(
                variant_id=str(version_id) if version_id else "default",
                label=label,
                sent=counts["sent"],
                opened=counts["opened"],
                replied=counts["replied"],
                meetings_booked=counts["meetings"],
                deals_won=counts["won"],
            )
        )

    logger.info("Computed prompt version performance", extra={"buckets": len(results)})
    return results


async def get_campaign_performance(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[VariantPerformance]:
    """Roll up performance per campaign type on sent emails.

    Mirrors :func:`get_prompt_version_performance`'s bucketing shape exactly,
    keyed on each draft's own snapshotted `campaign_type` instead of
    `(prompt_version_id, ab_variant)` -- see app/db/models/outreach.py's
    comment on why campaign_type is snapshotted at draft-generation time
    rather than read live from the lead (so a lead's campaign_type changing
    later never rewrites which bucket an already-sent draft counted toward).

    Args:
        db: Active database session.
        start: Inclusive start of the window, or None for all time.
        end: Exclusive end of the window, or None for all time.

    Returns:
        One `VariantPerformance` per campaign type that has at least one
        sent email in the period. `variant_id`/`label` carry the campaign
        type's value (e.g. "cold") rather than a prompt-version id.
    """
    sent_filters = [
        OutreachDraft.channel == OutreachChannel.EMAIL,
        OutreachDraft.status == DraftStatus.SENT,
        OutreachDraft.sent_at.is_not(None),
        *_period_filters(OutreachDraft.sent_at, start, end),
    ]

    drafts = (
        (
            await db.execute(
                select(OutreachDraft)
                .where(*sent_filters)
                .order_by(OutreachDraft.sent_at)
            )
        )
        .scalars()
        .all()
    )
    if not drafts:
        return []

    draft_ids = [d.id for d in drafts]
    opened_ids = set(
        (
            await db.execute(
                select(func.distinct(EmailOpenEvent.draft_id)).where(
                    EmailOpenEvent.draft_id.in_(draft_ids)
                )
            )
        ).scalars()
    )

    lead_ids = {d.lead_id for d in drafts}
    replied_lead_ids = set(
        (
            await db.execute(
                select(func.distinct(InboundMessage.lead_id)).where(
                    InboundMessage.lead_id.in_(lead_ids)
                )
            )
        ).scalars()
    )
    # "meetings" here means the redefined meetings_booked metric -- entered
    # Meeting Booked, not Hot. See get_overview's meetings_booked comment
    # and the module docstring's "Meetings booked" entry for why.
    meeting_booked_lead_ids = set(
        (
            await db.execute(
                select(func.distinct(PipelineEvent.lead_id)).where(
                    PipelineEvent.lead_id.in_(lead_ids),
                    PipelineEvent.to_stage == PipelineStage.MEETING_BOOKED,
                    PipelineEvent.from_stage != PipelineStage.MEETING_BOOKED,
                )
            )
        ).scalars()
    )
    won_lead_ids = set(
        (
            await db.execute(
                select(func.distinct(PipelineEvent.lead_id)).where(
                    PipelineEvent.lead_id.in_(lead_ids),
                    PipelineEvent.to_stage == PipelineStage.CONVERTED,
                )
            )
        ).scalars()
    )

    buckets: dict[CampaignType, dict[str, int]] = {}
    for draft in drafts:
        bucket = buckets.setdefault(
            draft.campaign_type, {"sent": 0, "opened": 0, "replied": 0, "meetings": 0, "won": 0}
        )
        bucket["sent"] += 1
        if draft.id in opened_ids:
            bucket["opened"] += 1
        if draft.lead_id in replied_lead_ids:
            bucket["replied"] += 1
        if draft.lead_id in meeting_booked_lead_ids:
            bucket["meetings"] += 1
        if draft.lead_id in won_lead_ids:
            bucket["won"] += 1

    results: list[VariantPerformance] = [
        VariantPerformance(
            variant_id=campaign_type.value,
            label=campaign_type.value,
            sent=counts["sent"],
            opened=counts["opened"],
            replied=counts["replied"],
            meetings_booked=counts["meetings"],
            deals_won=counts["won"],
        )
        for campaign_type, counts in buckets.items()
    ]

    logger.info("Computed campaign performance", extra={"buckets": len(results)})
    return results


async def get_language_performance(
    db: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[LanguagePerformance]:
    """Compute performance metrics grouped by each sent draft's language.

    Args:
        db: Active database session.
        start: Inclusive start of the reporting window, or None for all time.
        end: Exclusive end of the reporting window, or None for all time.

    Returns:
        List of :class:`LanguagePerformance` objects, ordered by drafts_sent desc.
    """
    filters = [
        OutreachDraft.status == DraftStatus.SENT,
        OutreachDraft.sent_at.is_not(None),
        *_period_filters(OutreachDraft.sent_at, start, end),
    ]

    sent_drafts = (
        (await db.execute(select(OutreachDraft).where(*filters)))
        .scalars()
        .all()
    )

    if not sent_drafts:
        return []

    draft_ids = [d.id for d in sent_drafts]
    lead_ids = list({d.lead_id for d in sent_drafts})

    opened_ids = set(
        (
            await db.execute(
                select(func.distinct(EmailOpenEvent.draft_id)).where(
                    EmailOpenEvent.draft_id.in_(draft_ids)
                )
            )
        ).scalars()
    )

    replied_lead_ids = set(
        (
            await db.execute(
                select(func.distinct(InboundMessage.lead_id)).where(
                    InboundMessage.lead_id.in_(lead_ids)
                )
            )
        ).scalars()
    )

    buckets: dict[str, dict[str, int]] = {}
    for draft in sent_drafts:
        lang = draft.draft_language or "en"
        bucket = buckets.setdefault(lang, {"sent": 0, "opened": 0, "replied": 0})
        bucket["sent"] += 1
        if draft.id in opened_ids:
            bucket["opened"] += 1
        if draft.lead_id in replied_lead_ids:
            bucket["replied"] += 1

    results: list[LanguagePerformance] = [
        LanguagePerformance(
            language=lang,
            drafts_sent=counts["sent"],
            opens=counts["opened"],
            open_rate=safe_rate(counts["opened"], counts["sent"]),
            replies=counts["replied"],
            reply_rate=safe_rate(counts["replied"], counts["sent"]),
        )
        for lang, counts in buckets.items()
    ]

    results.sort(key=lambda r: r.drafts_sent, reverse=True)
    logger.info("Computed language performance", extra={"languages": len(results)})
    return results
