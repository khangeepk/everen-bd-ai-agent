"""Derives the five lead-scoring components from real lead/audit/social data.

This is the DB-aware glue between stored records and the pure formula in
:mod:`app.services.lead_scoring`. Each ``assess_*`` function documents exactly
which fields it reads and why, since these heuristics are the part of the
scoring engine most likely to need tuning once real data is in.

None of this module decides who gets contacted -- it only produces the inputs
the formula combines. The compliance gate it derives
(:func:`assess_compliance`) is the one component that can force
``DO_NOT_CONTACT`` regardless of everything else; see
app/services/lead_scoring.py for why that is a gate rather than a weight.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.audit import AuditReport, WebsiteAudit
from app.db.models.knowledge_base import Service
from app.db.models.lead import Lead, LeadSource
from app.services.knowledge_base import KnowledgeBaseService
from app.services.lead_scoring import ComplianceGate, ComponentScore, ScoreBreakdown

logger = logging.getLogger(__name__)

#: EEA member states plus the UK, matched case-insensitively against
#: Lead.country as free text. Not exhaustive of every naming variant a rep
#: might type -- extend as real data surfaces gaps.
EEA_COUNTRIES: frozenset[str] = frozenset(
    {
        "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
        "czech republic", "denmark", "estonia", "finland", "france", "germany",
        "greece", "hungary", "iceland", "ireland", "italy", "latvia",
        "liechtenstein", "lithuania", "luxembourg", "malta", "netherlands",
        "norway", "poland", "portugal", "romania", "slovakia", "slovenia",
        "spain", "sweden", "united kingdom", "uk", "gb",
    }
)

#: Contact channel weights for the Contactability component. Sums to 1.0.
_CHANNEL_WEIGHTS: dict[str, float] = {
    "contact_email": 0.50,
    "contact_phone": 0.25,
    "linkedin_url": 0.15,
    "website": 0.10,
}

#: Blend of channel presence vs. the lead's own discovery confidence in the
#: Contactability score.
_CHANNEL_WEIGHT_IN_CONTACTABILITY = 0.70
_CONFIDENCE_WEIGHT_IN_CONTACTABILITY = 0.30

#: Blend of website health vs. social presence in the Need score.
_WEBSITE_WEIGHT_IN_NEED = 0.70
_SOCIAL_WEIGHT_IN_NEED = 0.30

#: Fit search returns this many candidate services; the top match's score
#: becomes the Fit component and its price informs Revenue.
_FIT_SEARCH_TOP_K = 5


async def _latest_audit(db: AsyncSession, lead_id) -> WebsiteAudit | None:
    """Fetch the most recent website audit for a lead.

    Args:
        db: Active database session.
        lead_id: The lead's identifier.

    Returns:
        The latest :class:`WebsiteAudit`, or None if none exists.
    """
    return (
        await db.execute(
            select(WebsiteAudit)
            .where(WebsiteAudit.lead_id == lead_id)
            .order_by(WebsiteAudit.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_social_score(db: AsyncSession, lead_id) -> float | None:
    """Fetch the most recent social presence score for a lead.

    Args:
        db: Active database session.
        lead_id: The lead's identifier.

    Returns:
        The latest recorded social score, or None if no report carries one.
    """
    return (
        await db.execute(
            select(AuditReport.social_score)
            .where(AuditReport.lead_id == lead_id, AuditReport.social_score.is_not(None))
            .order_by(AuditReport.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def assess_need(db: AsyncSession, lead: Lead) -> ComponentScore:
    """Estimate how much this business needs Everen's services.

    Need is modelled as the inverse of digital health: a struggling website
    or absent social presence signals unmet need, not disqualification. Blends
    the latest website audit (70%) and social presence score (30%) when both
    are available.

    Args:
        db: Active database session.
        lead: The lead to assess.

    Returns:
        The Need component. Neutral (0.5) when no audit or social data exists
        yet -- absence of data is not evidence of low need.
    """
    audit = await _latest_audit(db, lead.id)
    social_score = await _latest_social_score(db, lead.id)

    health = audit.health_score if audit is not None else None

    if health is None and social_score is None:
        return ComponentScore(
            value=0.5,
            reasons=("No website audit or social review on file yet; using a neutral estimate.",),
        )

    if health is not None and social_score is not None:
        combined_health = _WEBSITE_WEIGHT_IN_NEED * health + _SOCIAL_WEIGHT_IN_NEED * social_score
        reasons = (
            f"Website health score {health:.2f}, social presence score {social_score:.2f}.",
        )
    elif health is not None:
        combined_health = health
        reasons = (f"Website health score {health:.2f}; no social review on file.",)
    else:
        combined_health = social_score  # type: ignore[assignment]
        reasons = (f"Social presence score {social_score:.2f}; no website audit on file.",)

    need = round(min(max(1.0 - combined_health, 0.0), 1.0), 4)
    return ComponentScore(value=need, reasons=reasons)


async def assess_fit(
    db: AsyncSession, lead: Lead, kb: KnowledgeBaseService
) -> tuple[ComponentScore, list[Service]]:
    """Estimate how well the lead matches Everen's service offerings.

    Runs the lead's category and notes through the same knowledge-base
    similarity search used for RAG recommendations, so Fit reflects genuine
    semantic overlap rather than a fixed category lookup table.

    Args:
        db: Active database session.
        lead: The lead to assess.
        kb: Knowledge base service for similarity search.

    Returns:
        A tuple of the Fit component and the matched services (best first,
        empty if none matched), the latter reused by :func:`assess_revenue`.
    """
    query = " ".join(filter(None, [lead.category, lead.notes])).strip()
    if not query:
        return (
            ComponentScore(
                value=0.5,
                reasons=("No category or notes to match against; using a neutral estimate.",),
            ),
            [],
        )

    chunks = await kb.search(query, top_k=_FIT_SEARCH_TOP_K * 3)
    scored = KnowledgeBaseService.collapse_to_services(chunks)[:_FIT_SEARCH_TOP_K]
    if not scored:
        return (
            ComponentScore(value=0.0, reasons=("No matching service found in the knowledge base.",)),
            [],
        )

    rows = (
        (await db.execute(select(Service).where(Service.id.in_([entry.item for entry in scored]))))
        .scalars()
        .all()
    )
    by_id = {service.id: service for service in rows}
    matched = [by_id[entry.item] for entry in scored if entry.item in by_id]

    # Cosine similarity can be negative; Fit is not.
    top_score = max(scored[0].score, 0.0)
    reasons = tuple(
        f"Matches '{service.name}' ({service.category})" for service in matched[:3]
    ) or ("No confident match.",)

    return ComponentScore(value=round(top_score, 4), reasons=reasons), matched


def assess_contactability(lead: Lead) -> ComponentScore:
    """Estimate how reachable and verified the lead's contact info is.

    Blends channel completeness (which fields are populated, weighted by how
    directly usable each is for outreach) with the lead's own discovery
    confidence score.

    Args:
        lead: The lead to assess.

    Returns:
        The Contactability component.
    """
    channel_score = sum(
        weight for field, weight in _CHANNEL_WEIGHTS.items() if getattr(lead, field)
    )
    present = [field for field in _CHANNEL_WEIGHTS if getattr(lead, field)]

    combined = (
        _CHANNEL_WEIGHT_IN_CONTACTABILITY * channel_score
        + _CONFIDENCE_WEIGHT_IN_CONTACTABILITY * float(lead.confidence_score or 0.0)
    )

    if not present:
        reasons = ("No contact channel on file.",)
    else:
        reasons = (f"Available channels: {', '.join(present)}.",)

    return ComponentScore(value=round(min(max(combined, 0.0), 1.0), 4), reasons=reasons)


def assess_revenue(matched_services: list[Service]) -> ComponentScore:
    """Estimate deal-size potential from the best-matched service's pricing.

    Revenue is a proxy, not a forecast: it normalizes the top-matched
    service's price midpoint against the configured deal-size scale
    (``settings.lead_score_revenue_scale_min/max``). A lead matching only
    custom-quoted services, or none at all, gets a neutral estimate rather
    than a fabricated number.

    Args:
        matched_services: Services from :func:`assess_fit`, best first.

    Returns:
        The Revenue component.
    """
    if not matched_services:
        return ComponentScore(
            value=0.5, reasons=("No matched service to estimate deal size from.",)
        )

    top = matched_services[0]
    if top.price_min is None and top.price_max is None:
        return ComponentScore(
            value=0.5,
            reasons=(f"'{top.name}' is custom-quoted; using a neutral estimate.",),
        )

    low = float(top.price_min) if top.price_min is not None else float(top.price_max)
    high = float(top.price_max) if top.price_max is not None else float(top.price_min)
    midpoint = (low + high) / 2.0

    scale_min = settings.lead_score_revenue_scale_min
    scale_max = settings.lead_score_revenue_scale_max
    if scale_max <= scale_min:
        logger.warning("Revenue scale misconfigured: max <= min")
        return ComponentScore(value=0.5, reasons=("Revenue scale misconfigured.",))

    normalized = (midpoint - scale_min) / (scale_max - scale_min)
    value = round(min(max(normalized, 0.0), 1.0), 4)

    return ComponentScore(
        value=value,
        reasons=(f"Best-fit service '{top.name}' is typically priced around ${midpoint:,.0f}.",),
    )


def assess_compliance(lead: Lead) -> tuple[ComponentScore, ComplianceGate]:
    """Assess residual compliance risk and whether the hard gate triggers.

    The gate triggers on the explicit ``do_not_contact`` flag alone -- it does
    not infer suppression from other fields, so it only ever fires on a
    deliberate signal. Residual risk (the 10%-weighted score) is reduced when
    no lawful basis is recorded, more sharply so for leads in EEA/UK
    jurisdictions where that gap is a GDPR exposure rather than a nice-to-have.

    Args:
        lead: The lead to assess.

    Returns:
        A tuple of the residual-risk component and the gate decision.
    """
    if lead.do_not_contact:
        reason = lead.do_not_contact_reason or "Lead is flagged do-not-contact."
        return (
            ComponentScore(value=0.0, reasons=(reason,)),
            ComplianceGate(triggered=True, reasons=(reason,)),
        )

    score = 1.0
    reasons: list[str] = []

    is_eea = bool(lead.country) and lead.country.strip().lower() in EEA_COUNTRIES

    if lead.consent_basis is None:
        if is_eea:
            score -= 0.5
            reasons.append(
                f"No lawful basis on file for a lead in {lead.country}, an EEA/UK jurisdiction."
            )
        else:
            score -= 0.2
            reasons.append("No lawful basis on file.")
    else:
        reasons.append(f"Lawful basis recorded: {lead.consent_basis}.")

    if lead.source is LeadSource.GOOGLE_PLACES and lead.consent_basis is None:
        score -= 0.1
        reasons.append(
            "Sourced via Google Places discovery; contact data provenance should be verified."
        )

    value = round(min(max(score, 0.0), 1.0), 4)
    return (
        ComponentScore(value=value, reasons=tuple(reasons) or ("No compliance concerns noted.",)),
        ComplianceGate(triggered=False),
    )


async def build_score_breakdown(
    db: AsyncSession, lead: Lead, kb: KnowledgeBaseService
) -> ScoreBreakdown:
    """Assemble the full five-component breakdown for a lead.

    Args:
        db: Active database session.
        lead: The lead to score.
        kb: Knowledge base service for the Fit similarity search.

    Returns:
        A :class:`ScoreBreakdown` ready for :func:`app.services.lead_scoring.score_lead`.
    """
    need = await assess_need(db, lead)
    fit, matched_services = await assess_fit(db, lead, kb)
    contactability = assess_contactability(lead)
    revenue = assess_revenue(matched_services)
    compliance, gate = assess_compliance(lead)

    return ScoreBreakdown(
        need=need,
        fit=fit,
        contactability=contactability,
        revenue=revenue,
        compliance=compliance,
        gate=gate,
    )
