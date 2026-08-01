"""Social presence completeness scoring.

NO SCRAPING. This module never fetches a social platform. It scores a
structured checklist that a human reviewer fills in after looking at publicly
visible profiles, which is why it is pure and stdlib-only.

Why it works this way: LinkedIn, Instagram, and Facebook all gate profile data
behind the profile *owner's* OAuth consent. There is no API that returns an
arbitrary business's profile, and scraping these platforms breaches their terms
(LinkedIn in particular litigates it). For cold prospecting, a human reviewer
looking at a public page is the compliant path.

If a prospect later consents and connects their accounts, populate the same
:class:`ProfileChecklist` from the platform API and every scoring function here
keeps working unchanged.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.audit_scoring import Finding, FindingCategory, Severity

logger = logging.getLogger(__name__)


class Platform(str, enum.Enum):
    """Social platforms the reviewer can assess."""

    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    X = "x"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    GOOGLE_BUSINESS = "google_business"


class PostingCadence(str, enum.Enum):
    """How recently and regularly the profile posts."""

    WEEKLY_OR_MORE = "weekly_or_more"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    DORMANT = "dormant"
    NONE = "none"


#: Cadence contribution to the completeness score.
_CADENCE_SCORE: dict[PostingCadence, float] = {
    PostingCadence.WEEKLY_OR_MORE: 1.0,
    PostingCadence.MONTHLY: 0.75,
    PostingCadence.QUARTERLY: 0.40,
    PostingCadence.DORMANT: 0.15,
    PostingCadence.NONE: 0.0,
}

#: Weight of each checklist element in the per-platform score.
_WEIGHTS: dict[str, float] = {
    "profile_exists": 0.25,
    "has_profile_image": 0.10,
    "has_cover_image": 0.05,
    "has_description": 0.15,
    "has_website_link": 0.15,
    "has_contact_details": 0.10,
    "cadence": 0.20,
}


@dataclass(frozen=True)
class ProfileChecklist:
    """A reviewer's observations about one public profile.

    Every field is something visible on a public profile page. Nothing here
    requires logging in, scraping, or an API call.

    Attributes:
        platform: Which platform.
        profile_url: The public profile URL, if one was found.
        profile_exists: Whether a profile was found at all.
        has_profile_image: Whether a logo or avatar is set.
        has_cover_image: Whether a banner image is set.
        has_description: Whether the bio/about section is filled in.
        has_website_link: Whether the profile links back to the website.
        has_contact_details: Whether a phone or email is shown.
        cadence: Observed posting frequency.
        follower_band: Coarse follower bucket, e.g. ``"100-1k"``. Optional and
            deliberately imprecise -- exact counts add noise, not signal.
        reviewer_notes: Free-text observations.
    """

    platform: Platform
    profile_url: str | None = None
    profile_exists: bool = False
    has_profile_image: bool = False
    has_cover_image: bool = False
    has_description: bool = False
    has_website_link: bool = False
    has_contact_details: bool = False
    cadence: PostingCadence = PostingCadence.NONE
    follower_band: str | None = None
    reviewer_notes: str | None = None


def score_profile(checklist: ProfileChecklist) -> float:
    """Score one profile's completeness.

    A profile that does not exist scores 0.0 outright -- partial credit for a
    missing profile would understate the gap.

    Args:
        checklist: The reviewer's observations.

    Returns:
        A completeness score in ``[0.0, 1.0]``, rounded to 4 places.
    """
    if not checklist.profile_exists:
        return 0.0

    total = _WEIGHTS["profile_exists"]
    for attribute in (
        "has_profile_image",
        "has_cover_image",
        "has_description",
        "has_website_link",
        "has_contact_details",
    ):
        if getattr(checklist, attribute):
            total += _WEIGHTS[attribute]

    total += _WEIGHTS["cadence"] * _CADENCE_SCORE[checklist.cadence]
    return round(min(total, 1.0), 4)


def score_presence(checklists: Sequence[ProfileChecklist]) -> float:
    """Score overall social presence across all reviewed platforms.

    Args:
        checklists: One entry per reviewed platform.

    Returns:
        The mean per-platform score, or 0.0 when nothing was reviewed.
    """
    if not checklists:
        logger.info("score_presence called with no checklists")
        return 0.0
    return round(sum(score_profile(item) for item in checklists) / len(checklists), 4)


def _missing_elements(checklist: ProfileChecklist) -> list[str]:
    """List the incomplete elements of an existing profile.

    Args:
        checklist: The reviewer's observations.

    Returns:
        Human-readable labels for each missing element.
    """
    labels = {
        "has_profile_image": "profile image",
        "has_description": "description or bio",
        "has_website_link": "link back to the website",
        "has_contact_details": "contact details",
    }
    return [label for attribute, label in labels.items() if not getattr(checklist, attribute)]


def build_findings(checklists: Sequence[ProfileChecklist]) -> list[Finding]:
    """Turn checklists into audit findings.

    Args:
        checklists: One entry per reviewed platform.

    Returns:
        Findings describing absent or incomplete profiles. An empty list means
        every reviewed profile is in good shape.
    """
    findings: list[Finding] = []

    for checklist in checklists:
        platform = checklist.platform.value

        if not checklist.profile_exists:
            findings.append(
                Finding(
                    code=f"social_missing_{platform}",
                    category=FindingCategory.SOCIAL,
                    severity=Severity.MEDIUM,
                    title=f"No {platform.replace('_', ' ').title()} profile found",
                    detail=(
                        f"We could not find a {platform.replace('_', ' ')} presence. "
                        "Prospective customers who look you up there find nothing, "
                        "and competitors who are present get the enquiry instead."
                    ),
                    score=0.0,
                )
            )
            continue

        missing = _missing_elements(checklist)
        if missing:
            findings.append(
                Finding(
                    code=f"social_incomplete_{platform}",
                    category=FindingCategory.SOCIAL,
                    severity=Severity.LOW if len(missing) < 3 else Severity.MEDIUM,
                    title=f"{platform.replace('_', ' ').title()} profile is incomplete",
                    detail=(
                        f"The profile is missing: {', '.join(missing)}. "
                        "Completing these makes the business look established and "
                        "gives visitors a route to get in touch."
                    ),
                    evidence=tuple(missing),
                    score=score_profile(checklist),
                )
            )

        if checklist.cadence in (PostingCadence.DORMANT, PostingCadence.NONE):
            findings.append(
                Finding(
                    code=f"social_dormant_{platform}",
                    category=FindingCategory.SOCIAL,
                    severity=Severity.LOW,
                    title=f"{platform.replace('_', ' ').title()} profile looks inactive",
                    detail=(
                        "The profile exists but has not posted recently. A dormant "
                        "profile can read as a business that is no longer trading."
                    ),
                    score=_CADENCE_SCORE[checklist.cadence],
                )
            )

    logger.info(
        "Built social findings",
        extra={"profiles": len(checklists), "findings": len(findings)},
    )
    return findings
