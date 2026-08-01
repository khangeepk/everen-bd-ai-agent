"""Tests for :mod:`app.agents.recommender`.

The LLM is never called: :meth:`ServiceRecommenderAgent._generate_rationales`
is patched to simulate both a working provider and an outage, so retrieval and
fallback behavior are asserted independently of any network service.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.recommender import (
    ServiceRecommenderAgent,
    build_context,
    fallback_rationale,
)
from app.db.models.knowledge_base import ChunkSourceType, PricingModel, Service
from app.schemas.knowledge_base import RecommendationRequest
from app.services.knowledge_base import KnowledgeBaseService, RetrievedChunk


def _service(name: str, slug: str) -> Service:
    """Build a Service fixture.

    Args:
        name: Service name.
        slug: URL slug.

    Returns:
        An unsaved :class:`Service`.
    """
    return Service(
        name=name,
        slug=slug,
        category="Artificial Intelligence",
        summary=f"{name} summary for retrieval testing purposes.",
        description=f"{name} description. " * 20,
        price_min=Decimal("35000.00"),
        price_max=Decimal("180000.00"),
        pricing_model=PricingModel.PROJECT_RANGE,
    )


def test_build_context_numbers_and_scores_chunks() -> None:
    """Context blocks carry an index, source type, and score per chunk."""
    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, uuid.uuid4(), "Alpha text", 0.87),
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.PORTFOLIO, uuid.uuid4(), "Beta text", 0.61),
    ]
    context = build_context(chunks)

    assert "[1]" in context and "[2]" in context
    assert "0.870" in context
    assert "service" in context and "portfolio" in context


def test_build_context_on_empty_input_is_explicit() -> None:
    """An empty retrieval yields an explicit placeholder, not a blank prompt."""
    assert "no relevant knowledge base content" in build_context([])


def test_fallback_rationale_quotes_only_stored_facts() -> None:
    """The deterministic rationale never invents pricing."""
    service = _service("AI Agent Integration", "ai-agent-integration")
    rationale = fallback_rationale(service, 0.83)

    assert "AI Agent Integration" in rationale
    assert "0.83" in rationale
    assert "USD 35,000 - 180,000" in rationale


@pytest.mark.asyncio
async def test_no_matches_returns_empty_recommendations(
    db_session: AsyncSession, fake_embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty knowledge base yields no recommendations rather than an error."""
    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    monkeypatch.setattr(KnowledgeBaseService, "search", _fake_search([]))

    agent = ServiceRecommenderAgent(db=db_session, kb=kb)
    result = await agent.recommend(RecommendationRequest(query="quantum teleportation"))

    assert result.recommendations == []
    assert result.retrieved_chunk_count == 0


def _fake_search(chunks: list[RetrievedChunk]):
    """Build a KnowledgeBaseService.search replacement returning fixed chunks.

    Args:
        chunks: Chunks the fake should return.

    Returns:
        An async callable suitable for monkeypatching.
    """

    async def _search(self, query: str, *, top_k: int = 8, min_score: float = 0.15):
        return chunks

    return _search


@pytest.mark.asyncio
async def test_recommendations_are_ranked_and_grounded(
    db_session: AsyncSession, fake_embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Services come back ordered by score with excerpts attached."""
    strong = _service("AI Agent Integration", "ai-agent-integration")
    weak = _service("Cloud Infrastructure", "cloud-infrastructure")
    db_session.add_all([strong, weak])
    await db_session.flush()

    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, strong.id, "Strong excerpt", 0.92),
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, weak.id, "Weak excerpt", 0.31),
    ]
    monkeypatch.setattr(KnowledgeBaseService, "search", _fake_search(chunks))
    monkeypatch.setattr(
        ServiceRecommenderAgent, "_generate_rationales", _fake_rationales({})
    )

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    agent = ServiceRecommenderAgent(db=db_session, kb=kb)
    result = await agent.recommend(RecommendationRequest(query="We need an LLM assistant"))

    names = [entry.service.name for entry in result.recommendations]
    assert names == ["AI Agent Integration", "Cloud Infrastructure"]
    assert result.recommendations[0].score > result.recommendations[1].score
    assert result.recommendations[0].supporting_excerpts


def _fake_rationales(mapping: dict):
    """Build a _generate_rationales replacement returning a fixed mapping.

    Args:
        mapping: Service ID to rationale text.

    Returns:
        An async callable suitable for monkeypatching.
    """

    async def _generate(self, query, chunks, services):
        return mapping

    return _generate


@pytest.mark.asyncio
async def test_llm_outage_degrades_to_fallback_rationale(
    db_session: AsyncSession, fake_embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the LLM returns nothing, recommendations still carry a rationale."""
    service = _service("AI Agent Integration", "ai-agent-integration")
    db_session.add(service)
    await db_session.flush()

    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, service.id, "Excerpt", 0.88)
    ]
    monkeypatch.setattr(KnowledgeBaseService, "search", _fake_search(chunks))
    monkeypatch.setattr(ServiceRecommenderAgent, "_generate_rationales", _fake_rationales({}))

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    agent = ServiceRecommenderAgent(db=db_session, kb=kb)
    result = await agent.recommend(RecommendationRequest(query="LLM assistant"))

    assert len(result.recommendations) == 1
    rationale = result.recommendations[0].rationale
    assert rationale
    assert "AI Agent Integration" in rationale


@pytest.mark.asyncio
async def test_llm_rationale_is_used_when_available(
    db_session: AsyncSession, fake_embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A returned LLM rationale takes precedence over the fallback."""
    service = _service("AI Agent Integration", "ai-agent-integration")
    db_session.add(service)
    await db_session.flush()

    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, service.id, "Excerpt", 0.88)
    ]
    monkeypatch.setattr(KnowledgeBaseService, "search", _fake_search(chunks))
    monkeypatch.setattr(
        ServiceRecommenderAgent,
        "_generate_rationales",
        _fake_rationales({service.id: "Tailored explanation from the model."}),
    )

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    agent = ServiceRecommenderAgent(db=db_session, kb=kb)
    result = await agent.recommend(RecommendationRequest(query="LLM assistant"))

    assert result.recommendations[0].rationale == "Tailored explanation from the model."


@pytest.mark.asyncio
async def test_top_k_limits_returned_recommendations(
    db_session: AsyncSession, fake_embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No more than top_k services are recommended."""
    services = [_service(f"Service {i}", f"service-{i}") for i in range(6)]
    db_session.add_all(services)
    await db_session.flush()

    chunks = [
        RetrievedChunk(
            uuid.uuid4(), ChunkSourceType.SERVICE, svc.id, f"Excerpt {i}", 0.9 - i * 0.05
        )
        for i, svc in enumerate(services)
    ]
    monkeypatch.setattr(KnowledgeBaseService, "search", _fake_search(chunks))
    monkeypatch.setattr(ServiceRecommenderAgent, "_generate_rationales", _fake_rationales({}))

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    agent = ServiceRecommenderAgent(db=db_session, kb=kb)
    result = await agent.recommend(RecommendationRequest(query="anything", top_k=2))

    assert len(result.recommendations) == 2
