"""Tests for ORM model behavior and constraints."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_base import PricingModel, Service
from app.db.models.lead import Lead, LeadSource, LeadStatus
from app.db.models.user import User, UserRole


def _service(**overrides: object) -> Service:
    """Build a valid Service with optional overrides.

    Args:
        **overrides: Attributes to replace.

    Returns:
        An unsaved :class:`Service`.
    """
    defaults = {
        "name": "Custom Web Application Development",
        "slug": "custom-web-app",
        "category": "Software Engineering",
        "summary": "Bespoke web applications.",
        "description": "A long description of the service offering.",
        "price_min": Decimal("25000.00"),
        "price_max": Decimal("120000.00"),
        "pricing_model": PricingModel.PROJECT_RANGE,
    }
    defaults.update(overrides)
    return Service(**defaults)


def test_price_range_label_with_both_bounds() -> None:
    """A bounded range renders with both endpoints."""
    assert _service().price_range_label() == "USD 25,000 - 120,000"


def test_price_range_label_with_only_minimum() -> None:
    """An open-ended range renders as 'From'."""
    assert _service(price_max=None).price_range_label() == "From USD 25,000"


def test_price_range_label_with_only_maximum() -> None:
    """A capped range renders as 'Up to'."""
    assert _service(price_min=None).price_range_label() == "Up to USD 120,000"


def test_price_range_label_with_no_bounds() -> None:
    """Fully custom pricing renders a contact prompt, never a fabricated number."""
    label = _service(price_min=None, price_max=None).price_range_label()
    assert label == "Contact for pricing"


def test_price_range_label_respects_currency() -> None:
    """The stored currency code appears in the label."""
    assert "EUR" in _service(currency="EUR").price_range_label()


def test_lead_is_contactable_with_email() -> None:
    """An email address makes a lead contactable."""
    lead = Lead(name="Acme", contact_email="ops@acme.example", source=LeadSource.WEB_RESEARCH)
    assert lead.is_contactable() is True


def test_lead_is_contactable_with_linkedin_only() -> None:
    """A LinkedIn URL alone is enough."""
    lead = Lead(name="Acme", linkedin_url="https://linkedin.com/company/acme")
    assert lead.is_contactable() is True


def test_lead_without_channels_is_not_contactable() -> None:
    """A lead with no channel is not contactable."""
    assert Lead(name="Acme").is_contactable() is False


def test_approver_roles_can_approve() -> None:
    """Admins and sales users may approve outreach."""
    for role in (UserRole.ADMIN, UserRole.SALES):
        user = User(provider_subject="s", email="a@b.example", role=role, is_active=True)
        assert user.can_approve_outreach() is True


def test_non_approver_roles_cannot_approve() -> None:
    """Viewers may not approve outreach (AGENTS.md section 8)."""
    for role in (UserRole.VIEWER,):
        user = User(provider_subject="s", email="a@b.example", role=role, is_active=True)
        assert user.can_approve_outreach() is False


def test_viewer_cannot_write() -> None:
    """VIEWER is read-only; ADMIN and SALES may write (app.api.deps.require_write_access)."""
    for role in (UserRole.ADMIN, UserRole.SALES):
        user = User(provider_subject="s", email="a@b.example", role=role, is_active=True)
        assert user.can_write() is True
    viewer = User(provider_subject="s2", email="c@d.example", role=UserRole.VIEWER, is_active=True)
    assert viewer.can_write() is False


def test_deactivated_admin_cannot_approve() -> None:
    """Deactivation revokes approval rights regardless of role."""
    user = User(
        provider_subject="s", email="a@b.example", role=UserRole.ADMIN, is_active=False
    )
    assert user.can_approve_outreach() is False


@pytest.mark.asyncio
async def test_lead_persists_and_defaults_to_new(db_session: AsyncSession) -> None:
    """A saved lead defaults to status 'new' with a zero confidence score."""
    lead = Lead(name="Acme Corp", source=LeadSource.WEB_RESEARCH)
    db_session.add(lead)
    await db_session.flush()

    assert lead.id is not None
    assert lead.status is LeadStatus.NEW
    assert lead.confidence_score == 0.0
    assert lead.created_at is not None


@pytest.mark.asyncio
async def test_duplicate_contact_email_is_rejected(db_session: AsyncSession) -> None:
    """The unique constraint on contact_email_hash prevents duplicate leads."""
    from app.services.pii import blind_index

    email_hash = blind_index("dup@example.com", purpose="lead_contact_email")
    db_session.add(
        Lead(name="Acme", contact_email="dup@example.com", contact_email_hash=email_hash)
    )
    await db_session.flush()

    db_session.add(
        Lead(
            name="Acme Again",
            contact_email="dup@example.com",
            contact_email_hash=email_hash,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_duplicate_service_slug_is_rejected(db_session: AsyncSession) -> None:
    """Service slugs are unique."""
    db_session.add(_service())
    await db_session.flush()

    db_session.add(_service(name="Another"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
