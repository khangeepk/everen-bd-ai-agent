"""Tests for :mod:`app.services.lead_signals`.

Exercises the DB-aware component extractors against an in-memory SQLite
session and the deterministic fake embedder from conftest -- no network calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import utcnow
from app.db.models.audit import AuditReport, AuditStatus, WebsiteAudit
from app.db.models.knowledge_base import PricingModel, Service
from app.db.models.lead import Lead, LeadSource
from app.services.knowledge_base import KnowledgeBaseService
from app.services.lead_scoring import ScoreLabel, score_lead
from app.services.lead_signals import (
    assess_compliance,
    assess_contactability,
    assess_fit,
    assess_need,
    assess_revenue,
    build_score_breakdown,
)


def _lead(**overrides: object) -> Lead:
    """Build a Lead fixture with sensible defaults.

    Args:
        **overrides: Fields to replace.

    Returns:
        An unsaved :class:`Lead`.
    """
    defaults: dict[str, object] = {"name": "Acme Dental", "source": LeadSource.MANUAL}
    defaults.update(overrides)
    return Lead(**defaults)


# ---------------------------------------------------------------------------
# Contactability -- synchronous, no DB round trip required.
# ---------------------------------------------------------------------------


def test_full_contact_channels_score_highest() -> None:
    """A lead with every channel and full confidence scores near 1.0."""
    lead = _lead(
        contact_email="ops@acme.example",
        contact_phone="555-0100",
        linkedin_url="https://linkedin.com/company/acme",
        website="https://acme.example",
        confidence_score=1.0,
    )
    assert assess_contactability(lead).value == pytest.approx(1.0)


def test_no_contact_channels_scores_zero_with_zero_confidence() -> None:
    """No channels and no confidence gives 0.0, not a hidden default credit."""
    lead = _lead(confidence_score=0.0)
    result = assess_contactability(lead)

    assert result.value == 0.0
    assert "No contact channel" in result.reasons[0]


def test_email_weighs_more_than_linkedin_alone() -> None:
    """Email (0.5 weight) outscores LinkedIn alone (0.15 weight)."""
    with_email = assess_contactability(_lead(contact_email="a@b.example")).value
    with_linkedin = assess_contactability(
        _lead(linkedin_url="https://linkedin.com/company/x")
    ).value

    assert with_email > with_linkedin


def test_confidence_score_contributes_even_with_no_channels() -> None:
    """The lead's own discovery confidence still contributes 30% of the blend."""
    result = assess_contactability(_lead(confidence_score=1.0))
    assert result.value == pytest.approx(0.30)


def test_contactability_never_exceeds_one() -> None:
    """The blend is capped at 1.0."""
    lead = _lead(
        contact_email="a@b.example",
        contact_phone="555",
        linkedin_url="https://linkedin.com/x",
        website="https://x.example",
        confidence_score=1.0,
    )
    assert assess_contactability(lead).value <= 1.0


# ---------------------------------------------------------------------------
# Need -- reads the latest audit and social score.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_need_is_neutral_with_no_audit_or_social(db_session: AsyncSession) -> None:
    """No audit and no social review yields a neutral estimate, not zero."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    result = await assess_need(db_session, lead)

    assert result.value == 0.5
    assert "neutral" in result.reasons[0].lower()


@pytest.mark.asyncio
async def test_need_is_inverse_of_website_health(db_session: AsyncSession) -> None:
    """A struggling website (low health) implies high need."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        WebsiteAudit(
            lead_id=lead.id,
            url="https://acme.example",
            status=AuditStatus.COMPLETED,
            health_score=0.2,
        )
    )
    await db_session.flush()

    result = await assess_need(db_session, lead)
    assert result.value == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_need_uses_the_most_recent_audit(db_session: AsyncSession) -> None:
    """When multiple audits exist, the latest one drives Need."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        WebsiteAudit(
            lead_id=lead.id,
            url="https://acme.example",
            status=AuditStatus.COMPLETED,
            health_score=0.9,
        )
    )
    await db_session.flush()
    db_session.add(
        WebsiteAudit(
            lead_id=lead.id,
            url="https://acme.example",
            status=AuditStatus.COMPLETED,
            health_score=0.1,
        )
    )
    await db_session.flush()

    result = await assess_need(db_session, lead)
    assert result.value == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_need_blends_website_and_social(db_session: AsyncSession) -> None:
    """With both signals present, website health carries 70% of the blend."""
    lead = _lead()
    db_session.add(lead)
    await db_session.flush()

    audit = WebsiteAudit(
        lead_id=lead.id, url="https://acme.example", status=AuditStatus.COMPLETED, health_score=1.0
    )
    db_session.add(audit)
    await db_session.flush()

    db_session.add(
        AuditReport(
            audit_id=audit.id,
            lead_id=lead.id,
            headline="h",
            summary="s",
            body_markdown="b",
            generated_by_agent="test",
            social_score=0.0,
        )
    )
    await db_session.flush()

    result = await assess_need(db_session, lead)
    # combined_health = 0.7*1.0 + 0.3*0.0 = 0.7 -> need = 1 - 0.7 = 0.3
    assert result.value == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Fit -- knowledge-base similarity search.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fit_is_neutral_with_no_category_or_notes(
    db_session: AsyncSession, fake_embedder
) -> None:
    """A lead with nothing to search on gets a neutral estimate, not a penalty."""
    lead = _lead(category=None, notes=None)
    db_session.add(lead)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    result, matched = await assess_fit(db_session, lead, kb)

    assert result.value == 0.5
    assert matched == []


@pytest.mark.asyncio
async def test_fit_matches_an_indexed_service(db_session: AsyncSession, fake_embedder) -> None:
    """A category overlapping an indexed service's text produces a real match."""
    service = Service(
        name="AI Agent Integration",
        slug="ai-agent-integration",
        category="Artificial Intelligence chatbot automation",
        summary="Retrieval-augmented assistants for internal knowledge bases.",
        description="Detailed description of chatbot automation and AI agents. " * 5,
        price_min=Decimal("35000"),
        price_max=Decimal("180000"),
        pricing_model=PricingModel.PROJECT_RANGE,
    )
    db_session.add(service)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    await kb.index_service(service)

    lead = _lead(category="chatbot automation", notes="interested in AI agents")
    db_session.add(lead)
    await db_session.flush()

    result, matched = await assess_fit(db_session, lead, kb)

    assert matched
    assert matched[0].id == service.id
    assert result.value > 0.0


