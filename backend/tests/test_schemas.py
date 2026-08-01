"""Tests for Pydantic request/response validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.knowledge_base import RecommendationRequest, ServiceCreate
from app.schemas.lead import LeadCreate, PaginatedLeads


def _service_payload(**overrides: object) -> dict:
    """Build a valid ServiceCreate payload.

    Args:
        **overrides: Fields to replace.

    Returns:
        A payload dict.
    """
    payload = {
        "name": "Custom Web Application Development",
        "slug": "custom-web-application-development",
        "category": "Software Engineering",
        "summary": "Bespoke web applications on a modern stack.",
        "description": "A sufficiently long description of the offering here.",
        "price_min": Decimal("25000"),
        "price_max": Decimal("120000"),
    }
    payload.update(overrides)
    return payload


def test_valid_service_payload_is_accepted() -> None:
    """A well-formed service payload validates."""
    assert ServiceCreate(**_service_payload()).currency == "USD"


def test_inverted_price_range_is_rejected() -> None:
    """price_min above price_max is a validation error, not a stored oddity."""
    payload = _service_payload(price_min=Decimal("99999"), price_max=Decimal("100"))

    with pytest.raises(ValidationError, match="price_min must not exceed price_max"):
        ServiceCreate(**payload)


def test_equal_prices_are_allowed() -> None:
    """A single-point price is a valid degenerate range."""
    payload = _service_payload(price_min=Decimal("5000"), price_max=Decimal("5000"))
    assert ServiceCreate(**payload).price_min == Decimal("5000")


def test_negative_price_is_rejected() -> None:
    """Prices cannot be negative."""
    with pytest.raises(ValidationError):
        ServiceCreate(**_service_payload(price_min=Decimal("-1")))


def test_open_ended_price_range_is_allowed() -> None:
    """Both bounds may be omitted for custom-quoted services."""
    payload = _service_payload(price_min=None, price_max=None)
    assert ServiceCreate(**payload).price_min is None


@pytest.mark.parametrize("bad_slug", ["Has Spaces", "UPPERCASE", "trailing-", "--double"])
def test_malformed_slugs_are_rejected(bad_slug: str) -> None:
    """Slugs must be lowercase kebab-case."""
    with pytest.raises(ValidationError):
        ServiceCreate(**_service_payload(slug=bad_slug))


def test_valid_slug_is_accepted() -> None:
    """A well-formed kebab-case slug passes."""
    assert ServiceCreate(**_service_payload(slug="ai-agent-v2")).slug == "ai-agent-v2"


def test_lead_confidence_score_bounds() -> None:
    """confidence_score is constrained to [0.0, 1.0]."""
    assert LeadCreate(name="Acme", confidence_score=0.0).confidence_score == 0.0
    assert LeadCreate(name="Acme", confidence_score=1.0).confidence_score == 1.0

    for invalid in (-0.01, 1.01, 42.0):
        with pytest.raises(ValidationError):
            LeadCreate(name="Acme", confidence_score=invalid)


def test_lead_defaults() -> None:
    """A minimal lead defaults to manual source and zero confidence."""
    lead = LeadCreate(name="Acme Corp")

    assert lead.source.value == "manual"
    assert lead.confidence_score == 0.0
    assert lead.contact_email is None


def test_malformed_lead_email_is_rejected() -> None:
    """Contact emails are validated."""
    with pytest.raises(ValidationError):
        LeadCreate(name="Acme", contact_email="not-an-email")


def test_malformed_lead_website_is_rejected() -> None:
    """Website URLs must be well-formed."""
    with pytest.raises(ValidationError):
        LeadCreate(name="Acme", website="not a url")


def test_empty_lead_name_is_rejected() -> None:
    """A lead must be named."""
    with pytest.raises(ValidationError):
        LeadCreate(name="")


def test_page_size_is_capped_at_100() -> None:
    """Pagination honours the AGENTS.md section 9.3 cap."""
    assert PaginatedLeads(items=[], total=0, page=1, page_size=100).page_size == 100

    with pytest.raises(ValidationError):
        PaginatedLeads(items=[], total=0, page=1, page_size=101)


def test_page_number_must_be_positive() -> None:
    """Pages are 1-indexed."""
    with pytest.raises(ValidationError):
        PaginatedLeads(items=[], total=0, page=0, page_size=20)


def test_recommendation_request_defaults_and_bounds() -> None:
    """Recommendation parameters have sane defaults and enforced limits."""
    request = RecommendationRequest(query="We need an internal document assistant")

    assert request.top_k == 5
    assert request.min_score == 0.15

    with pytest.raises(ValidationError):
        RecommendationRequest(query="ok query", top_k=21)

    with pytest.raises(ValidationError):
        RecommendationRequest(query="x")
