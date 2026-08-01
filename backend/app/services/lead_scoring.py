"""Lead scoring formula: weighted composite score with a compliance gate.

Standard library only, so the formula, gate, and banding logic are
unit-testable without a database, an LLM, or any network access. Deriving the
five component scores from real lead/audit/social data lives in
:mod:`app.services.lead_signals`, which is DB-aware and depends on this module.

Formula
-------
::

    total = 0.30 * need + 0.25 * fit + 0.20 * contactability
          + 0.15 * revenue + 0.10 * compliance

Design decision -- ComplianceRisk is a gate, not just a weight.

A pure weighted sum lets strong Need/Fit/Contactability/Revenue scores
outvote a failing ComplianceRisk: a lead scoring ~0.90 on the other four
components still totals ~0.81 even if ComplianceRisk is 0.0, because that
component is only 10% of the sum. That would let a suppressed contact, a
withdrawn-consent lead, or one with no lawful basis for outreach surface as
"Hot" and enter an outreach queue.

So :func:`score_lead` treats a triggered :class:`ComplianceGate` as an
absolute override: the label becomes ``DO_NOT_CONTACT`` regardless of the
weighted total, before any banding is applied. The 10% weight is still
applied to the total when the gate does *not* trigger, so residual,
sub-threshold compliance risk continues to pull the score down as specified.
This is the recommended interpretation of the spec, not the only one --
see the module docstring in ``app/services/lead_signals.py`` for what feeds
the gate.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Component weights. Must sum to 1.0 -- enforced by a module-load assertion
#: rather than a runtime check, since these are a code constant, not input.
WEIGHTS: dict[str, float] = {
    "need": 0.30,
    "fit": 0.25,
    "contactability": 0.20,
    "revenue": 0.15,
    "compliance": 0.10,
}

_WEIGHT_SUM_TOLERANCE = 1e-9
if abs(sum(WEIGHTS.values()) - 1.0) > _WEIGHT_SUM_TOLERANCE:
    raise AssertionError(f"WEIGHTS must sum to 1.0, got {sum(WEIGHTS.values())}")

#: Banding thresholds against the total score in [0.0, 1.0].
HOT_THRESHOLD = 0.75
WARM_THRESHOLD = 0.50

#: Formula version stamped onto every stored score. Bump when WEIGHTS,
#: thresholds, or the gate logic change, so historical scores stay
#: interpretable against the rules that actually produced them.
FORMULA_VERSION = "1.0.0"


class ScoreLabel(str, enum.Enum):
    """The business-facing outcome of a lead score."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    DO_NOT_CONTACT = "do_not_contact"


@dataclass(frozen=True)
class ComponentScore:
    """One weighted component of the total score.

    Attributes:
        value: Normalized score in ``[0.0, 1.0]``. Higher is always better --
            for ``compliance`` this means lower risk, not higher risk.
        reasons: Short evidence strings explaining the value, for display and
            for debugging why a lead scored the way it did.
    """

    value: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate the score is normalized.

        Raises:
            ValueError: If ``value`` is outside ``[0.0, 1.0]``.
        """
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"component score {self.value} is outside [0.0, 1.0]")


@dataclass(frozen=True)
class ComplianceGate:
    """Whether outreach to this lead is blocked outright.

    Attributes:
        triggered: True if this lead must never be contacted regardless of
            its other scores.
        reasons: Why the gate triggered, e.g. ``"explicit do-not-contact flag"``.
            Empty when the gate does not trigger.
    """

    triggered: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate that a triggered gate always carries a reason.

        Raises:
            ValueError: If ``triggered`` is True but ``reasons`` is empty. A
                Do-Not-Contact decision with no recorded reason is not
                auditable, which defeats the point of storing it.
        """
        if self.triggered and not self.reasons:
            raise ValueError("a triggered ComplianceGate must carry at least one reason")


