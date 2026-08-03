"""Seed realistic local demo data across the whole funnel.

app/db/seed.py only seeds the Services Knowledge Base -- it creates zero
leads, audits, scores, or drafts, so a fresh local database has nothing for
the dashboard, Kanban, approvals queue, or LinkedIn queue to show even once
the frontend can reach the API. This script fills that gap for LOCAL
DEVELOPMENT ONLY.

Deliberately network-free: no OpenAI, Google Places, or PageSpeed calls.
Draft bodies use the same fallback-content generators the real
OutreachDraftAgent falls back to when the LLM is unavailable
(app/agents/outreach.py's _fallback_* methods, reimplemented minimally here
rather than imported, since the agent's public generate() methods require a
live KnowledgeBaseService/embedder) -- realistic-shaped text, not Lorem
Ipsum, so the UI reads the way it will in production.

Run with (from backend/, with DATABASE_URL pointed at your local Postgres
and migrations already applied)::

    python -m scripts.seed_demo_data

Idempotent: skips creation if demo leads (source_detail="demo_seed_v1")
already exist, so re-running against the same database is a no-op rather
than duplicating rows. Pass --reset to delete and recreate the demo batch.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.models.audit import AuditFinding, AuditStatus, WebsiteAudit
from app.db.models.lead import Lead, LeadSource, LeadStatus
from app.db.models.lead_score import LeadScore
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import PipelineEvent
from app.db.models.user import User, UserRole
from app.db.session import SessionFactory
from app.services.audit_scoring import FindingCategory, Severity
from app.services.lead_scoring import ComplianceGate, ComponentScore, ScoreBreakdown, score_lead
from app.services.outreach_policy import CampaignType, OutreachChannel
from app.services.pipeline import PipelineStage, PipelineTransitionReason

logger = logging.getLogger(__name__)

#: Marks every row this script creates, so it can find (and, with --reset,
#: delete) exactly its own batch without touching anything a real user or
#: another test created.
DEMO_MARKER = "demo_seed_v1"

#: Matches app.core.dev_auth.mint_dev_session_token's default subject, so
#: the user row this script creates is the SAME row get_current_user's
#: just-in-time provisioning would otherwise create on first real request --
#: approved_by/computed_by references below resolve to a real, already-
#: familiar local user rather than a throwaway one nobody recognizes in the
#: UI.
DEV_USER_SUBJECT = "dev-local-user"

_CATEGORIES = ["Restaurants", "Home Services", "Retail", "Healthcare", "Legal", "Real Estate"]
_CITIES = [
    ("Dallas", "TX"), ("Austin", "TX"), ("Denver", "CO"), ("Phoenix", "AZ"),
    ("Columbus", "OH"), ("Charlotte", "NC"), ("Nashville", "TN"), ("Portland", "OR"),
]
_COMPANY_NOUNS = [
    "Bistro", "Plumbing Co", "Boutique", "Family Clinic", "Law Group", "Realty",
    "Auto Repair", "Dental Care", "Fitness Studio", "Roofing", "Bakery", "Salon",
]

_rng = random.Random(20260803)  # fixed seed -- reproducible demo batch


def _company_name(i: int) -> str:
    city, _ = _CITIES[i % len(_CITIES)]
    noun = _COMPANY_NOUNS[i % len(_COMPANY_NOUNS)]
    return f"{city} {noun} #{i + 1}"


async def _get_or_create_dev_user(db) -> User:
    """Return the local dev user row, creating it if this is a fresh database.

    Args:
        db: Active session.

    Returns:
        The persisted dev User row (role=ADMIN, matches the dev auth token's
        default subject/role so it lines up with whatever the frontend's
        dev session later provisions).
    """
    existing = (
        await db.execute(select(User).where(User.provider_subject == DEV_USER_SUBJECT))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(
        provider_subject=DEV_USER_SUBJECT,
        provider="dev-local",
        email="dev@localhost",
        full_name="Local Dev User",
        role=UserRole.ADMIN,
    )
    db.add(user)
    await db.flush()
    logger.info("Created local dev user", extra={"user_id": str(user.id)})
    return user


def _make_score_breakdown(*, tier: str) -> ScoreBreakdown:
    """Build a plausible ScoreBreakdown for one of three quality tiers.

    Args:
        tier: "hot", "warm", or "cold" -- biases the component values so the
            resulting weighted total lands in roughly the right band.

    Returns:
        A ScoreBreakdown with realistic-sounding evidence reasons.
    """
    bands = {
        "hot": (0.75, 0.95),
        "warm": (0.5, 0.74),
        "cold": (0.15, 0.45),
    }
    lo, hi = bands[tier]

    def val() -> float:
        return round(_rng.uniform(lo, hi), 2)

    return ScoreBreakdown(
        need=ComponentScore(
            val(), ("Site audit found 3 unresolved performance issues", "No online booking form")
        ),
        fit=ComponentScore(val(), ("Category matches Managed Engineering Retainer offering",)),
        contactability=ComponentScore(
            val(), ("Verified email on file", "LinkedIn profile present")
        ),
        revenue=ComponentScore(val(), ("Mid-size local business, 10-50 employees (estimated)",)),
        compliance=ComponentScore(val(), ("No suppression flags", "Consent basis on file")),
        gate=ComplianceGate(triggered=False),
    )


async def _seed_lead(db, i: int, dev_user: User) -> Lead:
    """Create one demo lead with a realistic pipeline position.

    Args:
        db: Active session.
        i: Index in the batch, used to vary category/city/stage/tier.
        dev_user: Attributed as the actor for pipeline events.

    Returns:
        The persisted Lead.
    """
    city, state = _CITIES[i % len(_CITIES)]
    category = _CATEGORIES[i % len(_CATEGORIES)]
    name = _company_name(i)

    # Distribute across the funnel: ~15% won, ~10% lost, ~20% hot-in-progress,
    # ~25% contacted/warm, ~30% brand new -- deliberately not a uniform split,
    # so the funnel visually narrows the way a real one does.
    bucket = i % 20
    if bucket < 3:
        status, stage, tier = LeadStatus.WON, PipelineStage.CONVERTED, "hot"
    elif bucket < 5:
        status, stage, tier = LeadStatus.LOST, PipelineStage.LOST, "cold"
    elif bucket < 9:
        status, stage, tier = LeadStatus.RESPONDED, PipelineStage.HOT, "hot"
    elif bucket < 14:
        status, stage, tier = LeadStatus.CONTACTED, PipelineStage.CONTACTED, "warm"
    else:
        status, stage, tier = LeadStatus.NEW, PipelineStage.NEW, "cold"

    lead = Lead(
        name=name,
        category=category,
        contact_name=f"Contact {i + 1}",
        contact_title="Owner",
        website=f"https://example-{i + 1}.test",
        linkedin_url=f"https://www.linkedin.com/company/example-{i + 1}" if i % 3 == 0 else None,
        country="US",
        source=LeadSource.GOOGLE_PLACES if i % 2 == 0 else LeadSource.WEB_RESEARCH,
        source_detail=DEMO_MARKER,
        confidence_score=round(_rng.uniform(0.55, 0.95), 2),
        status=status,
        pipeline_stage=stage,
        campaign_type=CampaignType.COLD,
        notes=f"{city}, {state} -- demo seed record.",
    )
    lead.set_contact_email(f"owner{i + 1}@example-{i + 1}.test", verified=True)
    db.add(lead)
    await db.flush()

    db.add(
        PipelineEvent(
            lead_id=lead.id,
            from_stage=None,
            to_stage=PipelineStage.NEW,
            reason=PipelineTransitionReason.MANUAL,
            triggered_by_id=dev_user.id,
            changed_at=datetime.now(timezone.utc) - timedelta(days=_rng.randint(1, 30)),
            note="Demo seed: lead created.",
        )
    )
    if stage != PipelineStage.NEW:
        db.add(
            PipelineEvent(
                lead_id=lead.id,
                from_stage=PipelineStage.NEW,
                to_stage=stage,
                reason=PipelineTransitionReason.REPLY_CLASSIFIED,
                triggered_by_id=dev_user.id,
                changed_at=datetime.now(timezone.utc) - timedelta(days=_rng.randint(0, 15)),
                note="Demo seed: advanced for a realistic funnel shape.",
            )
        )

    # Score every lead except brand-new ones with no activity yet (mirrors
    # real behavior: scoring runs after some enrichment/audit signal exists).
    if status != LeadStatus.NEW or i % 4 == 0:
        breakdown = _make_score_breakdown(tier=tier)
        result = score_lead(breakdown)
        db.add(
            LeadScore(
                lead_id=lead.id,
                need_score=breakdown.need.value,
                fit_score=breakdown.fit.value,
                contactability_score=breakdown.contactability.value,
                revenue_score=breakdown.revenue.value,
                compliance_score=breakdown.compliance.value,
                need_reasons="\n".join(breakdown.need.reasons),
                fit_reasons="\n".join(breakdown.fit.reasons),
                contactability_reasons="\n".join(breakdown.contactability.reasons),
                revenue_reasons="\n".join(breakdown.revenue.reasons),
                compliance_reasons="\n".join(breakdown.compliance.reasons),
                gate_triggered=result.breakdown.gate.triggered,
                gate_reasons="\n".join(result.breakdown.gate.reasons) or None,
                total_score=result.total_score,
                label=result.label,
                formula_version=result.formula_version,
                computed_by_id=dev_user.id,
                computed_at=datetime.now(timezone.utc) - timedelta(days=_rng.randint(0, 10)),
            )
        )

    # Audit + findings for about a third of leads -- enough to populate audit
    # views without every single lead having one (matches real usage, where
    # audits are triggered selectively, not automatically for every lead).
    if i % 3 == 0:
        completed_at = datetime.now(timezone.utc) - timedelta(days=_rng.randint(1, 20))
        started_at = completed_at - timedelta(minutes=_rng.randint(5, 90))
        audit = WebsiteAudit(
            lead_id=lead.id,
            url=lead.website or "https://example.test",
            status=AuditStatus.COMPLETED,
            requested_by_id=dev_user.id,
            started_at=started_at,
            completed_at=completed_at,
            performance_score=round(_rng.uniform(0.2, 0.9), 2),
            seo_score=round(_rng.uniform(0.3, 0.85), 2),
            accessibility_score=round(_rng.uniform(0.4, 0.9), 2),
            best_practices_score=round(_rng.uniform(0.4, 0.9), 2),
            mobile_score=round(_rng.uniform(0.3, 0.9), 2),
            ssl_valid=i % 5 != 0,
            contact_form_found=i % 4 != 0,
            contact_form_reachable=i % 4 != 0,
            pages_crawled=_rng.randint(3, 20),
            links_checked=_rng.randint(10, 80),
            broken_link_count=_rng.randint(0, 4),
            robots_blocked=False,
            health_score=round(_rng.uniform(0.3, 0.85), 2),
        )
        db.add(audit)
        await db.flush()

        _FINDINGS = [
            (
                "slow_lcp", FindingCategory.PERFORMANCE, Severity.HIGH,
                "Homepage loads slowly on mobile",
                "Largest Contentful Paint is over 4.5s on a mid-tier mobile device, "
                "well past Google's 2.5s 'good' threshold.",
            ),
            (
                "no_meta_description", FindingCategory.SEO, Severity.MEDIUM,
                "Missing meta description on key pages",
                "3 of the top 5 crawled pages have no meta description, hurting "
                "click-through from search results.",
            ),
            (
                "contact_form_broken", FindingCategory.CONTACT_FORM, Severity.CRITICAL,
                "Contact form does not submit",
                "Submitting the contact form returns a 500 error -- the business "
                "is very likely losing inbound leads silently.",
            ),
            (
                "no_ssl", FindingCategory.SECURITY, Severity.CRITICAL,
                "Site is not served over HTTPS",
                "Browsers show a 'Not Secure' warning, which erodes visitor trust "
                "and hurts search ranking.",
            ),
            (
                "not_mobile_friendly", FindingCategory.MOBILE, Severity.HIGH,
                "Layout breaks on mobile viewports",
                "Text and buttons overlap below 400px width -- roughly 60% of "
                "local search traffic is mobile.",
            ),
        ]
        chosen = _rng.sample(_FINDINGS, k=_rng.randint(2, 4))
        for code, category, severity, title, detail in chosen:
            db.add(
                AuditFinding(
                    audit_id=audit.id,
                    code=code,
                    category=category,
                    severity=severity,
                    title=title,
                    detail=detail,
                    evidence="Automated demo-seed finding (no live crawl was performed).",
                    score=round(_rng.uniform(0.1, 0.7), 2),
                )
            )

    return lead


def _fallback_email_body(lead: Lead) -> tuple[str, str]:
    """Build a realistic-shaped (but hand-written, not LLM) email draft.

    Args:
        lead: The lead this draft targets.

    Returns:
        (subject, body) tuple.
    """
    subject = f"Quick note about {lead.name}'s website"
    body = (
        f"Hi {lead.contact_name or 'there'},\n\n"
        f"I took a look at {lead.name}'s website and noticed a couple of things "
        "that are probably costing you inquiries -- slow load time on mobile and "
        "a contact form that doesn't seem to submit reliably.\n\n"
        "We help local businesses fix exactly this kind of thing, usually within "
        "a few weeks. Worth a 15-minute call to see if it's a fit?\n\n"
        "Best,\nEveren Techno BD Team"
    )
    return subject, body


async def _seed_drafts_for_lead(db, lead: Lead, dev_user: User, i: int) -> None:
    """Create 0-2 outreach drafts in varied statuses for one lead.

    Args:
        db: Active session.
        lead: The lead the draft(s) target.
        dev_user: Attributed as approver for approved/sent drafts.
        i: Batch index, used to vary channel/status.
    """
    if lead.status == LeadStatus.NEW and i % 4 != 0:
        return  # Most brand-new leads have no drafts yet -- realistic.

    subject, body = _fallback_email_body(lead)
    now = datetime.now(timezone.utc)

    draft_bucket = i % 5
    if draft_bucket == 0:
        draft_status, approved, sent = DraftStatus.PENDING_REVIEW, False, False
    elif draft_bucket == 1:
        draft_status, approved, sent = DraftStatus.APPROVED, True, False
    elif draft_bucket == 2:
        draft_status, approved, sent = DraftStatus.SENT, True, True
    elif draft_bucket == 3:
        draft_status, approved, sent = DraftStatus.REJECTED, False, False
    else:
        draft_status, approved, sent = DraftStatus.PENDING_REVIEW, False, False

    email_draft = OutreachDraft(
        lead_id=lead.id,
        channel=OutreachChannel.EMAIL,
        status=draft_status,
        subject=subject,
        body=body,
        recipient_email=lead.contact_email,
        sender_name=settings.outreach_from_name,
        sender_email=settings.outreach_from_email,
        sender_company=settings.outreach_company_name,
        sender_physical_address=(
            settings.outreach_physical_address
            if settings.outreach_physical_address != "REPLACE_ME"
            else "123 Demo Street, Austin, TX 78701"
        ),
        unsubscribe_url=f"{settings.outreach_public_base_url}/unsubscribe/demo-{i}",
        campaign_type=CampaignType.COLD,
        created_by_agent="demo_seed",
        used_fallback=True,
        approved_by_id=dev_user.id if approved else None,
        approved_at=now - timedelta(days=1) if approved else None,
        rejected_reason="Not a great fit for this quarter's focus." if draft_status == DraftStatus.REJECTED else None,
        sent_at=now - timedelta(hours=_rng.randint(1, 72)) if sent else None,
        provider_message_id=f"demo-msg-{i}" if sent else None,
    )
    db.add(email_draft)

    # Give every 3rd lead a LinkedIn draft too, so the LinkedIn queue page
    # (which filters channel=linkedin) has real pending items to show.
    if lead.linkedin_url and i % 3 == 0:
        note = (
            f"Hi {lead.contact_name or 'there'}, I help local {lead.category or 'businesses'} "
            f"like {lead.name} fix website issues that cost them leads. Mind connecting?"
        )
        follow_up = (
            f"Thanks for connecting! Following up on {lead.name} -- happy to share what "
            "I found on your site whenever's convenient."
        )
        db.add(
            OutreachDraft(
                lead_id=lead.id,
                channel=OutreachChannel.LINKEDIN,
                status=DraftStatus.PENDING_REVIEW,
                subject=None,
                body=note,
                linkedin_followup_message=follow_up,
                campaign_type=CampaignType.COLD,
                created_by_agent="demo_seed",
                used_fallback=True,
            )
        )


async def _demo_batch_exists(db) -> bool:
    """Whether a previous run of this script already seeded data.

    Args:
        db: Active session.

    Returns:
        True if any lead carries the demo marker.
    """
    existing = (
        await db.execute(select(Lead.id).where(Lead.source_detail == DEMO_MARKER).limit(1))
    ).scalar_one_or_none()
    return existing is not None


async def _reset_demo_batch(db) -> None:
    """Delete every row this script previously created.

    Args:
        db: Active session.
    """
    demo_lead_ids = (
        await db.execute(select(Lead.id).where(Lead.source_detail == DEMO_MARKER))
    ).scalars().all()
    if not demo_lead_ids:
        return
    # ON DELETE CASCADE on the FK side handles scores/audits/drafts/events.
    await db.execute(delete(Lead).where(Lead.id.in_(demo_lead_ids)))
    await db.commit()
    logger.info("Deleted previous demo batch", extra={"lead_count": len(demo_lead_ids)})


async def seed_demo_data(*, count: int = 40, reset: bool = False) -> None:
    """Seed a realistic demo funnel: leads, scores, audits, and drafts.

    Args:
        count: How many demo leads to create.
        reset: If True, delete any previous demo batch first.
    """
    async with SessionFactory() as db:
        if reset:
            await _reset_demo_batch(db)

        if await _demo_batch_exists(db):
            logger.info(
                "Demo batch already present; skipping (pass --reset to recreate)"
            )
            return

        dev_user = await _get_or_create_dev_user(db)

        leads: list[Lead] = []
        for i in range(count):
            lead = await _seed_lead(db, i, dev_user)
            leads.append(lead)

        for i, lead in enumerate(leads):
            await _seed_drafts_for_lead(db, lead, dev_user, i)

        await db.commit()
        logger.info("Demo data seeded", extra={"leads": len(leads)})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=40, help="Number of demo leads to create.")
    parser.add_argument(
        "--reset", action="store_true", help="Delete any previous demo batch before seeding."
    )
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging()
    args = _parse_args()
    if settings.is_production:
        raise SystemExit("Refusing to run demo data seeding against APP_ENV=production.")
    asyncio.run(seed_demo_data(count=args.count, reset=args.reset))
