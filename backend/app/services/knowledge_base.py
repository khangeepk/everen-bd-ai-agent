"""Services Knowledge Base ingestion and retrieval.

Ingestion renders each Service and PortfolioItem into embedding-friendly text,
chunks it, embeds it, and replaces that record's chunks transactionally.
Retrieval runs top-k cosine search in PostgreSQL via pgvector, then collapses
chunk hits down to one entry per service.

See AGENTS.md sections 6, 7, and 9.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.knowledge_base import (
    ChunkSourceType,
    KnowledgeChunk,
    PortfolioItem,
    Service,
)
from app.services.chunking import chunk_text
from app.services.embeddings import EmbeddingClient
from app.services.similarity import ScoredItem, deduplicate_by_key, distance_to_score

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.15


@dataclass(frozen=True)
class RetrievedChunk:
    """A knowledge chunk returned by vector search.

    Attributes:
        chunk_id: Identifier of the matching chunk.
        source_type: Whether the chunk came from a service or portfolio item.
        source_id: Identifier of the originating record.
        content: The chunk text.
        score: Cosine similarity to the query.
    """

    chunk_id: uuid.UUID
    source_type: ChunkSourceType
    source_id: uuid.UUID
    content: str
    score: float


def render_service_document(service: Service) -> str:
    """Render a Service into text suitable for embedding.

    Pricing and duration are inlined so that budget- and timeline-shaped
    queries can match on them.

    Args:
        service: The service to render.

    Returns:
        A plain-text document describing the service.
    """
    parts = [
        f"Service: {service.name}",
        f"Category: {service.category}",
        f"Summary: {service.summary}",
        f"Pricing: {service.price_range_label()} ({service.pricing_model.value})",
    ]
    if service.typical_duration_weeks:
        parts.append(f"Typical duration: {service.typical_duration_weeks} weeks")
    parts.append("")
    parts.append(service.description)
    return "\n".join(parts)


def render_portfolio_document(item: PortfolioItem) -> str:
    """Render a PortfolioItem into text suitable for embedding.

    Args:
        item: The portfolio item to render.

    Returns:
        A plain-text document describing the delivered project.
    """
    parts = [f"Case study: {item.title}", f"Client: {item.client_name}"]
    if item.industry:
        parts.append(f"Industry: {item.industry}")
    parts.append("")
    parts.append(item.body)
    if item.outcome:
        parts.append("")
        parts.append(f"Outcome: {item.outcome}")
    return "\n".join(parts)


class KnowledgeBaseService:
    """Ingests knowledge-base records and retrieves them by similarity."""

    def __init__(self, db: AsyncSession, embedder: EmbeddingClient) -> None:
        """Initialize the service.

        Args:
            db: Active database session.
            embedder: Client used to embed documents and queries.
        """
        self._db = db
        self._embedder = embedder

    async def _replace_chunks(
        self, source_type: ChunkSourceType, source_id: uuid.UUID, document: str
    ) -> int:
        """Re-chunk, re-embed, and atomically replace a record's chunks.

        The delete and the insert share the caller's transaction, so a failure
        mid-embed leaves the previous chunks intact.

        Args:
            source_type: Which kind of record is being indexed.
            source_id: Identifier of the record.
            document: Rendered text to index.

        Returns:
            The number of chunks written.
        """
        pieces = chunk_text(document)
        if not pieces:
            logger.warning(
                "Document produced no chunks",
                extra={"source_type": source_type.value, "source_id": str(source_id)},
            )
            return 0

        vectors = await self._embedder.embed(pieces)

        await self._db.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.source_type == source_type,
                KnowledgeChunk.source_id == source_id,
            )
        )

        for index, (content, vector) in enumerate(zip(pieces, vectors)):
            self._db.add(
                KnowledgeChunk(
                    source_type=source_type,
                    source_id=source_id,
                    chunk_index=index,
                    content=content,
                    token_count=len(content) // 4,
                    embedding=vector,
                    embedding_model=settings.embedding_model,
                )
            )

        await self._db.flush()
        logger.info(
            "Indexed record",
            extra={
                "source_type": source_type.value,
                "source_id": str(source_id),
                "chunks": len(pieces),
            },
        )
        return len(pieces)

    async def index_service(self, service: Service) -> int:
        """Index a single service.

        Args:
            service: The service to index.

        Returns:
            The number of chunks written.
        """
        return await self._replace_chunks(
            ChunkSourceType.SERVICE, service.id, render_service_document(service)
        )

    async def index_portfolio_item(self, item: PortfolioItem) -> int:
        """Index a single portfolio item.

        Args:
            item: The portfolio item to index.

        Returns:
            The number of chunks written.
        """
        return await self._replace_chunks(
            ChunkSourceType.PORTFOLIO, item.id, render_portfolio_document(item)
        )

    async def reindex_all(self) -> dict[str, int]:
        """Rebuild the entire knowledge base.

        Returns:
            Counts keyed by ``"services"``, ``"portfolio_items"``, and
            ``"chunks"``.
        """
        services = (await self._db.execute(select(Service).where(Service.is_active))).scalars().all()
        items = (
            (await self._db.execute(select(PortfolioItem).where(PortfolioItem.is_public)))
            .scalars()
            .all()
        )

        total = 0
        for service in services:
            total += await self.index_service(service)
        for item in items:
            total += await self.index_portfolio_item(item)

        summary = {
            "services": len(services),
            "portfolio_items": len(items),
            "chunks": total,
        }
        logger.info("Knowledge base reindexed", extra=summary)
        return summary

    async def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[RetrievedChunk]:
        """Retrieve the chunks most similar to a natural-language query.

        Args:
            query: The search text.
            top_k: Maximum chunks to return.
            min_score: Discard chunks scoring below this cosine similarity.

        Returns:
            Matching chunks, highest score first. Empty for a blank query.

        Raises:
            ValueError: If ``top_k`` is not positive.
        """
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not query.strip():
            return []

        query_vector = (await self._embedder.embed([query]))[0]

        bind = self._db.get_bind()
        is_postgres = bind.dialect.name == "postgresql"

        if is_postgres:
            distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
            result = await self._db.execute(
                select(KnowledgeChunk, distance.label("distance"))
                .where(KnowledgeChunk.embedding.is_not(None))
                .order_by(distance)
                .limit(top_k)
            )
            rows = result.all()
        else:
            from app.services.similarity import cosine_similarity

            all_chunks = (
                await self._db.execute(
                    select(KnowledgeChunk).where(KnowledgeChunk.embedding.is_not(None))
                )
            ).scalars().all()
            scored = []
            for chunk in all_chunks:
                if chunk.embedding:
                    sim = cosine_similarity(query_vector, chunk.embedding)
                    dist = 1.0 - sim
                    scored.append((chunk, dist))
            scored.sort(key=lambda item: item[1])
            rows = scored[:top_k]

        retrieved: list[RetrievedChunk] = []
        for chunk, raw_distance in rows:
            score = distance_to_score(float(raw_distance))
            if score < min_score:
                continue
            retrieved.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    content=chunk.content,
                    score=score,
                )
            )

        logger.info(
            "Knowledge base searched",
            extra={"query_chars": len(query), "hits": len(retrieved), "top_k": top_k},
        )
        return retrieved

    @staticmethod
    def collapse_to_services(
        chunks: Sequence[RetrievedChunk],
    ) -> list[ScoredItem[uuid.UUID]]:
        """Reduce chunk hits to one scored entry per service.

        Portfolio chunks are ignored here -- they are surfaced as supporting
        evidence rather than as recommendations in their own right.

        Args:
            chunks: Retrieved chunks.

        Returns:
            Service IDs scored by their best-matching chunk, descending.
        """
        service_hits = [
            ScoredItem(item=chunk.source_id, score=chunk.score)
            for chunk in chunks
            if chunk.source_type is ChunkSourceType.SERVICE
        ]
        return deduplicate_by_key(service_hits, lambda service_id: service_id)
