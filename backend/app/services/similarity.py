"""Vector similarity helpers and result fusion.

Standard library only. In production, top-k retrieval runs inside PostgreSQL
via pgvector's ``<=>`` cosine-distance operator with an HNSW index -- these
functions exist to convert pgvector distances into scores, to re-rank fused
result sets, and to provide an exact reference implementation that the unit
tests check the ranking logic against.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class ScoredItem(Generic[T]):
    """An item paired with its similarity score.

    Attributes:
        item: The retrieved object.
        score: Cosine similarity in ``[-1.0, 1.0]``; higher is more similar.
    """

    item: T
    score: float


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Similarity in ``[-1.0, 1.0]``. Returns 0.0 if either vector has zero
        magnitude, since direction is undefined in that case.

    Raises:
        ValueError: If the vectors have different lengths or are empty.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector length mismatch: {len(a)} != {len(b)}")
    if not a:
        raise ValueError("Cannot compute similarity on empty vectors")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    # Clamp: floating-point error can push an identical pair marginally past 1.
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def distance_to_score(distance: float) -> float:
    """Convert a pgvector cosine distance into a similarity score.

    pgvector's ``<=>`` returns ``1 - cosine_similarity``.

    Args:
        distance: Cosine distance from pgvector, in ``[0.0, 2.0]``.

    Returns:
        The equivalent cosine similarity.
    """
    return 1.0 - distance


def rank(
    candidates: Sequence[tuple[T, Sequence[float]]],
    query_vector: Sequence[float],
    *,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[ScoredItem[T]]:
    """Rank candidates against a query vector by cosine similarity.

    Ties are broken deterministically by the candidate's original position, so
    repeated calls return a stable order.

    Args:
        candidates: Pairs of ``(item, embedding)``.
        query_vector: The embedded query.
        top_k: Maximum number of results to return.
        min_score: Discard results scoring below this threshold.

    Returns:
        Up to ``top_k`` items sorted by descending score.

    Raises:
        ValueError: If ``top_k`` is not positive.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    scored: list[tuple[int, ScoredItem[T]]] = []
    for position, (item, vector) in enumerate(candidates):
        score = cosine_similarity(query_vector, vector)
        if score >= min_score:
            scored.append((position, ScoredItem(item=item, score=score)))

    scored.sort(key=lambda pair: (-pair[1].score, pair[0]))
    results = [entry for _, entry in scored[:top_k]]

    logger.info(
        "Ranked candidates",
        extra={"candidates": len(candidates), "returned": len(results), "top_k": top_k},
    )
    return results


def deduplicate_by_key(
    items: Sequence[ScoredItem[T]], key_fn: Callable[[T], Hashable]
) -> list[ScoredItem[T]]:
    """Collapse results sharing a key, keeping the highest-scoring one.

    Several chunks often belong to the same service; the recommendation layer
    wants one entry per service, scored by its best-matching chunk.

    Args:
        items: Scored results, in any order.
        key_fn: Callable mapping an item to a hashable grouping key.

    Returns:
        Deduplicated results sorted by descending score.
    """
    best: dict[Hashable, ScoredItem[T]] = {}
    for entry in items:
        key = key_fn(entry.item)
        current = best.get(key)
        if current is None or entry.score > current.score:
            best[key] = entry
    return sorted(best.values(), key=lambda entry: -entry.score)
