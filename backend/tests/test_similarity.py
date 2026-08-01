"""Tests for :mod:`app.services.similarity`."""

from __future__ import annotations

import math

import pytest

from app.services.similarity import (
    ScoredItem,
    cosine_similarity,
    deduplicate_by_key,
    distance_to_score,
    rank,
)


def test_identical_vectors_score_one() -> None:
    """A vector compared with itself scores exactly 1.0 after clamping."""
    vector = [0.3, 0.9, 0.1, 0.4]
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero() -> None:
    """Perpendicular vectors have zero similarity."""
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_opposite_vectors_score_negative_one() -> None:
    """Antiparallel vectors score -1.0."""
    assert cosine_similarity([1.0, 2.0], [-1.0, -2.0]) == pytest.approx(-1.0)


def test_magnitude_does_not_affect_similarity() -> None:
    """Cosine similarity depends on direction, not length."""
    assert cosine_similarity([1.0, 2.0], [10.0, 20.0]) == pytest.approx(1.0)


def test_zero_vector_scores_zero_rather_than_raising() -> None:
    """A zero-magnitude vector yields 0.0 instead of dividing by zero."""
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_result_never_exceeds_one() -> None:
    """Floating-point drift cannot push the score outside [-1, 1]."""
    vector = [1e-8] * 512
    assert -1.0 <= cosine_similarity(vector, vector) <= 1.0


def test_mismatched_lengths_raise() -> None:
    """Comparing different-width vectors is an error, not a silent truncation."""
    with pytest.raises(ValueError, match="length mismatch"):
        cosine_similarity([1.0, 2.0], [1.0])


def test_empty_vectors_raise() -> None:
    """Empty vectors have no defined direction."""
    with pytest.raises(ValueError, match="empty vectors"):
        cosine_similarity([], [])


def test_distance_to_score_inverts_pgvector_distance() -> None:
    """pgvector's cosine distance converts back to similarity."""
    assert distance_to_score(0.0) == pytest.approx(1.0)
    assert distance_to_score(1.0) == pytest.approx(0.0)
    assert distance_to_score(2.0) == pytest.approx(-1.0)


def test_rank_orders_by_descending_similarity() -> None:
    """The closest candidate comes first."""
    query = [1.0, 0.0]
    candidates = [
        ("orthogonal", [0.0, 1.0]),
        ("exact", [1.0, 0.0]),
        ("near", [0.9, 0.1]),
    ]
    results = rank(candidates, query, top_k=3, min_score=-1.0)

    assert [entry.item for entry in results] == ["exact", "near", "orthogonal"]
    assert results[0].score >= results[1].score >= results[2].score


def test_rank_respects_top_k() -> None:
    """No more than top_k results are returned."""
    candidates = [(f"item{i}", [float(i), 1.0]) for i in range(10)]
    assert len(rank(candidates, [1.0, 1.0], top_k=3, min_score=-1.0)) == 3


def test_rank_filters_below_min_score() -> None:
    """Candidates under the threshold are dropped."""
    candidates = [("good", [1.0, 0.0]), ("bad", [-1.0, 0.0])]
    results = rank(candidates, [1.0, 0.0], top_k=5, min_score=0.5)

    assert [entry.item for entry in results] == ["good"]


def test_rank_tie_break_is_stable() -> None:
    """Equally-scoring candidates keep their input order across calls."""
    candidates = [("first", [1.0, 0.0]), ("second", [2.0, 0.0]), ("third", [3.0, 0.0])]
    first_pass = [entry.item for entry in rank(candidates, [1.0, 0.0], top_k=3, min_score=-1.0)]
    second_pass = [entry.item for entry in rank(candidates, [1.0, 0.0], top_k=3, min_score=-1.0)]

    assert first_pass == second_pass == ["first", "second", "third"]


def test_rank_rejects_non_positive_top_k() -> None:
    """top_k must be positive."""
    with pytest.raises(ValueError, match="top_k must be positive"):
        rank([("a", [1.0])], [1.0], top_k=0)


def test_rank_on_empty_candidates_returns_empty() -> None:
    """Ranking nothing yields nothing rather than raising."""
    assert rank([], [1.0, 0.0], top_k=5) == []


def test_deduplicate_keeps_highest_score_per_key() -> None:
    """Multiple chunks of one service collapse to its best-scoring chunk."""
    items = [
        ScoredItem(item=("svc-a", "chunk1"), score=0.4),
        ScoredItem(item=("svc-a", "chunk2"), score=0.9),
        ScoredItem(item=("svc-b", "chunk1"), score=0.6),
    ]
    results = deduplicate_by_key(items, lambda pair: pair[0])

    assert [entry.item[0] for entry in results] == ["svc-a", "svc-b"]
    assert results[0].score == pytest.approx(0.9)


def test_deduplicate_returns_descending_order() -> None:
    """Deduplicated output is sorted best-first."""
    items = [
        ScoredItem(item="low", score=0.1),
        ScoredItem(item="high", score=0.95),
        ScoredItem(item="mid", score=0.5),
    ]
    scores = [entry.score for entry in deduplicate_by_key(items, lambda value: value)]

    assert scores == sorted(scores, reverse=True)


def test_ranking_matches_manual_computation() -> None:
    """rank() agrees with a hand-computed cosine similarity."""
    query = [3.0, 4.0]
    candidate = [4.0, 3.0]
    expected = (3 * 4 + 4 * 3) / (math.sqrt(25) * math.sqrt(25))

    result = rank([("c", candidate)], query, top_k=1, min_score=-1.0)
    assert result[0].score == pytest.approx(expected)
