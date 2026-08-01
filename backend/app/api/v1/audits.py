"""Website audit and social review routes.

Audits are triggered by a BD rep against a specific lead. That is deliberate:
the audit crawls a third party's website, and a human-initiated, attributable
request is what keeps that defensible. There is no bulk or automatic path.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auditor import AGENT_NAME, WebsiteAuditAgent
from app.api.deps import get_current_user, require_write_access
from app.db.base import utcnow
from app.db.models.audit import (
    AuditFinding,
    AuditReport,
    AuditStatus,
    SocialProfileReview,
    WebsiteAudit,
)
from app.db.models.lead import Lead
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.audit import (
    AuditDetailResponse,
    AuditRequest,
    AuditResponse,
    FindingResponse,
    ReportResponse,
    SocialReviewRequest,
    SocialReviewResponse,
)
from app.services.audit_scoring import FindingCategory
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService
from app.services.social_review import ProfileChecklist, score_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audits", tags=["audits"])


def get_audit_agent(db: AsyncSession = Depends(get_db)) -> WebsiteAuditAgent:
    """Construct an audit agent for the request.

    Args:
        db: Active database session.

    Returns:
        A configured :class:`WebsiteAuditAgent`.
    """
    kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
    return WebsiteAuditAgent(db=db, kb=kb)


async def _load_checklists(db: AsyncSession, lead_id: uuid.UUID) -> list[ProfileChecklist]:
    """Load stored social reviews for a lead as checklists.

    Args:
        db: Active database session.
        lead_id: The lead to load reviews for.

    Returns:
        One checklist per reviewed platform.
    """
    rows = (
        (
            await db.execute(
                select(SocialProfileReview).where(SocialProfileReview.lead_id == lead_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        ProfileChecklist(
            platform=row.platform,
            profile_url=row.profile_url,
            profile_exists=row.profile_exists,
            has_profile_image=row.has_profile_image,
            has_cover_image=row.has_cover_image,
            has_description=row.has_description,
            has_website_link=row.has_website_link,
            has_contact_details=row.has_contact_details,
            cadence=row.cadence,
            follower_band=row.follower_band,
            reviewer_notes=row.reviewer_notes,
        )
        for row in rows
    ]


@router.post(
    "",
    response_model=AuditDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a website audit",
    description=(
        "Runs PageSpeed Insights, an SSL check, contact form detection, and a "
        "bounded robots-respecting link crawl, then generates a business-friendly "
        "report mapped to Everen Techno services. Produces a document only; "
        "nothing is sent to the prospect."
    ),
)
async def run_audit(
    payload: AuditRequest,
    agent: WebsiteAuditAgent = Depends(get_audit_agent),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> AuditDetailResponse:
    """Run a full website audit and generate its report.

    Args:
        payload: The audit request.
        agent: The audit agent.
        db: Active database session.
        user: The authenticated caller, recorded as the requester.

    Returns:
        The audit, its findings, and the generated report.

    Raises:
        HTTPException: 404 if ``lead_id`` references a missing lead, 400 for an
            unusable URL.
    """
    if payload.lead_id is not None and await db.get(Lead, payload.lead_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    audit = WebsiteAudit(
        lead_id=payload.lead_id,
        url=str(payload.url),
        status=AuditStatus.RUNNING,
        requested_by_id=user.id,
        started_at=utcnow(),
    )
    db.add(audit)
    await db.flush()

    checklists = (
        await _load_checklists(db, payload.lead_id)
        if payload.include_social and payload.lead_id is not None
        else []
    )

    try:
        outcome = await agent.run_audit(str(payload.url), checklists)
    except ValueError as exc:
        audit.status = AuditStatus.FAILED
        audit.error_detail = str(exc)
        audit.completed_at = utcnow()
        await db.flush()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit.performance_score = outcome.category_scores.get(FindingCategory.PERFORMANCE)
    audit.seo_score = outcome.category_scores.get(FindingCategory.SEO)
    audit.accessibility_score = outcome.category_scores.get(FindingCategory.ACCESSIBILITY)
    audit.best_practices_score = outcome.category_scores.get(FindingCategory.BEST_PRACTICES)
    audit.mobile_score = outcome.category_scores.get(FindingCategory.MOBILE)
    audit.health_score = outcome.health

    if outcome.ssl is not None:
        audit.ssl_valid = outcome.ssl.valid
        audit.ssl_expires_at = outcome.ssl.expires_at
    if outcome.contact_form is not None:
        audit.contact_form_found = outcome.contact_form.form_found
        audit.contact_form_reachable = outcome.contact_form.endpoint_reachable
    if outcome.crawl is not None:
        audit.pages_crawled = outcome.crawl.pages_crawled
        audit.links_checked = outcome.crawl.links_checked
        audit.broken_link_count = len(outcome.crawl.broken_links)
        audit.robots_blocked = outcome.crawl.robots_blocked

    audit.error_detail = "\n".join(outcome.errors) or None
    audit.status = (
        AuditStatus.ROBOTS_BLOCKED
        if outcome.crawl is not None and outcome.crawl.robots_blocked
        else AuditStatus.COMPLETED
    )

    services = await agent.map_findings_to_services(outcome.findings)
    service_by_category = {
        category: matches[0].id for category, matches in services.items() if matches
    }

    for finding in outcome.findings:
        db.add(
            AuditFinding(
                audit_id=audit.id,
                code=finding.code,
                category=finding.category,
                severity=finding.severity,
                title=finding.title,
                detail=finding.detail,
                evidence="\n".join(finding.evidence) if finding.evidence else None,
                score=finding.score,
                mapped_service_id=service_by_category.get(finding.category),
            )
        )

    generated = await agent.generate_report(outcome, services)
    report = AuditReport(
        audit_id=audit.id,
        lead_id=payload.lead_id,
        headline=generated.headline,
        summary=generated.summary,
        body_markdown=generated.body_markdown,
        generated_by_agent=AGENT_NAME,
        used_fallback=generated.used_fallback,
        social_score=outcome.social_score,
    )
    db.add(report)

    audit.completed_at = utcnow()
    await db.flush()

    logger.info(
        "Audit stored",
        extra={
            "audit_id": str(audit.id),
            "user_id": str(user.id),
            "findings": len(outcome.findings),
            "used_fallback": generated.used_fallback,
        },
    )

    findings = (
        (
            await db.execute(
                select(AuditFinding).where(AuditFinding.audit_id == audit.id)
            )
        )
        .scalars()
        .all()
    )

    return AuditDetailResponse(
        audit=AuditResponse.model_validate(audit),
        findings=[FindingResponse.model_validate(row) for row in findings],
        report=ReportResponse.model_validate(report),
    )


@router.get(
    "/{audit_id}",
    response_model=AuditDetailResponse,
    summary="Get an audit",
    description="Retrieves an audit with its findings and report.",
)
async def get_audit(
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AuditDetailResponse:
    """Retrieve one audit.

    Args:
        audit_id: Identifier of the audit.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The audit, its findings, and its report if one exists.

    Raises:
        HTTPException: 404 if no such audit exists.
    """
    audit = await db.get(WebsiteAudit, audit_id)
    if audit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit not found")

    findings = (
        (await db.execute(select(AuditFinding).where(AuditFinding.audit_id == audit_id)))
        .scalars()
        .all()
    )
    report = (
        await db.execute(select(AuditReport).where(AuditReport.audit_id == audit_id))
    ).scalar_one_or_none()

    return AuditDetailResponse(
        audit=AuditResponse.model_validate(audit),
        findings=[FindingResponse.model_validate(row) for row in findings],
        report=ReportResponse.model_validate(report) if report is not None else None,
    )


@router.put(
    "/leads/{lead_id}/social",
    response_model=SocialReviewResponse,
    summary="Record a social profile review",
    description=(
        "Stores a human reviewer's observations about one public social profile. "
        "No scraping: a reviewer looks at the public page and fills this in. "
        "Upserts on (lead, platform)."
    ),
)
async def upsert_social_review(
    lead_id: uuid.UUID,
    payload: SocialReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
) -> SocialReviewResponse:
    """Create or update a social profile review.

    Args:
        lead_id: The lead the profile belongs to.
        payload: The reviewer's observations.
        db: Active database session.
        user: The authenticated reviewer.

    Returns:
        The stored review with its completeness score.

    Raises:
        HTTPException: 404 if the lead does not exist.
    """
    if await db.get(Lead, lead_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    checklist = ProfileChecklist(
        platform=payload.platform,
        profile_url=str(payload.profile_url) if payload.profile_url else None,
        profile_exists=payload.profile_exists,
        has_profile_image=payload.has_profile_image,
        has_cover_image=payload.has_cover_image,
        has_description=payload.has_description,
        has_website_link=payload.has_website_link,
        has_contact_details=payload.has_contact_details,
        cadence=payload.cadence,
        follower_band=payload.follower_band,
        reviewer_notes=payload.reviewer_notes,
    )
    score = score_profile(checklist)

    existing = (
        await db.execute(
            select(SocialProfileReview).where(
                SocialProfileReview.lead_id == lead_id,
                SocialProfileReview.platform == payload.platform,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = SocialProfileReview(lead_id=lead_id, platform=payload.platform)
        db.add(existing)

    for attribute, value in payload.model_dump(exclude={"platform", "profile_url"}).items():
        setattr(existing, attribute, value)
    existing.profile_url = str(payload.profile_url) if payload.profile_url else None
    existing.completeness_score = score
    existing.reviewed_by_id = user.id
    existing.reviewed_at = utcnow()

    await db.flush()

    logger.info(
        "Social review recorded",
        extra={
            "lead_id": str(lead_id),
            "platform": payload.platform.value,
            "score": score,
            "reviewer_id": str(user.id),
        },
    )
    return SocialReviewResponse.model_validate(existing)
