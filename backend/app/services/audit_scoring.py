"""Audit finding model, severity classification, and health scoring.

Standard library only, so grading and prioritization logic is unit-testable
without network access, a browser, or a database.

Scores follow the Lighthouse convention of 0.0-1.0 and are banded into letter
grades using Lighthouse's own thresholds (>=0.90 good, >=0.50 needs
improvement, below that poor) so the report agrees with what a prospect sees if
they run PageSpeed Insights themselves.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Lighthouse's own banding thresholds.
GOOD_THRESHOLD = 0.90
NEEDS_IMPROVEMENT_THRESHOLD = 0.50


class Severity(str, enum.Enum):
    """How much a finding should worry the business owner."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


#: Ordering weight for sorting, highest first.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class FindingCategory(str, enum.Enum):
    """Which part of the audit produced a finding."""

    PERFORMANCE = "performance"
    SEO = "seo"
    ACCESSIBILITY = "accessibility"
    BEST_PRACTICES = "best_practices"
    SECURITY = "security"
    MOBILE = "mobile"
    BROKEN_LINKS = "broken_links"
    CONTACT_FORM = "contact_form"
    SOCIAL = "social"


@dataclass(frozen=True)
class Finding:
    """One issue discovered during an audit.

    Attributes:
        code: Stable machine-readable identifier, e.g. ``"no_https"``.
        category: Which audit area produced it.
        severity: How urgent it is.
        title: Short business-readable headline.
        detail: One or two sentences of plain-language explanation.
        evidence: Concrete supporting data, e.g. a list of broken URLs.
        score: Underlying 0.0-1.0 score where one exists.
    """

    code: str
    category: FindingCategory
    severity: Severity
    title: str
    detail: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    score: float | None = None


def grade(score: float) -> str:
    """Band a 0.0-1.0 score into a letter grade.

    Args:
        score: Normalized score.

    Returns:
        One of ``"A"``, ``"B"``, ``"C"``, ``"D"``, or ``"F"``.

    Raises:
        ValueError: If ``score`` is outside ``[0.0, 1.0]``.
    """
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score {score} is outside [0.0, 1.0]")

    if score >= GOOD_THRESHOLD:
        return "A"
    if score >= 0.80:
        return "B"
    if score >= NEEDS_IMPROVEMENT_THRESHOLD:
        return "C"
    if score >= 0.30:
        return "D"
    return "F"


def severity_for_score(score: float) -> Severity:
    """Map a 0.0-1.0 category score onto a severity.

    Args:
        score: Normalized score.

    Returns:
        The corresponding severity.

    Raises:
        ValueError: If ``score`` is outside ``[0.0, 1.0]``.
    """
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score {score} is outside [0.0, 1.0]")

    if score >= GOOD_THRESHOLD:
        return Severity.INFO
    if score >= 0.70:
        return Severity.LOW
    if score >= NEEDS_IMPROVEMENT_THRESHOLD:
        return Severity.MEDIUM
    if score >= 0.30:
        return Severity.HIGH
    return Severity.CRITICAL


def prioritize(findings: Sequence[Finding], limit: int | None = None) -> list[Finding]:
    """Sort findings by urgency, most severe first.

    Ties break on the original position so repeated calls are stable.

    Args:
        findings: Findings in any order.
        limit: Optional cap on how many to return.

    Returns:
        Findings sorted by descending severity.

    Raises:
        ValueError: If ``limit`` is not positive.
    """
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")

    ordered = sorted(
        enumerate(findings), key=lambda pair: (-_SEVERITY_RANK[pair[1].severity], pair[0])
    )
    result = [finding for _, finding in ordered]
    return result[:limit] if limit is not None else result


def deduplicate(findings: Sequence[Finding]) -> list[Finding]:
    """Collapse findings sharing a code, keeping the most severe.

    The website audit can surface the same issue from both the mobile and
    desktop PageSpeed runs; the report should mention it once.

    Args:
        findings: Findings in any order.

    Returns:
        One finding per code, ordered by descending severity.
    """
    best: dict[str, Finding] = {}
    for finding in findings:
        current = best.get(finding.code)
        if current is None or _SEVERITY_RANK[finding.severity] > _SEVERITY_RANK[current.severity]:
            best[finding.code] = finding
    return prioritize(list(best.values()))


def count_by_severity(findings: Sequence[Finding]) -> dict[Severity, int]:
    """Tally findings by severity.

    Args:
        findings: Findings to count.

    Returns:
        A count for every severity, including zeros.
    """
    counts = {severity: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def health_score(category_scores: dict[FindingCategory, float]) -> float:
    """Combine category scores into one overall health score.

    Weights reflect what actually costs a small business enquiries: a site that
    is slow or insecure loses visitors outright, while an accessibility gap is
    serious but less immediately revenue-linked.

    Args:
        category_scores: Normalized score per category. Unknown categories are
            ignored; missing ones are simply not counted.

    Returns:
        A weighted mean in ``[0.0, 1.0]``, or 0.0 when no known categories are
        supplied.

    Raises:
        ValueError: If any score is outside ``[0.0, 1.0]``.
    """
    weights: dict[FindingCategory, float] = {
        FindingCategory.PERFORMANCE: 0.25,
        FindingCategory.SECURITY: 0.20,
        FindingCategory.SEO: 0.20,
        FindingCategory.MOBILE: 0.15,
        FindingCategory.CONTACT_FORM: 0.10,
        FindingCategory.ACCESSIBILITY: 0.10,
    }

    total_weight = 0.0
    accumulated = 0.0
    for category, score in category_scores.items():
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score for {category.value} is outside [0.0, 1.0]: {score}")
        weight = weights.get(category)
        if weight is None:
            continue
        accumulated += score * weight
        total_weight += weight

    if total_weight == 0.0:
        logger.warning("health_score called with no weighted categories")
        return 0.0

    return round(accumulated / total_weight, 4)