@dataclass(frozen=True)
class ScoreBreakdown:
    """The five inputs to the composite score.

    Attributes:
        need: How much the business appears to need Everen's services.
        fit: How well the business matches Everen's service offerings.
        contactability: How reachable and verified the lead's contact
            channels are.
        revenue: Estimated deal-size potential.
        compliance: Residual compliance risk score (higher = lower risk).
            Contributes its 10% weight only when ``gate.triggered`` is False.
        gate: Hard override. When triggered, the label is
            :attr:`ScoreLabel.DO_NOT_CONTACT` regardless of the weighted total.
    """

    need: ComponentScore
    fit: ComponentScore
    contactability: ComponentScore
    revenue: ComponentScore
    compliance: ComponentScore
    gate: ComplianceGate = field(default_factory=lambda: ComplianceGate(triggered=False))


@dataclass(frozen=True)
class LeadScoreResult:
    """The fully computed score for one lead.

    Attributes:
        total_score: Weighted composite in ``[0.0, 1.0]``.
        label: The banded business label.
        breakdown: The inputs the score was computed from.
        formula_version: Which version of the formula produced this result.
    """

    total_score: float
    label: ScoreLabel
    breakdown: ScoreBreakdown
    formula_version: str = FORMULA_VERSION


def weighted_total(breakdown: ScoreBreakdown) -> float:
    """Compute the weighted composite score.

    The compliance weight is applied whether or not the gate has triggered --
    the gate overrides the *label*, not the underlying number, so the stored
    total remains a faithful record of the weighted formula even for a
    Do-Not-Contact lead.

    Args:
        breakdown: The five component scores.

    Returns:
        The weighted total in ``[0.0, 1.0]``, rounded to 4 places.
    """
    total = (
        WEIGHTS["need"] * breakdown.need.value
        + WEIGHTS["fit"] * breakdown.fit.value
        + WEIGHTS["contactability"] * breakdown.contactability.value
        + WEIGHTS["revenue"] * breakdown.revenue.value
        + WEIGHTS["compliance"] * breakdown.compliance.value
    )
    return round(min(max(total, 0.0), 1.0), 4)


def label_for(total_score: float, gate: ComplianceGate) -> ScoreLabel:
    """Band a weighted total into a business-facing label.

    Args:
        total_score: The weighted composite score.
        gate: The compliance gate. A triggered gate forces
            :attr:`ScoreLabel.DO_NOT_CONTACT` regardless of ``total_score``.

    Returns:
        The label.

    Raises:
        ValueError: If ``total_score`` is outside ``[0.0, 1.0]``.
    """
    if not 0.0 <= total_score <= 1.0:
        raise ValueError(f"total_score {total_score} is outside [0.0, 1.0]")

    if gate.triggered:
        return ScoreLabel.DO_NOT_CONTACT
    if total_score >= HOT_THRESHOLD:
        return ScoreLabel.HOT
    if total_score >= WARM_THRESHOLD:
        return ScoreLabel.WARM
    return ScoreLabel.COLD


def score_lead(breakdown: ScoreBreakdown) -> LeadScoreResult:
    """Compute the full score result for a lead.

    Args:
        breakdown: The five component scores and compliance gate.

    Returns:
        The composite result: total score, label, and the breakdown it came
        from.
    """
    total = weighted_total(breakdown)
    label = label_for(total, breakdown.gate)

    if breakdown.gate.triggered:
        # WARNING, not INFO: this overrides whatever the weighted total says
        # and can silently pull a lead out of an active outreach queue, so it
        # is worth a reviewer's attention rather than blending into routine
        # scoring activity.
        logger.warning(
            "Compliance gate triggered; label forced to do_not_contact regardless of "
            "weighted total",
            extra={
                "total_score": total,
                "gate_reasons": list(breakdown.gate.reasons),
            },
        )

    logger.info(
        "Lead scored",
        extra={
            "total_score": total,
            "label": label.value,
            "gate_triggered": breakdown.gate.triggered,
        },
    )
    return LeadScoreResult(total_score=total, label=label, breakdown=breakdown)
