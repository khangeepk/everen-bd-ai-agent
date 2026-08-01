"""Shared pytest fixtures.

Uses an in-memory SQLite database so the suite runs without a PostgreSQL
server. Vector columns degrade to JSON text on SQLite (see
:mod:`app.db.types`), so similarity ranking is asserted directly against
:mod:`app.services.similarity` rather than through pgvector operators.

All external services are faked -- no network calls (AGENTS.md section 11).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db import models  # noqa: F401 - registers models on Base.metadata


class FakeEmbeddingClient:
    """Deterministic embedding client for tests.

    Produces a normalized bag-of-characters vector, so semantically similar
    strings land near each other without any network call.
    """

    def __init__(self, dimension: int | None = None) -> None:
        """Initialize the fake.

        Args:
            dimension: Width of the generated vectors. Defaults to settings.embedding_dimension.
        """
        from app.core.config import settings

        self.dimension = dimension or settings.embedding_dimension
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return deterministic vectors for the given texts.

        Args:
            texts: Input strings.

        Returns:
            One vector per input.
        """
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for char in text.lower():
                if char.isalnum():
                    vector[ord(char) % self.dimension] += 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def fake_embedder() -> FakeEmbeddingClient:
    """Provide a fresh fake embedding client.

    Returns:
        A :class:`FakeEmbeddingClient`.
    """
    return FakeEmbeddingClient()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Provide a session bound to a fresh in-memory SQLite database.

    Yields:
        An open :class:`AsyncSession` with all tables created.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