@pytest.mark.asyncio
async def test_fit_with_no_services_indexed_scores_zero(
    db_session: AsyncSession, fake_embedder
) -> None:
    """A query with nothing to match against scores 0.0, not neutral."""
    lead = _lead(category="underwater basket weaving")
    db_session.add(lead)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    result, matched = await assess_fit(db_session, lead, kb)

    assert result.value == 0.0
    assert matched == []


# ---------------------------------------------------------------------------
# Revenue -- normalizes matched service pricing.
# ---------------------------------------------------------------------------


def _service(price_min: Decimal | None, price_max: Decimal | None) -> Service:
    """Build a Service fixture with the given price bounds.

    Args:
        price_min: Lower price bound, or None.
        price_max: Upper price bound, or None.

    Returns:
        An unsaved :class:`Service`.
    """
    return Service(
        name="Test Service",
        slug="test-service",
        category="Test",
        summary="A test service.",
        description="A longer description of a test service offering.",
        price_min=price_min,
        price_max=price_max,
        pricing_model=PricingModel.PROJECT_RANGE,
    )


def test_revenue_with_no_matched_services_is_neutral() -> None:
    """No Fit match means no basis for a revenue estimate."""
    result = assess_revenue([])
    assert result.value == 0.5


def test_revenue_with_custom_quoted_service_is_neutral() -> None:
    """A service with no price bounds cannot be normalized, so it's neutral."""
    result = assess_revenue([_service(None, None)])
    assert result.value == 0.5


def test_revenue_at_scale_minimum() -> None:
    """A service priced at the scale floor normalizes to 0.0."""
    price = Decimal(str(settings.lead_score_revenue_scale_min))
    result = assess_revenue([_service(price, price)])
    assert result.value == pytest.approx(0.0, abs=1e-3)


def test_revenue_at_scale_maximum() -> None:
    """A service priced at the scale ceiling normalizes to 1.0."""
    price = Decimal(str(settings.lead_score_revenue_scale_max))
    result = assess_revenue([_service(price, price)])
    assert result.value == pytest.approx(1.0, abs=1e-3)


def test_revenue_uses_the_top_matched_service() -> None:
    """Only the best-fit (first) service informs Revenue."""
    cheap = _service(Decimal("5000"), Decimal("5000"))
    expensive = _service(Decimal("180000"), Decimal("180000"))

    assert assess_revenue([cheap, expensive]).value < assess_revenue([expensive, cheap]).value


def test_revenue_clamps_above_scale_maximum() -> None:
    """A price above the configured ceiling clamps to 1.0 rather than overshooting."""
    result = assess_revenue([_service(Decimal("500000"), Decimal("500000"))])
    assert result.value == 1.0


# ---------------------------------------------------------------------------
# Compliance -- the hard gate and residual risk.
# ---------------------------------------------------------------------------


def test_do_not_contact_flag_triggers_the_gate() -> None:
    """The explicit flag is the only thing that triggers the gate."""
    lead = _lead(do_not_contact=True, do_not_contact_reason="Unsubscribed via email link")
    score, gate = assess_compliance(lead)

    assert gate.triggered is True
    assert "Unsubscribed" in gate.reasons[0]
    assert score.value == 0.0


def test_do_not_contact_without_reason_still_gates_with_a_default_message() -> None:
    """A missing reason does not prevent the gate from triggering."""
    lead = _lead(do_not_contact=True, do_not_contact_reason=None)
    score, gate = assess_compliance(lead)

    assert gate.triggered is True
    assert gate.reasons[0]


