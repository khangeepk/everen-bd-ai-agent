"""Tests for :mod:`app.services.lead_scoring`.

The central behavior under test: a triggered ComplianceGate forces
DO_NOT_CONTACT regardless of how high the other four components score. That is
the fix applied to the originally specified formula, where ComplianceRisk at a
10% weight could never on its own prevent a lead from surfacing as Hot.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services.lead_scoring import (
    HOT_THRESHOLD,
    WARM_THRESHOLD,
    WEIGHTS,
    ComplianceGate,
    ComponentScore,
    ScoreBreakdown,
    ScoreLabel,
    label_for,
    score_lead,
    weighted_total,
)


def _breakdown(
    need: float = 0.5,
    fit: float = 0.5,
    contactability: float = 0.5,
    revenue: float = 0.5,
    compliance: float = 0.5,
    gate_triggered: bool = False,
) -> ScoreBreakdown:
    """Build a ScoreBreakdown fixture.

    Args:
        need: Need component value.
        fit: Fit component value.
        contactability: Contactability component value.
        revenue: Revenue component value.
        compliance: Compliance component value.
        gate_triggered: Whether the compliance gate should trigger.

    Returns:
        A :class:`ScoreBreakdown`.
    """
    gate = (
        ComplianceGate(triggered=True, reasons=("test gate",))
        if gate_triggered
        else ComplianceGate(triggered=False)
    )
    return ScoreBreakdown(
        need=ComponentScore(value=need),
        fit=ComponentScore(value=fit),
        contactability=ComponentScore(value=contactability),
        revenue=ComponentScore(value=revenue),
        compliance=ComponentScore(value=compliance),
        gate=gate,
    )


def test_weights_sum_to_one() -> None:
    """The specified weights (30/25/20/15/10) sum to 1.0."""
    assert WEIGHTS == {
        "need": 0.30,
        "fit": 0.25,
        "contactability": 0.20,
        "revenue": 0.15,
        "compliance": 0.10,
    }
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_weighted_total_matches_hand_computation() -> None:
    """The formula matches a manually computed weighted sum."""
    breakdown = _breakdown(need=0.8, fit=0.6, contactability=0.9, revenue=0.4, compliance=1.0)
    expected = 0.30 * 0.8 + 0.25 * 0.6 + 0.20 * 0.9 + 0.15 * 0.4 + 0.10 * 1.0

    assert weighted_total(breakdown) == pytest.approx(expected)


def test_all_perfect_components_score_one() -> None:
    """A lead perfect on every component totals 1.0."""
    breakdown = _breakdown(1.0, 1.0, 1.0, 1.0, 1.0)
    assert weighted_total(breakdown) == pytest.approx(1.0)


def test_all_zero_components_score_zero() -> None:
    """A lead scoring zero everywhere totals 0.0."""
    breakdown = _breakdown(0.0, 0.0, 0.0, 0.0, 0.0)
    assert weighted_total(breakdown) == pytest.approx(0.0)


def test_need_has_more_influence_than_revenue() -> None:
    """Need (30%) moves the total more than Revenue (15%) for equal deltas."""
    baseline = _breakdown(0.5, 0.5, 0.5, 0.5, 0.5)
    boosted_need = _breakdown(1.0, 0.5, 0.5, 0.5, 0.5)
    boosted_revenue = _breakdown(0.5, 0.5, 0.5, 1.0, 0.5)

    need_delta = weighted_total(boosted_need) - weighted_total(baseline)
    revenue_delta = weighted_total(boosted_revenue) - weighted_total(baseline)

    assert need_delta > revenue_delta


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (1.0, ScoreLabel.HOT),
        (HOT_THRESHOLD, ScoreLabel.HOT),
        (HOT_THRESHOLD - 0.0001, ScoreLabel.WARM),
        (WARM_THRESHOLD, ScoreLabel.WARM),
        (WARM_THRESHOLD - 0.0001, ScoreLabel.COLD),
        (0.0, ScoreLabel.COLD),
    ],
)
def test_label_banding_without_gate(total: float, expected: ScoreLabel) -> None:
    """Labels band correctly at and around the threshold boundaries."""
    assert label_for(total, ComplianceGate(triggered=False)) is expected


def test_label_rejects_out_of_range_total() -> None:
    """An impossible total raises rather than silently banding."""
    with pytest.raises(ValueError, match="outside"):
        label_for(1.5, ComplianceGate(triggered=False))


def test_triggered_gate_overrides_a_perfect_score() -> None:
    """This is the core fix: gate wins even at total_score = 1.0."""
    assert label_for(1.0, ComplianceGate(triggered=True, reasons=("suppressed",))) is (
        ScoreLabel.DO_NOT_CONTACT
    )


def test_triggered_gate_overrides_every_band() -> None:
    """The gate wins regardless of where the total would otherwise land."""
    gate = ComplianceGate(triggered=True, reasons=("suppressed",))

    for total in (1.0, HOT_THRESHOLD, WARM_THRESHOLD, 0.0):
        assert label_for(total, gate) is ScoreLabel.DO_NOT_CONTACT


def test_end_to_end_hot_lead_with_no_compliance_concerns() -> None:
    """A strong lead with clean compliance scores Hot."""
    breakdown = _breakdown(need=0.9, fit=0.9, contactability=0.9, revenue=0.8, compliance=1.0)
    result = score_lead(breakdown)

    assert result.label is ScoreLabel.HOT
    assert result.total_score >= HOT_THRESHOLD


def test_end_to_end_gate_beats_a_near_perfect_lead() -> None:
    """The scenario the original weighted-only formula got wrong.

    Need/Fit/Contactability/Revenue are all excellent (0.95), which alone
    would total roughly 0.9 (comfortably Hot) even with ComplianceRisk at
    0.0. Because the gate is evaluated first, this lead is Do-Not-Contact,
    not Hot.
    """
    breakdown = _breakdown(
        need=0.95,
        fit=0.95,
        contactability=0.95,
        revenue=0.95,
        compliance=0.0,
        gate_triggered=True,
    )
    result = score_lead(breakdown)

    assert result.label is ScoreLabel.DO_NOT_CONTACT
    # The stored total still reflects the honest weighted formula, including
    # the zeroed compliance component -- the gate overrides the label, not
    # the underlying number.
    assert result.total_score < 1.0
    assert result.total_score == pytest.approx(weighted_total(breakdown))


def test_end_to_end_weak_compliance_without_gate_still_bands_normally() -> None:
    """Sub-threshold compliance risk pulls the score down but doesn't gate it.

    This is the "10% weight" half of the design: when do_not_contact is not
    set, a mediocre compliance score still only contributes its 10% share.
    """
    breakdown = _breakdown(need=0.8, fit=0.8, contactability=0.8, revenue=0.8, compliance=0.3)
    result = score_lead(breakdown)

    assert result.label is not ScoreLabel.DO_NOT_CONTACT
    assert result.total_score == pytest.approx(weighted_total(breakdown))


def test_component_score_rejects_out_of_range() -> None:
    """A component value outside [0, 1] is a programming error."""
    with pytest.raises(ValueError, match="outside"):
        ComponentScore(value=1.5)

    with pytest.raises(ValueError, match="outside"):
        ComponentScore(value=-0.1)


def test_triggered_gate_requires_a_reason() -> None:
    """A Do-Not-Contact decision must be auditable."""
    with pytest.raises(ValueError, match="must carry at least one reason"):
        ComplianceGate(triggered=True, reasons=())


def test_untriggered_gate_does_not_require_a_reason() -> None:
    """A clean gate needs no explanation."""
    gate = ComplianceGate(triggered=False)
    assert gate.reasons == ()


def test_breakdown_and_result_are_immutable() -> None:
    """Frozen dataclasses prevent editing a score after computation."""
    breakdown = _breakdown()
    result = score_lead(breakdown)

    with pytest.raises(FrozenInstanceError):
        result.total_score = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        breakdown.need = ComponentScore(value=1.0)  # type: ignore[misc]


def test_weighted_total_is_rounded_and_bounded() -> None:
    """The total is clamped and rounded, never a raw float artifact."""
    breakdown = _breakdown(1.0, 1.0, 1.0, 1.0, 1.0)
    total = weighted_total(breakdown)

    assert total == round(total, 4)
    assert 0.0 <= total <= 1.0
