"""Embedding client wrapper.

Thin async facade over the OpenAI embeddings API. Defined behind a Protocol so
services depend on the interface, not the vendor -- tests inject a fake and
never touch the network (AGENTS.md section 11).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Embeddings API calls are batched at this size.
MAX_BATCH_SIZE = 128


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider fails or returns an unusable shape."""


@runtime_checkable
class EmbeddingClient(Protocol):
    """Interface for anything that can turn text into vectors."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: Input strings.

        Returns:
            One vector per input, in the same order.
        """
        ...


class OpenAIEmbeddingClient:
    """Embedding client backed by the OpenAI embeddings API."""

    def __init__(self, model: str | None = None, dimension: int | None = None) -> None:
        """Initialize the client.

        Args:
            model: Embedding model name. Defaults to ``settings.embedding_model``.
            dimension: Expected vector width. Defaults to
                ``settings.embedding_dimension``.
        """
        self.model = model or settings.embedding_model
        self.dimension = dimension or settings.embedding_dimension
        self._client = None

    def _get_client(self):
        """Lazily construct the AsyncOpenAI client.

        Deferred so that importing this module does not require the OpenAI SDK
        or a valid API key.

        Returns:
            An ``openai.AsyncOpenAI`` instance.

        Raises:
            EmbeddingError: If the OpenAI SDK is not installed.
        """
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover
                raise EmbeddingError("The 'openai' package is not installed") from exc
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts, batching requests to stay within API limits.

        Args:
            texts: Input strings. Empty input returns an empty list.

        Returns:
            One vector per input, order preserved.

        Raises:
            EmbeddingError: If the provider errors or returns a vector whose
                width does not match the configured dimension.
        """
        if not texts:
            return []

        client = self._get_client()
        vectors: list[list[float]] = []

        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = list(texts[start : start + MAX_BATCH_SIZE])
            try:
                response = await client.embeddings.create(model=self.model, input=batch)
            except Exception as exc:
                logger.exception(
                    "Embedding request failed",
                    extra={"model": self.model, "batch_size": len(batch)},
                )
                raise EmbeddingError(f"Embedding request failed: {exc}") from exc

            for entry in response.data:
                vector = list(entry.embedding)
                if len(vector) != self.dimension:
                    raise EmbeddingError(
                        f"Model {self.model} returned {len(vector)} dimensions, "
                        f"expected {self.dimension}"
                    )
                vectors.append(vector)

        logger.info(
            "Embedded texts", extra={"count": len(vectors), "model": self.model}
        )
        return vectors
