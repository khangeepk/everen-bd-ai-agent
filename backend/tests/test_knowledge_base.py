"""Tests for :mod:`app.services.knowledge_base` ingestion and collapsing."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_base import (
    ChunkSourceType,
    KnowledgeChunk,
    PortfolioItem,
    PricingModel,
    Service,
)
from app.services.knowledge_base import (
    KnowledgeBaseService,
    RetrievedChunk,
    render_portfolio_document,
    render_service_document,
)


def _service() -> Service:
    """Build a Service fixture with a multi-paragraph description.

    Returns:
        An unsaved :class:`Service`.
    """
    return Service(
        name="AI Agent Integration",
        slug="ai-agent-integration",
        category="Artificial Intelligence",
        summary="Retrieval-augmented assistants with human review built in.",
        description="\n\n".join(f"Paragraph {i} " + ("detail " * 40) for i in range(8)),
        price_min=Decimal("35000.00"),
        price_max=Decimal("180000.00"),
        pricing_model=PricingModel.PROJECT_RANGE,
        typical_duration_weeks=20,
    )


def test_service_document_inlines_pricing_and_duration() -> None:
    """Pricing and duration are embedded so budget queries can match them."""
    document = render_service_document(_service())

    assert "AI Agent Integration" in document
    assert "USD 35,000 - 180,000" in document
    assert "20 weeks" in document
    assert "Artificial Intelligence" in document


def test_service_document_never_fabricates_price() -> None:
    """A service with no bounds renders the contact prompt, not a number."""
    service = _service()
    service.price_min = None
    service.price_max = None

    assert "Contact for pricing" in render_service_document(service)


def test_portfolio_document_includes_client_and_outcome() -> None:
    """Case-study rendering carries client, body, and outcome."""
    item = PortfolioItem(
        client_name="Meridian Logistics",
        industry="Freight",
        title="RAG assistant over carrier contracts",
        body="We built an ingestion pipeline over 40,000 scanned contracts.",
        outcome="Lookup time fell from 25 minutes to under two.",
    )
    document = render_portfolio_document(item)

    assert "Meridian Logistics" in document
    assert "Freight" in document
    assert "Outcome:" in document


@pytest.mark.asyncio
async def test_index_service_creates_ordered_chunks(
    db_session: AsyncSession, fake_embedder
) -> None:
    """Indexing writes contiguous, zero-based, embedded chunks."""
    service = _service()
    db_session.add(service)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    written = await kb.index_service(service)

    chunks = (
        (
            await db_session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.source_id == service.id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )

    assert written == len(chunks) > 0
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.source_type is ChunkSourceType.SERVICE for c in chunks)
    assert all(c.embedding is not None for c in chunks)


@pytest.mark.asyncio
async def test_embeddings_round_trip_through_the_column(
    db_session: AsyncSession, fake_embedder
) -> None:
    """Vectors survive the write/read cycle as lists of floats."""
    service = _service()
    db_session.add(service)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    await kb.index_service(service)

    chunk = (
        await db_session.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == service.id).limit(1)
        )
    ).scalar_one()

    assert isinstance(chunk.embedding, list)
    assert len(chunk.embedding) == fake_embedder.dimension
    assert all(isinstance(value, float) for value in chunk.embedding)


@pytest.mark.asyncio
async def test_reindexing_replaces_rather_than_duplicates(
    db_session: AsyncSession, fake_embedder
) -> None:
    """Re-indexing a service leaves exactly one generation of chunks."""
    service = _service()
    db_session.add(service)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    first = await kb.index_service(service)

    service.description = "A much shorter description this time."
    second = await kb.index_service(service)

    total = len(
        (
            await db_session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.source_id == service.id)
            )
        )
        .scalars()
        .all()
    )

    assert total == second
    assert total != first or second == first


@pytest.mark.asyncio
async def test_embedder_receives_every_chunk(db_session: AsyncSession, fake_embedder) -> None:
    """Each chunk is sent for embedding exactly once per index call."""
    service = _service()
    db_session.add(service)
    await db_session.flush()

    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)
    written = await kb.index_service(service)

    assert len(fake_embedder.calls) == 1
    assert len(fake_embedder.calls[0]) == written


@pytest.mark.asyncio
async def test_blank_query_short_circuits_without_embedding(
    db_session: AsyncSession, fake_embedder
) -> None:
    """An empty query returns nothing and never calls the embeddings API."""
    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)

    assert await kb.search("   ") == []
    assert fake_embedder.calls == []


@pytest.mark.asyncio
async def test_search_rejects_non_positive_top_k(
    db_session: AsyncSession, fake_embedder
) -> None:
    """top_k must be positive."""
    kb = KnowledgeBaseService(db=db_session, embedder=fake_embedder)

    with pytest.raises(ValueError, match="top_k must be positive"):
        await kb.search("anything", top_k=0)


def test_collapse_keeps_best_chunk_per_service() -> None:
    """Multiple chunks of one service reduce to its highest score."""
    service_id = uuid.uuid4()
    other_id = uuid.uuid4()
    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, service_id, "a", 0.42),
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, service_id, "b", 0.91),
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.SERVICE, other_id, "c", 0.60),
    ]
    collapsed = KnowledgeBaseService.collapse_to_services(chunks)

    assert len(collapsed) == 2
    assert collapsed[0].item == service_id
    assert collapsed[0].score == pytest.approx(0.91)


def test_collapse_excludes_portfolio_chunks() -> None:
    """Portfolio chunks are evidence, not recommendations in their own right."""
    chunks = [
        RetrievedChunk(uuid.uuid4(), ChunkSourceType.PORTFOLIO, uuid.uuid4(), "case", 0.99),
    ]
    assert KnowledgeBaseService.collapse_to_services(chunks) == []


def test_collapse_on_empty_input_returns_empty() -> None:
    """Collapsing nothing yields nothing."""
    assert KnowledgeBaseService.collapse_to_services([]) == []
