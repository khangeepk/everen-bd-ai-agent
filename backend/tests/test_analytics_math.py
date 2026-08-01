"""Tests for :mod:`app.services.analytics_math`."""

from __future__ import annotations

import pytest

from app.services.analytics_math import (
    RankedItem,
    VariantPerformance,
    rank_variants_by_reply_rate,
    safe_rate,
    top_n,
)


def test_safe_rate_normal_division() -> None:
    """Ordinary division works as expected."""
    assert safe_rate(25, 100) == 0.25


def test_safe_rate_zero_denominator_is_zero_not_error() -> None:
    """No opportunities means a 0.0 rate, not a crash."""
    assert safe_rate(0, 0) == 0.0


def test_safe_rate_rejects_negative_numerator() -> None:
    """A negative count indicates a bug upstream."""
    with pytest.raises(ValueError, match="must not be negative"):
        safe_rate(-1, 10)


def test_safe_rate_rejects_negative_denominator() -> None:
    """Same for the denominator."""
    with pytest.raises(ValueError, match="must not be negative"):
        safe_rate(1, -10)


def test_top_n_orders_highest_first() -> None:
    """The ranking is descending by count."""
    result = top_n({"retail": 3, "food_service": 10, "legal": 5}, n=3)
    assert [item.label for item in result] == ["food_service", "legal", "retail"]


def test_top_n_breaks_ties_alphabetically() -> None:
    """Equal counts resolve deterministically by label."""
    result = top_n({"zebra": 5, "alpha": 5}, n=2)
    assert [item.label for item in result] == ["alpha", "zebra"]


def test_top_n_truncates_to_requested_size() -> None:
    """Only the requested number of entries is returned."""
    result = top_n({"a": 1, "b": 2, "c": 3, "d": 4}, n=2)
    assert len(result) == 2
    assert result[0] == RankedItem(label="d", count=4)


def test_top_n_excludes_blank_labels_by_default() -> None:
    """An uncategorized lead should not appear as a fake top category."""
    result = top_n({"": 100, "retail": 1}, n=5)
    assert [item.label for item in result] == ["retail"]


def test_top_n_can_include_blank_labels_when_asked() -> None:
    """Opting out of the blank-label filter is supported."""
    result = top_n({"": 100, "retail": 1}, n=5, exclude_blank=False)
    assert result[0].label == ""


def test_top_n_rejects_non_positive_n() -> None:
    """A zero or negative N is a caller error."""
    with pytest.raises(ValueError, match="n must be positive"):
        top_n({"a": 1}, n=0)


def _variant(
    variant_id: str, label: str, sent: int, opened: int, replied: int
) -> VariantPerformance:
    """Build a VariantPerformance with only the fields these tests exercise.

    Args:
        variant_id: The variant identifier.
        label: Human-readable label.
        sent: Sends under this variant.
        opened: Distinct opens.
        replied: Distinct replies.

    Returns:
        The constructed performance record.
    """
    return VariantPerformance(
        variant_id=variant_id,
        label=label,
        sent=sent,
        opened=opened,
        replied=replied,
        meetings_booked=0,
        deals_won=0,
    )


def test_variant_performance_computes_rates() -> None:
    """Rate properties divide correctly."""
    variant = _variant("v1", "A", sent=100, opened=40, replied=10)
    assert variant.open_rate == 0.4
    assert variant.reply_rate == 0.1


def test_variant_performance_handles_zero_sent() -> None:
    """An unused variant reports 0.0 rates, not a division error."""
    variant = _variant("v1", "A", sent=0, opened=0, replied=0)
    assert variant.open_rate == 0.0
    assert variant.reply_rate == 0.0


def test_rank_variants_by_reply_rate_orders_highest_first() -> None:
    """The better-performing variant sorts first."""
    a = _variant("v1", "A", sent=100, opened=50, replied=20)
    b = _variant("v2", "B", sent=100, opened=60, replied=30)
    ranked = rank_variants_by_reply_rate([a, b])
    assert [v.variant_id for v in ranked] == ["v2", "v1"]


def test_rank_variants_excludes_tiny_samples() -> None:
    """A variant with too few sends to be meaningful is filtered out."""
    tiny = _variant("v1", "A", sent=1, opened=1, replied=1)
    solid = _variant("v2", "B", sent=100, opened=50, replied=20)
    ranked = rank_variants_by_reply_rate([tiny, solid], min_sent=10)
    assert [v.variant_id for v in ranked] == ["v2"]


def test_rank_variants_ties_break_by_sample_size_then_label() -> None:
    """Equal reply rates prefer the larger sample, then alphabetical label."""
    small = _variant("v1", "B", sent=10, opened=5, replied=2)
    large = _variant("v2", "A", sent=100, opened=50, replied=20)
    ranked = rank_variants_by_reply_rate([small, large])
    assert ranked[0].variant_id == "v2"
