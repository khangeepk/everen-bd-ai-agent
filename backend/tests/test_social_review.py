"""Tests for :mod:`app.services.social_review`."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from app.services.audit_scoring import FindingCategory, Severity
from app.services.social_review import (
    Platform,
    PostingCadence,
    ProfileChecklist,
    build_findings,
    score_presence,
    score_profile,
)


def _complete(platform: Platform = Platform.LINKEDIN) -> ProfileChecklist:
    """Build a fully complete profile checklist.

    Args:
        platform: Platform to assign.

    Returns:
        A complete :class:`ProfileChecklist`.
    """
    return ProfileChecklist(
        platform=platform,
        profile_url=f"https://{platform.value}.test/business",
        profile_exists=True,
        has_profile_image=True,
        has_cover_image=True,
        has_description=True,
        has_website_link=True,
        has_contact_details=True,
        cadence=PostingCadence.WEEKLY_OR_MORE,
    )


def test_complete_profile_scores_one() -> None:
    """A fully complete, active profile scores 1.0."""
    assert score_profile(_complete()) == pytest.approx(1.0)


def test_missing_profile_scores_zero() -> None:
    """A profile that does not exist gets no partial credit."""
    checklist = ProfileChecklist(platform=Platform.INSTAGRAM, profile_exists=False)
    assert score_profile(checklist) == 0.0


def test_missing_profile_scores_zero_even_with_other_flags() -> None:
    """Contradictory input still scores zero if the profile does not exist."""
    checklist = ProfileChecklist(
        platform=Platform.INSTAGRAM,
        profile_exists=False,
        has_profile_image=True,
        has_description=True,
        cadence=PostingCadence.WEEKLY_OR_MORE,
    )
    assert score_profile(checklist) == 0.0


def test_bare_profile_scores_low_but_nonzero() -> None:
    """An empty but existing profile earns only the existence weight."""
    checklist = ProfileChecklist(platform=Platform.FACEBOOK, profile_exists=True)
    score = score_profile(checklist)

    assert 0.0 < score < 0.3


def test_cadence_affects_score() -> None:
    """More frequent posting scores higher, all else equal."""
    base = _complete()
    weekly = score_profile(base)
    monthly = score_profile(ProfileChecklist(**{**vars(base), "cadence": PostingCadence.MONTHLY}))
    dormant = score_profile(ProfileChecklist(**{**vars(base), "cadence": PostingCadence.DORMANT}))

    assert weekly > monthly > dormant


def test_score_never_exceeds_one() -> None:
    """The score is capped at 1.0."""
    assert score_profile(_complete()) <= 1.0


def test_presence_averages_across_platforms() -> None:
    """Overall presence is the mean of per-platform scores."""
    checklists = [
        _complete(Platform.LINKEDIN),
        ProfileChecklist(platform=Platform.INSTAGRAM, profile_exists=False),
    ]
    assert score_presence(checklists) == pytest.approx(0.5)


def test_presence_with_no_reviews_is_zero() -> None:
    """Nothing reviewed gives 0.0 rather than raising."""
    assert score_presence([]) == 0.0


def test_complete_presence_produces_no_findings() -> None:
    """A fully complete profile generates nothing to report."""
    assert build_findings([_complete()]) == []


def test_missing_profile_produces_a_finding() -> None:
    """An absent profile is reported at medium severity."""
    findings = build_findings(
        [ProfileChecklist(platform=Platform.LINKEDIN, profile_exists=False)]
    )

    assert len(findings) == 1
    assert findings[0].code == "social_missing_linkedin"
    assert findings[0].category is FindingCategory.SOCIAL
    assert findings[0].severity is Severity.MEDIUM


def test_incomplete_profile_lists_what_is_missing() -> None:
    """The finding names the specific gaps as evidence."""
    checklist = ProfileChecklist(
        platform=Platform.FACEBOOK,
        profile_exists=True,
        has_profile_image=True,
        cadence=PostingCadence.WEEKLY_OR_MORE,
    )
    findings = build_findings([checklist])
    incomplete = [f for f in findings if f.code == "social_incomplete_facebook"]

    assert incomplete
    assert "description or bio" in incomplete[0].evidence
    assert "contact details" in incomplete[0].evidence


def test_many_gaps_escalate_severity() -> None:
    """Three or more missing elements raises severity from low to medium."""
    few_gaps = ProfileChecklist(
        platform=Platform.X,
        profile_exists=True,
        has_profile_image=True,
        has_description=True,
        has_website_link=True,
        cadence=PostingCadence.WEEKLY_OR_MORE,
    )
    many_gaps = ProfileChecklist(
        platform=Platform.X, profile_exists=True, cadence=PostingCadence.WEEKLY_OR_MORE
    )

    few = [f for f in build_findings([few_gaps]) if "incomplete" in f.code][0]
    many = [f for f in build_findings([many_gaps]) if "incomplete" in f.code][0]

    assert few.severity is Severity.LOW
    assert many.severity is Severity.MEDIUM


def test_dormant_profile_produces_a_finding() -> None:
    """An inactive profile is flagged separately from incompleteness."""
    checklist = ProfileChecklist(
        platform=Platform.INSTAGRAM,
        profile_exists=True,
        has_profile_image=True,
        has_description=True,
        has_website_link=True,
        has_contact_details=True,
        cadence=PostingCadence.DORMANT,
    )
    codes = {finding.code for finding in build_findings([checklist])}

    assert "social_dormant_instagram" in codes


def test_missing_profile_does_not_also_report_dormancy() -> None:
    """A non-existent profile produces one finding, not two."""
    findings = build_findings(
        [ProfileChecklist(platform=Platform.TIKTOK, profile_exists=False)]
    )
    assert len(findings) == 1


def test_findings_span_multiple_platforms() -> None:
    """Each reviewed platform contributes its own findings."""
    checklists = [
        ProfileChecklist(platform=Platform.LINKEDIN, profile_exists=False),
        ProfileChecklist(platform=Platform.FACEBOOK, profile_exists=False),
    ]
    codes = {finding.code for finding in build_findings(checklists)}

    assert codes == {"social_missing_linkedin", "social_missing_facebook"}


def test_checklist_is_immutable() -> None:
    """Checklists are frozen so a recorded review cannot be silently edited."""
    checklist = _complete()

    with pytest.raises(FrozenInstanceError):
        checklist.profile_exists = False  # type: ignore[misc]
