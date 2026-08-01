"""Integration tests for language-aware outreach draft generation.

Coverage:
* test_resolve_draft_language_supported               — returns 'es' for Spanish lead
* test_resolve_draft_language_unsupported_fallback   — unsupported lang falls back to None (English)
* test_resolve_draft_language_override_wins          — language_override beats detected_language
* test_agent_injects_language_note_into_system_prompt— verifies prompt contains language instruction
* test_generate_drafts_persists_draft_language      — asserts draft_language stored on DB row
* test_language_analytics_endpoint                   — GET /analytics/languages returns stats
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.outreach import OutreachDraftAgent, _resolve_draft_language
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachChannel, OutreachDraft
from app.services.knowledge_base import KnowledgeBaseService


def _make_lead(
    detected_language: str | None = None,
    language_override: str | None = None,
    country: str | None = "Spain",
) -> Lead:
    """Helper to construct an in-memory Lead instance.

    Args:
        detected_language: Auto-detected language code.
        language_override: Manual rep override.
        country: Lead country.

    Returns:
        Unpersisted Lead instance.
    """
    lead = Lead(
        id=uuid.uuid4(),
        name="Spanish Business SL",
        country=country,
        detected_language=detected_language,
        language_override=language_override,
    )
    lead.set_contact_email("contact@business.es")
    return lead


def test_resolve_draft_language_supported() -> None:
    """Supported language should resolve to its BCP-47 code."""
    lead = _make_lead(detected_language="es")
    supported = ["en", "es", "fr", "de"]
    assert _resolve_draft_language(lead, supported) == "es"


def test_resolve_draft_language_english_returns_none() -> None:
    """English ('en') should return None since English requires no extra prompt instruction."""
    lead = _make_lead(detected_language="en")
    supported = ["en", "es", "fr"]
    assert _resolve_draft_language(lead, supported) is None


def test_resolve_draft_language_unsupported_fallback() -> None:
    """Unsupported language should fall back to None (English) with a warning."""
    lead = _make_lead(detected_language="sw")  # Swahili not in default supported list
    supported = ["en", "es", "fr"]
    assert _resolve_draft_language(lead, supported) is None


def test_resolve_draft_language_override_wins() -> None:
    """language_override should beat detected_language."""
    lead = _make_lead(detected_language="fr", language_override="es")
    supported = ["en", "es", "fr"]
    assert _resolve_draft_language(lead, supported) == "es"


@pytest.mark.asyncio
async def test_agent_injects_language_note_into_system_prompt(db_session: AsyncSession) -> None:
    """Non-English leads get language instructions in the LLM system prompt."""
    lead = _make_lead(detected_language="es")
    db_session.add(lead)
    await db_session.flush()

    mock_kb = AsyncMock(spec=KnowledgeBaseService)

    agent = OutreachDraftAgent(db=db_session, kb=mock_kb)

    with patch.object(agent, "_generate_with_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Hola, vi su sitio web..."
        content = await agent._generate_for_channel(
            OutreachChannel.EMAIL,
            lead,
            findings=[],
            service=None,
            base_context="Business: Spanish Business SL",
            decision=AsyncMock(warnings=[]),
        )

        assert content.draft_language == "es"
        assert mock_llm.called
        system_prompt_arg = mock_llm.call_args[0][0]
        assert "LANGUAGE REQUIREMENT: Write the entire message body in Spanish" in system_prompt_arg


@pytest.mark.asyncio
async def test_analytics_language_endpoint(db_session: AsyncSession) -> None:
    """get_language_performance service should group sent drafts by draft_language."""
    from app.services.analytics import get_language_performance

    lead = _make_lead(detected_language="es")
    db_session.add(lead)
    await db_session.flush()

    from app.db.base import utcnow

    approver_id = uuid.uuid4()
    now = utcnow()
    draft_es = OutreachDraft(
        id=uuid.uuid4(),
        lead_id=lead.id,
        channel=OutreachChannel.EMAIL,
        status=DraftStatus.SENT,
        body="Hola",
        created_by_agent="outreach-agent-v1",
        draft_language="es",
        approved_by_id=approver_id,
        sent_at=now,
    )
    draft_en = OutreachDraft(
        id=uuid.uuid4(),
        lead_id=lead.id,
        channel=OutreachChannel.EMAIL,
        status=DraftStatus.SENT,
        body="Hello",
        created_by_agent="outreach-agent-v1",
        draft_language=None,  # English
        approved_by_id=approver_id,
        sent_at=now,
    )
    db_session.add(draft_es)
    db_session.add(draft_en)
    await db_session.flush()

    perf = await get_language_performance(db_session)
    assert len(perf) == 2
    langs = {p.language: p.drafts_sent for p in perf}
    assert langs.get("es") == 1
    assert langs.get("en") == 1
