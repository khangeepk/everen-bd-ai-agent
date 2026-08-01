"""Lighthouse result parsing and finding extraction.

Standard library only. Deliberately separated from the HTTP client in
:mod:`app.services.pagespeed` so that interpreting a Lighthouse payload -- the
part with all the judgement in it -- can be tested without a network stack.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

from app.services.audit_scoring import (
    Finding,
    FindingCategory,
    Severity,
    severity_for_score,
)

logger = logging.getLogger(__name__)

#: Lighthouse category ids requested on every run.
PSI_CATEGORIES = ("performance", "accessibility", "best-practices", "seo")

#: Lighthouse category id -> our internal category.
CATEGORY_MAP: dict[str, FindingCategory] = {
    "performance": FindingCategory.PERFORMANCE,
    "accessibility": FindingCategory.ACCESSIBILITY,
    "best-practices": FindingCategory.BEST_PRACTICES,
    "seo": FindingCategory.SEO,
}

#: Individual Lighthouse audits worth calling out by name, with the severity to
#: apply when they fail and a business-readable explanation.
NOTABLE_AUDITS: dict[str, tuple[FindingCategory, Severity, str, str]] = {
    "is-on-https": (
        FindingCategory.SECURITY,
        Severity.CRITICAL,
        "Site is not served over HTTPS",
        "Browsers show a 'Not secure' warning to every visitor, and Google ranks "
        "insecure sites lower. This is usually a same-day fix.",
    ),
    "viewport": (
        FindingCategory.MOBILE,
        Severity.HIGH,
        "No mobile viewport tag",
        "Without it, phones render the desktop layout shrunk down, so text is "
        "unreadable without pinch-zooming. Most visitors are on phones.",
    ),
    "document-title": (
        FindingCategory.SEO,
        Severity.HIGH,
        "Page has no title tag",
        "The title is the clickable headline in Google results. Without one, "
        "search engines invent something, usually badly.",
    ),
    "meta-description": (
        FindingCategory.SEO,
        Severity.MEDIUM,
        "Missing meta description",
        "This is the summary under the headline in search results. Writing it "
        "yourself typically lifts click-through rate.",
    ),
    "http-status-code": (
        FindingCategory.SEO,
        Severity.CRITICAL,
        "Page returns an error status code",
        "Search engines cannot index a page that returns an error, so it will "
        "not appear in results at all.",
    ),
    "crawlable-anchors": (
        FindingCategory.SEO,
        Severity.MEDIUM,
        "Some links are not crawlable",
        "Search engines cannot follow these links, so the pages behind them may "
        "never be indexed.",
    ),
    "image-alt": (
        FindingCategory.ACCESSIBILITY,
        Severity.MEDIUM,
        "Images are missing alt text",
        "Screen readers cannot describe these images, and search engines cannot "
        "read them either.",
    ),
    "color-contrast": (
        FindingCategory.ACCESSIBILITY,
        Severity.MEDIUM,
        "Low colour contrast",
        "Text is hard to read for many visitors, particularly on phones outdoors.",
    ),
    "errors-in-console": (
        FindingCategory.BEST_PRACTICES,
        Severity.LOW,
        "JavaScript errors in the browser console",
        "Errors often mean a feature is quietly broken for some visitors.",
    ),
}

#: A Lighthouse audit score at or below this counts as failing.
AUDIT_FAIL_THRESHOLD = 0.5


class Strategy(str, enum.Enum):
    """Which device profile Lighthouse should emulate."""

    MOBILE = "mobile"
    DESKTOP = "desktop"


class PageSpeedError(RuntimeError):
    """Raised when PageSpeed Insights is unreachable or returns an error."""


@dataclass(frozen=True)
class PageSpeedResult:
    """Parsed Lighthouse output for one strategy.

    Attributes:
        url: The final URL Lighthouse analysed.
        strategy: Device profile used.
        category_scores: Lighthouse category id -> score in ``[0.0, 1.0]``.
        failed_audits: Audit id -> score, for audits at or below the failure
            threshold.
        metrics: Human-readable metric values.
        lighthouse_version: Version string reported by the API.
    """

    url: str
    strategy: Strategy
    category_scores: dict[str, float]
    failed_audits: dict[str, float]
    metrics: dict[str, str]
    lighthouse_version: str | None = None

    def score_for(self, category: FindingCategory) -> float | None:
        """Look up the score for an internal category.

        Args:
            category: The internal category.

        Returns:
            The score, or None if Lighthouse did not report it.
        """
        for lighthouse_id, mapped in CATEGORY_MAP.items():
            if mapped is category:
                return self.category_scores.get(lighthouse_id)
        return None


def parse_pagespeed(payload: dict[str, Any], strategy: Strategy) -> PageSpeedResult:
    """Parse a PSI v5 response into a :class:`PageSpeedResult`.

    Tolerates missing sections -- PSI omits categories it could not evaluate,
    and a partial result is more useful than an exception.

    Args:
        payload: Decoded PSI JSON response.
        strategy: The strategy the run used.

    Returns:
        The parsed result.

    Raises:
        PageSpeedError: If the payload contains no ``lighthouseResult``.
    """
    lighthouse = payload.get("lighthouseResult")
    if not isinstance(lighthouse, dict):
        raise PageSpeedError("PageSpeed response contained no lighthouseResult")

    category_scores: dict[str, float] = {}
    for category_id, category in (lighthouse.get("categories") or {}).items():
        score = category.get("score") if isinstance(category, dict) else None
        if isinstance(score, (int, float)):
            category_scores[category_id] = float(score)

    audits = lighthouse.get("audits") or {}
    failed_audits: dict[str, float] = {}
    metrics: dict[str, str] = {}

    for audit_id, audit in audits.items():
        if not isinstance(audit, dict):
            continue
        score = audit.get("score")
        if isinstance(score, (int, float)) and score <= AUDIT_FAIL_THRESHOLD:
            failed_audits[audit_id] = float(score)
        display = audit.get("displayValue")
        if isinstance(display, str) and display:
            metrics[audit_id] = display

    return PageSpeedResult(
        url=lighthouse.get("finalUrl") or lighthouse.get("requestedUrl") or "",
        strategy=strategy,
        category_scores=category_scores,
        failed_audits=failed_audits,
        metrics=metrics,
        lighthouse_version=lighthouse.get("lighthouseVersion"),
    )


def findings_from_pagespeed(result: PageSpeedResult) -> list[Finding]:
    """Derive audit findings from a parsed Lighthouse result.

    Produces one finding per weak category, plus one per notable failed audit.

    Args:
        result: The parsed Lighthouse result.

    Returns:
        Findings, unsorted and not yet deduplicated across strategies.
    """
    findings: list[Finding] = []

    for lighthouse_id, category in CATEGORY_MAP.items():
        score = result.category_scores.get(lighthouse_id)
        if score is None:
            continue
        severity = severity_for_score(score)
        if severity is Severity.INFO:
            continue
        label = lighthouse_id.replace("-", " ")
        findings.append(
            Finding(
                code=f"lighthouse_{lighthouse_id}_score",
                category=category,
                severity=severity,
                title=f"{label.title()} score is {int(score * 100)}/100",
                detail=(
                    f"Google's own {label} assessment scores this page "
                    f"{int(score * 100)} out of 100 on {result.strategy.value}. "
                    "Prospective customers see the effects of this directly."
                ),
                score=score,
            )
        )

    for audit_id, score in result.failed_audits.items():
        notable = NOTABLE_AUDITS.get(audit_id)
        if notable is None:
            continue
        category, severity, title, detail = notable
        findings.append(
            Finding(
                code=f"lighthouse_{audit_id}",
                category=category,
                severity=severity,
                title=title,
                detail=detail,
                score=score,
            )
        )

    logger.info(
        "Derived PageSpeed findings",
        extra={"strategy": result.strategy.value, "findings": len(findings)},
    )
    return findings