def test_clean_lead_with_consent_has_no_gate_and_full_score() -> None:
    """A lead with a recorded lawful basis and no flag scores cleanly."""
    lead = _lead(consent_basis="legitimate_interest", country="United States")
    score, gate = assess_compliance(lead)

    assert gate.triggered is False
    assert score.value == 1.0


def test_eea_lead_without_consent_basis_is_penalized_heavily() -> None:
    """Missing lawful basis in an EEA jurisdiction is a bigger risk than elsewhere."""
    eea_lead = _lead(country="Germany", consent_basis=None)
    other_lead = _lead(country="United States", consent_basis=None)

    eea_score, eea_gate = assess_compliance(eea_lead)
    other_score, other_gate = assess_compliance(other_lead)

    assert eea_gate.triggered is False
    assert other_gate.triggered is False
    assert eea_score.value < other_score.value


def test_country_matching_is_case_insensitive() -> None:
    """'GERMANY', 'germany', and 'Germany' are all recognized as EEA."""
    scores = {
        assess_compliance(_lead(country=variant, consent_basis=None))[0].value
        for variant in ("Germany", "germany", "GERMANY")
    }
    assert len(scores) == 1


def test_google_places_source_without_consent_adds_minor_risk() -> None:
    """Places-sourced leads without a recorded basis take an additional penalty."""
    places_lead = _lead(
        source=LeadSource.GOOGLE_PLACES, country="United States", consent_basis=None
    )
    manual_lead = _lead(source=LeadSource.MANUAL, country="United States", consent_basis=None)

    places_score, _ = assess_compliance(places_lead)
    manual_score, _ = assess_compliance(manual_lead)

    assert places_score.value < manual_score.value


def test_compliance_score_never_goes_negative() -> None:
    """Stacked penalties clamp at 0.0 rather than going negative."""
    lead = _lead(
        source=LeadSource.GOOGLE_PLACES, country="Germany", consent_basis=None
    )
    score, _ = assess_compliance(lead)
    assert score.value >= 0.0


# ---------------------------------------------------------------------------
# End-to-end: build_score_breakdown -> score_lead.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_do_not_contact_lead_is_gated_regardless_of_quality(
    db_session: AsyncSession, fake_embedder
) -> None:
    """A high-quality lead that is flagged do_not_contact still bands as Do-Not-Contact.

    This is the integration-level version of the formula-level regression
    test in test_lead_scoring.py -- it proves the wiring, not just the math.
    """
    service = Service(
        name="AI Agent Integration",
        slug="ai-agent-integration-2",
        category="Artificial Intelligence chatbot automation",
        summary="Retrieval-augmented assistants.",
        description="Chatbot automation and AI agent description text. " * 5,
        price_min=Decimal("180000"),
        price_max=Decimal("180000"),
        pricing_model=PricingModel.PROJECT_RANGE,
    )
    db_session.add(service)
    await db_session.flush()
    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    await kb.index_service(service)

    lead = _lead(
        category="chatbot automation",
        notes="AI agents",
        contact_email="a@b.example",
        contact_phone="555",
        linkedin_url="https://linkedin.com/x",
        website="https://x.example",
        confidence_score=1.0,
        do_not_contact=True,
        do_not_contact_reason="Legal objection received",
    )
    db_session.add(lead)
    await db_session.flush()

    breakdown = await build_score_breakdown(db_session, lead, kb)
    result = score_lead(breakdown)

    assert result.label is ScoreLabel.DO_NOT_CONTACT


@pytest.mark.asyncio
async def test_end_to_end_clean_strong_lead_scores_hot(
    db_session: AsyncSession, fake_embedder
) -> None:
    """A well-matched, contactable, healthy-need, compliant lead scores Hot."""
    service = Service(
        name="AI Agent Integration",
        slug="ai-agent-integration-3",
        category="Artificial Intelligence chatbot automation",
        summary="Retrieval-augmented assistants.",
        description="Chatbot automation and AI agent description text. " * 5,
        price_min=Decimal("180000"),
        price_max=Decimal("180000"),
        pricing_model=PricingModel.PROJECT_RANGE,
    )
    db_session.add(service)
    await db_session.flush()
    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    await kb.index_service(service)

    lead = _lead(
        category="chatbot automation",
        notes="AI agents",
        contact_email="a@b.example",
        contact_phone="555",
        linkedin_url="https://linkedin.com/x",
        website="https://x.example",
        confidence_score=1.0,
        consent_basis="consent",
        country="United States",
    )
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        WebsiteAudit(
            lead_id=lead.id, url="https://x.example", status=AuditStatus.COMPLETED, health_score=0.1
        )
    )
    await db_session.flush()

    breakdown = await build_score_breakdown(db_session, lead, kb)
    result = score_lead(breakdown)

    assert result.label is ScoreLabel.HOT
