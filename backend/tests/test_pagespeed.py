"""Tests for :mod:`app.services.pagespeed` parsing and finding extraction.

The PageSpeed API is never called; sample payloads stand in for it.
"""

from __future__ import annotations

import pytest

from app.services.audit_scoring import FindingCategory, Severity
from app.services.pagespeed_parsing import (
    PageSpeedError,
    Strategy,
    findings_from_pagespeed,
    parse_pagespeed,
)
from tests.sample_audit_data import (
    GOOD_SITE_MOBILE,
    MALFORMED_RESULT,
    PARTIAL_RESULT,
    POOR_SITE_DESKTOP,
    POOR_SITE_MOBILE,
)


def test_category_scores_are_parsed() -> None:
    """All four Lighthouse categories are read."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    assert result.category_scores["performance"] == pytest.approx(0.21)
    assert result.category_scores["seo"] == pytest.approx(0.35)
    assert result.category_scores["accessibility"] == pytest.approx(0.44)
    assert result.category_scores["best-practices"] == pytest.approx(0.58)


def test_failed_audits_are_collected() -> None:
    """Audits scoring at or below the failure threshold are captured."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    assert "is-on-https" in result.failed_audits
    assert "viewport" in result.failed_audits
    assert "unminified-css" not in result.failed_audits


def test_metrics_with_display_values_are_captured() -> None:
    """Human-readable metric values are retained for the report."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    assert result.metrics["largest-contentful-paint"] == "6.4 s"


def test_final_url_and_version_are_read() -> None:
    """Provenance fields are parsed."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    assert result.url == "http://example-poor.test/"
    assert result.lighthouse_version == "12.0.0"


def test_partial_result_does_not_raise() -> None:
    """A response missing categories still parses."""
    result = parse_pagespeed(PARTIAL_RESULT, Strategy.MOBILE)

    assert result.category_scores == {"performance": 0.5}
    assert result.score_for(FindingCategory.SEO) is None


def test_malformed_result_raises() -> None:
    """A response with no lighthouseResult is an error."""
    with pytest.raises(PageSpeedError, match="no lighthouseResult"):
        parse_pagespeed(MALFORMED_RESULT, Strategy.MOBILE)


def test_score_for_maps_internal_categories() -> None:
    """Internal categories resolve to the right Lighthouse ids."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    assert result.score_for(FindingCategory.PERFORMANCE) == pytest.approx(0.21)
    assert result.score_for(FindingCategory.SEO) == pytest.approx(0.35)


def test_poor_site_produces_findings() -> None:
    """A weak site generates category and audit findings."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
    findings = findings_from_pagespeed(result)

    assert findings
    codes = {finding.code for finding in findings}
    assert "lighthouse_performance_score" in codes
    assert "lighthouse_is-on-https" in codes
    assert "lighthouse_viewport" in codes


def test_good_site_produces_no_category_findings() -> None:
    """A healthy site generates nothing to complain about."""
    result = parse_pagespeed(GOOD_SITE_MOBILE, Strategy.MOBILE)
    findings = findings_from_pagespeed(result)

    assert findings == []


def test_no_https_is_critical() -> None:
    """A site without HTTPS is flagged at the highest severity."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
    finding = next(
        item for item in findings_from_pagespeed(result) if item.code == "lighthouse_is-on-https"
    )

    assert finding.severity is Severity.CRITICAL
    assert finding.category is FindingCategory.SECURITY


def test_missing_viewport_maps_to_mobile_category() -> None:
    """The viewport audit drives the mobile-friendliness finding."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
    finding = next(
        item for item in findings_from_pagespeed(result) if item.code == "lighthouse_viewport"
    )

    assert finding.category is FindingCategory.MOBILE
    assert finding.severity is Severity.HIGH


def test_unmapped_audits_are_ignored() -> None:
    """Only audits on the notable list become findings."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
    codes = {finding.code for finding in findings_from_pagespeed(result)}

    assert "lighthouse_total-blocking-time" not in codes


def test_finding_titles_are_business_readable() -> None:
    """Titles avoid raw metric jargon."""
    result = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)

    for finding in findings_from_pagespeed(result):
        assert "LCP" not in finding.title
        assert "TBT" not in finding.title


def test_desktop_and_mobile_share_finding_codes() -> None:
    """The same issue on both strategies uses one code, so dedup can collapse it."""
    mobile = {f.code for f in findings_from_pagespeed(parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE))}
    desktop = {f.code for f in findings_from_pagespeed(parse_pagespeed(POOR_SITE_DESKTOP, Strategy.DESKTOP))}

    assert "lighthouse_is-on-https" in mobile & desktop


def test_desktop_performance_is_less_severe_than_mobile() -> None:
    """The fixture's desktop run scores better, so its finding is milder."""
    mobile = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
    desktop = parse_pagespeed(POOR_SITE_DESKTOP, Strategy.DESKTOP)

    assert desktop.category_scores["performance"] > mobile.category_scores["performance"]
