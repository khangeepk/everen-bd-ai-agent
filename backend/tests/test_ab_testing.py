"""Tests for :mod:`app.services.ab_testing`."""

from __future__ import annotations

import pytest

from app.services.ab_testing import (
    NoVariantsError,
    PromptVariant,
    choose_variant,
    deterministic_fraction,
)


def test_deterministic_fraction_is_stable_for_the_same_key() -> None:
    """The same key always maps to the same fraction."""
    assert deterministic_fraction("lead-123") == deterministic_fraction("lead-123")


def test_deterministic_fraction_differs_across_keys() -> None:
    """Distinct keys are not all collapsed to the same bucket."""
    assert deterministic_fraction("lead-123") != deterministic_fraction("lead-456")


def test_deterministic_fraction_is_within_unit_interval() -> None:
    """The fraction is always in [0.0, 1.0)."""
    for key in ("a", "b", "some-uuid-like-string", ""):
        fraction = deterministic_fraction(key)
        assert 0.0 <= fraction < 1.0


def test_variant_rejects_non_positive_weight() -> None:
    """A zero or negative weight is a misconfiguration."""
    with pytest.raises(ValueError, match="weight must be positive"):
        PromptVariant(variant_id="v1", label="A", weight=0.0)


def test_choose_variant_rejects_empty_list() -> None:
    """No candidates means nothing can be assigned."""
    with pytest.raises(NoVariantsError):
        choose_variant("lead-123", [])


def test_choose_variant_with_single_candidate_always_returns_it() -> None:
    """No experiment is running if only one active version exists."""
    only = PromptVariant(variant_id="v1", label="A")
    assert choose_variant("lead-123", [only]) is only


def test_choose_variant_is_deterministic_for_the_same_key() -> None:
    """Repeated assignment of the same lead always lands on the same variant."""
    variants = [
        PromptVariant(variant_id="v1", label="A"),
        PromptVariant(variant_id="v2", label="B"),
    ]
    first = choose_variant("lead-123", variants)
    second = choose_variant("lead-123", variants)
    assert first.variant_id == second.variant_id


def test_choose_variant_distributes_roughly_evenly_across_many_keys() -> None:
    """An even 50/50 weighting splits a large population close to 50/50."""
    variants = [
        PromptVariant(variant_id="v1", label="A"),
        PromptVariant(variant_id="v2", label="B"),
    ]
    counts = {"v1": 0, "v2": 0}
    n = 2000
    for i in range(n):
        chosen = choose_variant(f"lead-{i}", variants)
        counts[chosen.variant_id] += 1

    # Not an exact 50/50 -- assert it's within a generous tolerance rather
    # than asserting exact balance, since this is a hash-based split, not a
    # perfectly uniform generator.
    ratio = counts["v1"] / n
    assert 0.40 <= ratio <= 0.60


def test_choose_variant_respects_weighting() -> None:
    """A 9:1 weight split skews assignment heavily toward the heavier variant."""
    variants = [
        PromptVariant(variant_id="v1", label="A", weight=9.0),
        PromptVariant(variant_id="v2", label="B", weight=1.0),
    ]
    counts = {"v1": 0, "v2": 0}
    n = 2000
    for i in range(n):
        chosen = choose_variant(f"lead-{i}", variants)
        counts[chosen.variant_id] += 1

    ratio = counts["v1"] / n
    assert ratio >= 0.80


def test_choose_variant_always_returns_a_known_variant_id() -> None:
    """The result is always one of the supplied variants, never a stray value."""
    variants = [
        PromptVariant(variant_id="v1", label="A", weight=1.0),
        PromptVariant(variant_id="v2", label="B", weight=2.0),
        PromptVariant(variant_id="v3", label="C", weight=1.0),
    ]
    valid_ids = {v.variant_id for v in variants}
    for i in range(500):
        chosen = choose_variant(f"lead-{i}", variants)
        assert chosen.variant_id in valid_ids
