"""RAG-based service recommendation agent.

Retrieves relevant knowledge-base chunks for a prospect's stated need, then
asks the LLM to explain why each retrieved service fits. Recommendations are
grounded strictly in retrieved content -- the agent never invents services or
prices.

This agent produces recommendations only. It does not draft or send outreach;
that path runs through the human-approval gate in AGENTS.md section 8.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.knowledge_base import ChunkSourceType, Service
from app.schemas.knowledge_base import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendedService,
    ServiceResponse,
)
from app.services.cost_guard import BudgetExceededError, CostProvider, estimate_openai_cost
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.knowledge_base import KnowledgeBaseService, RetrievedChunk

logger = logging.getLogger(__name__)

AGENT_NAME = "service-recommender-v1"

_SYSTEM_PROMPT = """You are a solutions consultant for Everen Techno.

You will receive a prospect's stated need and a set of excerpts retrieved from
Everen Techno's internal services knowledge base.

Rules:
- Recommend ONLY services that appear in the excerpts. Never invent a service.
- Never invent or adjust prices. Quote pricing only as it appears in the excerpts.
- If the excerpts do not support a confident recommendation, say so plainly.
- Write two or three sentences per service explaining the fit in concrete terms.
"""


def build_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Format retrieved chunks as a numbered context block for the LLM.

    Args:
        chunks: Retrieved knowledge-base chunks.

    Returns:
        A newline-delimited context block, or a placeholder when empty.
    """
    if not chunks:
        return "(no relevant knowledge base content was retrieved)"
    return "\n\n".join(
        f"[{index}] ({chunk.source_type.value}, score={chunk.score:.3f})\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    )


def fallback_rationale(service: Service, score: float) -> str:
    """Produce a deterministic rationale without calling the LLM.

    Used when the LLM is unavailable, so a degraded recommendation is still
    returned rather than a 5xx.

    Args:
        service: The recommended service.
        score: Its similarity score.

    Returns:
        A short, factual rationale drawn only from stored fields.
    """
    return (
        f"{service.name} ({service.category}) matched this need with a similarity "
        f"of {score:.2f}. {service.summary} Pricing: {service.price_range_label()}."
    )


class ServiceRecommenderAgent:
    """Recommends services for a prospect need using retrieval-augmented generation."""

    def __init__(self, db: AsyncSession, kb: KnowledgeBaseService) -> None:
        """Initialize the agent.

        Args:
            db: Active database session.
            kb: Knowledge base service used for retrieval.
        """
        self._db = db
        self._kb = kb

    async def _generate_rationales(
        self, query: str, chunks: Sequence[RetrievedChunk], services: Sequence[Service]
    ) -> dict[uuid.UUID, str]:
        """Ask the LLM for a fit rationale per service.

        Falls back to :func:`fallback_rationale` for every service if the LLM
        call fails, so retrieval quality is never masked by a provider outage.

        Args:
            query: The prospect's stated need.
            chunks: Retrieved supporting chunks.
            services: Services to explain.

        Returns:
            A mapping of service ID to rationale text.
        """
        try:
            await enforce_budget_before_call(
                self._db, CostProvider.OPENAI, settings.cost_guard_daily_budget_openai_usd
            )

            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            service_list = "\n".join(f"- {s.name}: {s.summary}" for s in services)
            response = await client.chat.completions.create(
                model=settings.recommendation_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Prospect need:\n{query}\n\n"
                            f"Retrieved excerpts:\n{build_context(chunks)}\n\n"
                            f"Services to explain:\n{service_list}\n\n"
                            "Return one line per service formatted as "
                            "'<service name>: <rationale>'."
                        ),
                    },
                ],
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""

            usage = getattr(response, "usage", None)
            cost = estimate_openai_cost(
                settings.recommendation_model,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
            await record_spend(
                self._db,
                CostProvider.OPENAI,
                "recommender.rationale",
                cost,
                daily_budget_usd=settings.cost_guard_daily_budget_openai_usd,
            )
        except BudgetExceededError:
            logger.warning("LLM rationale generation skipped: daily OpenAI budget exhausted")
            return {}
        except Exception:
            logger.exception("LLM rationale generation failed; using deterministic fallback")
            return {}

        by_name = {service.name.lower(): service.id for service in services}
        rationales: dict[uuid.UUID, str] = {}
        for line in content.splitlines():
            if ":" not in line:
                continue
            raw_name, _, text = line.partition(":")
            service_id = by_name.get(raw_name.strip().lstrip("-* ").lower())
            if service_id and text.strip():
                rationales[service_id] = text.strip()

        logger.info("Generated rationales", extra={"count": len(rationales)})
        return rationales

    async def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        """Recommend services matching a prospect's stated need.

        Args:
            request: The query and retrieval parameters.

        Returns:
            Ranked recommendations with rationales and supporting excerpts.
        """
        chunks = await self._kb.search(
            request.query,
            top_k=max(request.top_k * 3, request.top_k),
            min_score=request.min_score,
        )
        scored_services = KnowledgeBaseService.collapse_to_services(chunks)[: request.top_k]

        if not scored_services:
            logger.info("No services matched query", extra={"agent": AGENT_NAME})
            return RecommendationResponse(
                query=request.query, recommendations=[], retrieved_chunk_count=len(chunks)
            )

        service_ids = [entry.item for entry in scored_services]
        rows = (
            (await self._db.execute(select(Service).where(Service.id.in_(service_ids))))
            .scalars()
            .all()
        )
        by_id = {service.id: service for service in rows}
        ordered = [by_id[entry.item] for entry in scored_services if entry.item in by_id]

        rationales = await self._generate_rationales(request.query, chunks, ordered)

        recommendations: list[RecommendedService] = []
        for entry in scored_services:
            service = by_id.get(entry.item)
            if service is None:
                continue
            excerpts = [
                chunk.content
                for chunk in chunks
                if chunk.source_id == service.id
                or chunk.source_type is ChunkSourceType.PORTFOLIO
            ][:3]
            recommendations.append(
                RecommendedService(
                    service=ServiceResponse.model_validate(service),
                    score=entry.score,
                    rationale=rationales.get(service.id)
                    or fallback_rationale(service, entry.score),
                    supporting_excerpts=excerpts,
                )
            )

        logger.info(
            "Recommendation complete",
            extra={"agent": AGENT_NAME, "recommendations": len(recommendations)},
        )
        return RecommendationResponse(
            query=request.query,
            recommendations=recommendations,
            retrieved_chunk_count=len(chunks),
        )
