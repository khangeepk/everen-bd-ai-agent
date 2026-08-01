"""Tests for :mod:`app.services.audit_scoring`."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from app.services.audit_scoring import (
    Finding,
    FindingCategory,
    Severity,
    count_by_severity,
    deduplicate,
    grade,
    health_score,
    prioritize,
    severity_for_score,
)


def _finding(code: str, severity: Severity, category: FindingCategory | None = None) -> Finding:
    """Build a Finding fixture.

    Args:
        code: Finding code.
        severity: Severity to assign.
        category: Optional category, defaults to performance.

    Returns:
        A :class:`Finding`.
    """
    return Finding(
        code=code,
        category=category or FindingCategory.PERFORMANCE,
        severity=severity,
        title=f"Issue {code}",
        detail="Detail text.",
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [(1.0, "A"), (0.90, "A"), (0.89, "B"), (0.80, "B"), (0.79, "C"), (0.50, "C"), (0.49, "D"),
     (0.30, "D"), (0.29, "F"), (0.0, "F")],
)
def test_grade_bands(score: float, expected: str) -> None:
    """Grades follow Lighthouse's banding thresholds."""
    assert grade(score) == expected


@pytest.mark.parametrize("score", [-0.01, 1.01, 2.0, -1.0])
def test_grade_rejects_out_of_range(score: float) -> None:
    """Scores outside [0, 1] are a programming error, not a grade."""
    with pytest.raises(ValueError, match="outside"):
        grade(score)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, Severity.INFO),
        (0.90, Severity.INFO),
        (0.85, Severity.LOW),
        (0.70, Severity.LOW),
        (0.60, Severity.MEDIUM),
        (0.50, Severity.MEDIUM),
        (0.40, Severity.HIGH),
        (0.30, Severity.HIGH),
        (0.20, Severity.CRITICAL),
        (0.0, Severity.CRITICAL),
    ],
)
def test_severity_bands(score: float, expected: Severity) -> None:
    """Severity escalates as the score falls."""
    assert severity_for_score(score) is expected


def test_severity_rejects_out_of_range() -> None:
    """Out-of-range scores raise."""
    with pytest.raises(ValueError, match="outside"):
        severity_for_score(1.5)


def test_prioritize_orders_most_severe_first() -> None:
    """Findings sort by descending severity."""
    findings = [
        _finding("low", Severity.LOW),
        _finding("critical", Severity.CRITICAL),
        _finding("medium", Severity.MEDIUM),
        _finding("high", Severity.HIGH),
        _finding("info", Severity.INFO),
    ]
    codes = [item.code for item in prioritize(findings)]

    assert codes == ["critical", "high", "medium", "low", "info"]


def test_prioritize_is_stable_for_ties() -> None:
    """Equal severities keep their input order across repeated calls."""
    findings = [_finding(f"f{i}", Severity.MEDIUM) for i in range(5)]
    first = [item.code for item in prioritize(findings)]
    second = [item.code for item in prioritize(findings)]

    assert first == second == ["f0", "f1", "f2", "f3", "f4"]


def test_prioritize_respects_limit() -> None:
    """Only the top N findings are returned."""
    findings = [_finding(f"f{i}", Severity.HIGH) for i in range(10)]
    assert len(prioritize(findings, limit=3)) == 3


def test_prioritize_rejects_non_positive_limit() -> None:
    """A limit must be positive."""
    with pytest.raises(ValueError, match="limit must be positive"):
        prioritize([], limit=0)


def test_prioritize_on_empty_returns_empty() -> None:
    """Prioritizing nothing yields nothing."""
    assert prioritize([]) == []


def test_deduplicate_keeps_most_severe_per_code() -> None:
    """The same issue from mobile and desktop runs collapses to one."""
    findings = [
        _finding("lighthouse_viewport", Severity.MEDIUM),
        _finding("lighthouse_viewport", Severity.CRITICAL),
        _finding("other", Severity.LOW),
    ]
    result = deduplicate(findings)

    assert len(result) == 2
    assert result[0].code == "lighthouse_viewport"
    assert result[0].severity is Severity.CRITICAL


def test_deduplicate_preserves_distinct_codes() -> None:
    """Different codes are all retained."""
    findings = [_finding(f"code{i}", Severity.MEDIUM) for i in range(4)]
    assert len(deduplicate(findings)) == 4


def test_deduplicate_returns_prioritized_order() -> None:
    """Deduplicated output is sorted most severe first."""
    findings = [
        _finding("a", Severity.LOW),
        _finding("b", Severity.CRITICAL),
        _finding("c", Severity.MEDIUM),
    ]
    assert [item.code for item in deduplicate(findings)] == ["b", "c", "a"]


def test_count_by_severity_includes_zeros() -> None:
    """Every severity appears in the tally, even at zero."""
    counts = count_by_severity([_finding("a", Severity.HIGH), _finding("b", Severity.HIGH)])

    assert counts[Severity.HIGH] == 2
    assert counts[Severity.CRITICAL] == 0
    assert set(counts) == set(Severity)


def test_health_score_of_perfect_site_is_one() -> None:
    """All-perfect categories give a perfect health score."""
    scores = {
        FindingCategory.PERFORMANCE: 1.0,
        FindingCategory.SECURITY: 1.0,
        FindingCategory.SEO: 1.0,
        FindingCategory.MOBILE: 1.0,
        FindingCategory.CONTACT_FORM: 1.0,
        FindingCategory.ACCESSIBILITY: 1.0,
    }
    assert health_score(scores) == pytest.approx(1.0)


def test_health_score_of_broken_site_is_zero() -> None:
    """All-zero categories give a zero health score."""
    scores = {FindingCategory.PERFORMANCE: 0.0, FindingCategory.SECURITY: 0.0}
    assert health_score(scores) == pytest.approx(0.0)


def test_health_score_weights_security_heavily() -> None:
    """Losing security hurts more than losing accessibility."""
    baseline = {
        FindingCategory.PERFORMANCE: 1.0,
        FindingCategory.SECURITY: 1.0,
        FindingCategory.ACCESSIBILITY: 1.0,
    }
    no_security = {**baseline, FindingCategory.SECURITY: 0.0}
    no_accessibility = {**baseline, FindingCategory.ACCESSIBILITY: 0.0}

    assert health_score(no_security) < health_score(no_accessibility)


def test_health_score_handles_partial_categories() -> None:
    """A subset of categories still produces a sensible weighted mean."""
    assert health_score({FindingCategory.PERFORMANCE: 0.5}) == pytest.approx(0.5)


def test_health_score_with_no_known_categories_is_zero() -> None:
    """Unweighted categories alone give 0.0 rather than dividing by zero."""
    assert health_score({FindingCategory.BROKEN_LINKS: 1.0}) == 0.0


def test_health_score_on_empty_input_is_zero() -> None:
    """No categories gives 0.0."""
    assert health_score({}) == 0.0


def test_health_score_rejects_out_of_range() -> None:
    """An impossible score raises."""
    with pytest.raises(ValueError, match="outside"):
        health_score({FindingCategory.PERFORMANCE: 1.5})


def test_findings_are_immutable() -> None:
    """Finding is frozen so audit results cannot be edited after the fact."""
    finding = _finding("a", Severity.HIGH)

    with pytest.raises(FrozenInstanceError):
        finding.severity = Severity.LOW  # type: ignore[misc]
