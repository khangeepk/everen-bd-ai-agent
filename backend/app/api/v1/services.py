"""Services Knowledge Base routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.recommender import ServiceRecommenderAgent
from app.api.deps import get_current_user, require_approver, require_write_access
from app.db.models.knowledge_base import PortfolioItem, Service
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.knowledge_base import (
    PortfolioItemCreate,
    PortfolioItemResponse,
    RecommendationRequest,
    RecommendationResponse,
    ReindexResponse,
    ServiceCreate,
    ServiceResponse,
)
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/services", tags=["services"])


def get_kb_service(db: AsyncSession = Depends(get_db)) -> KnowledgeBaseService:
    """Construct a knowledge base service for the request.

    Args:
        db: Active database session.

    Returns:
        A configured :class:`KnowledgeBaseService`.
    """
    return KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())


@router.post(
    "",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service",
    description="Creates a service and indexes it into the knowledge base.",
)
async def create_service(
    payload: ServiceCreate,
    db: AsyncSession = Depends(get_db),
    kb: KnowledgeBaseService = Depends(get_kb_service),
    user: User = Depends(require_write_access),
) -> ServiceResponse:
    """Create a service and embed it for retrieval.

    Args:
        payload: The service to create.
        db: Active database session.
        kb: Knowledge base service.
        user: The authenticated caller.

    Returns:
        The created service.

    Raises:
        HTTPException: 409 if the slug is already taken.
    """
    service = Service(**payload.model_dump())
    db.add(service)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.exception("Duplicate service slug", extra={"slug": payload.slug})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A service with slug '{payload.slug}' already exists",
        ) from exc

    await kb.index_service(service)
    logger.info(
        "Service created", extra={"service_id": str(service.id), "user_id": str(user.id)}
    )
    return ServiceResponse.model_validate(service)


@router.get(
    "",
    response_model=list[ServiceResponse],
    summary="List services",
    description="Returns active services, optionally filtered by category.",
)
async def list_services(
    category: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ServiceResponse]:
    """List services with pagination.

    Args:
        category: Optional category filter.
        limit: Page size, capped at 100 per AGENTS.md section 9.3.
        offset: Number of rows to skip.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        Matching services.
    """
    stmt = select(Service).where(Service.is_active)
    if category:
        stmt = stmt.where(Service.category == category)
    stmt = stmt.order_by(Service.name).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).scalars().all()
    return [ServiceResponse.model_validate(row) for row in rows]


@router.get(
    "/{service_id}",
    response_model=ServiceResponse,
    summary="Get a service",
    description="Retrieves a single service by its identifier.",
)
async def get_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ServiceResponse:
    """Retrieve one service.

    Args:
        service_id: Identifier of the service.
        db: Active database session.
        user: The authenticated caller.

    Returns:
        The requested service.

    Raises:
        HTTPException: 404 if no such service exists.
    """
    service = await db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return ServiceResponse.model_validate(service)


@router.post(
    "/portfolio",
    response_model=PortfolioItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a portfolio item",
    description="Adds a case study and indexes it into the knowledge base.",
)
async def create_portfolio_item(
    payload: PortfolioItemCreate,
    db: AsyncSession = Depends(get_db),
    kb: KnowledgeBaseService = Depends(get_kb_service),
    user: User = Depends(require_write_access),
) -> PortfolioItemResponse:
    """Create and index a portfolio item.

    Args:
        payload: The portfolio item to create.
        db: Active database session.
        kb: Knowledge base service.
        user: The authenticated caller.

    Returns:
        The created portfolio item.

    Raises:
        HTTPException: 404 if ``service_id`` references a missing service.
    """
    if payload.service_id is not None and await db.get(Service, payload.service_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Referenced service not found"
        )

    item = PortfolioItem(**payload.model_dump())
    db.add(item)
    await db.flush()
    await kb.index_portfolio_item(item)

    logger.info("Portfolio item created", extra={"item_id": str(item.id)})
    return PortfolioItemResponse.model_validate(item)


@router.post(
    "/recommend",
    response_model=RecommendationResponse,
    summary="Recommend services for a prospect need",
    description=(
        "Runs RAG over the services knowledge base and returns ranked service "
        "recommendations. Produces recommendations only -- no outreach is drafted "
        "or sent by this endpoint."
    ),
)
async def recommend_services(
    payload: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
    kb: KnowledgeBaseService = Depends(get_kb_service),
    user: User = Depends(get_current_user),
) -> RecommendationResponse:
    """Recommend services matching a stated need.

    Args:
        payload: The query and retrieval parameters.
        db: Active database session.
        kb: Knowledge base service.
        user: The authenticated caller.

    Returns:
        Ranked recommendations with rationales.
    """
    agent = ServiceRecommenderAgent(db=db, kb=kb)
    result = await agent.recommend(payload)
    logger.info(
        "Recommendation served",
        extra={"user_id": str(user.id), "results": len(result.recommendations)},
    )
    return result


@router.post(
    "/reindex",
    response_model=ReindexResponse,
    summary="Rebuild the knowledge base",
    description="Re-chunks and re-embeds every active service and public portfolio item.",
)
async def reindex_knowledge_base(
    kb: KnowledgeBaseService = Depends(get_kb_service),
    user: User = Depends(require_approver),
) -> ReindexResponse:
    """Rebuild all knowledge base embeddings.

    Restricted to approver roles because a full reindex is expensive and
    briefly degrades retrieval quality while it runs.

    Args:
        kb: Knowledge base service.
        user: The authenticated caller, who must hold an approver role.

    Returns:
        Counts of reindexed records and chunks.
    """
    summary = await kb.reindex_all()
    logger.info("Reindex triggered", extra={"user_id": str(user.id), **summary})
    return ReindexResponse(**summary)
