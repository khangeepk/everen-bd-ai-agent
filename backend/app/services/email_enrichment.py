"""Pure logic for the email-enrichment fallback chain: types, format
validation, and pattern guessing.

Standard library only (no httpx, no crawling) so this half of the feature is
testable offline and safely importable from a DB model
(``app/db/models/lead.py`` imports :class:`EmailSource` from here, the same
way it already imports ``PipelineStage`` from
``app.services.pipeline``) -- a model file should never need to pull in an
HTTP client just to get an enum. The network-touching contact-page crawl
lives in ``app/services/email_discovery.py`` instead, which imports the
types defined here.

Two steps make up the fallback chain, tried in strict order (a fallback, not
"try both and pick the best"):

1. **Website contact/footer page** (``app.services.email_discovery``) --
   crawl the lead's own site looking for a ``mailto:`` link or an
   email-shaped string on a likely contact page or the home page footer.
2. **Common-pattern guess** (:func:`guess_pattern_emails`, here) -- only
   attempted if step 1 finds nothing. Given a contact name and the site's
   domain, generates the common ``name@domain`` permutations
   (``first.last@``, ``flast@``, ``first@``, ...). Pure string generation,
   no network call, no third-party verifier -- "format-only validation" per
   the request that created this feature means a regex syntax check,
   nothing more. This is a guess, not evidence, and is scored accordingly
   (see the CONFIDENCE constants below).

Every candidate carries a :class:`EmailCandidate` with a ``source`` and a
``confidence_score`` -- callers (``app/services/email_enrichment_scanner.py``)
decide what to do with a low-confidence, unverified guess. Neither this
module nor email_discovery.py ever claims a candidate is a working address --
only that it is syntactically well-formed and where it came from.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.services.web_parsing import normalize_url

logger = logging.getLogger(__name__)

#: Format-only email syntax check -- matches the standard already used
#: elsewhere in this codebase for the same purpose (app.services.canspam's
#: _EMAIL_PATTERN). Deliberately not RFC 5322-complete: good enough to reject
#: obvious garbage, not a substitute for actual deliverability verification.
_EMAIL_FORMAT_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Matches an email-shaped string in plain text (for email_discovery's
#: visible-text scan, since app/services/web_parsing.py's parser does not
#: surface raw text).
EMAIL_IN_TEXT_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

#: Confidence scores by evidence quality. Deliberately conservative and
#: ordered: a mailto: link is stronger evidence than a plain-text match, a
#: dedicated contact page is stronger than the home page footer, and any
#: pattern guess -- however plausible-looking -- is weaker than anything
#: actually observed on the site, because it is not evidence at all.
CONFIDENCE_MAILTO_CONTACT_PAGE = 0.75
CONFIDENCE_MAILTO_HOME_FOOTER = 0.65
CONFIDENCE_TEXT_MATCH_CONTACT_PAGE = 0.55
CONFIDENCE_TEXT_MATCH_HOME_FOOTER = 0.45
CONFIDENCE_PATTERN_GUESS = 0.30

#: Bound on how many pattern-guess candidates one lead can generate.
MAX_PATTERN_GUESSES = 6

#: Substrings in a link's path suggesting it's a contact/about page.
CONTACT_PATH_HINTS: tuple[str, ...] = ("contact", "contact-us", "about", "about-us", "get-in-touch")

CANDIDATE_CONTACT_PATHS: tuple[str, ...] = (
    "/contact", "/contact-us", "/contact/", "/contact-us/", "/about/contact",
    "/get-in-touch",
)


class EmailSource(str, enum.Enum):
    """Where a lead's contact email came from.

    Attributes:
        MANUAL: A human typed it in directly (create/update, or a Places
            candidate promotion where the rep supplied it). Treated as
            trusted by default -- see Lead.contact_email_verified's default.
        WEBSITE_CONTACT_PAGE: Found on the lead's own website by
            app.services.email_discovery's crawl step.
        PATTERN_GUESS: Generated from a common name@domain permutation, with
            no corroborating evidence from the site itself.
    """

    MANUAL = "manual"
    WEBSITE_CONTACT_PAGE = "website_contact_page"
    PATTERN_GUESS = "pattern_guess"


class EmailEnrichmentError(RuntimeError):
    """Raised when the contact-page crawl step cannot reach the site at all."""


@dataclass(frozen=True)
class EmailCandidate:
    """One candidate contact email, with its provenance.

    Attributes:
        email: The candidate address (format-validated, never
            deliverability-verified).
        source: Where it came from.
        confidence_score: 0.0-1.0, see the module's CONFIDENCE_* constants.
        evidence: Short human-readable note on how it was found, e.g.
            "mailto: link on /contact" or "pattern guess: first.last@domain".
    """

    email: str
    source: EmailSource
    confidence_score: float
    evidence: str


def is_valid_email_format(email: str) -> bool:
    """Check basic email syntax -- format only, never a deliverability check.

    Args:
        email: The address to check.

    Returns:
        True if it looks like a syntactically plausible email address.
    """
    return bool(_EMAIL_FORMAT_PATTERN.match(email.strip()))


def extract_mailto_addresses(page_links: list[str]) -> list[str]:
    """Pull email addresses out of mailto: links.

    Args:
        page_links: Raw (unresolved) href values from a parsed page.

    Returns:
        Deduplicated, format-valid addresses, in the order first seen.
    """
    seen: set[str] = set()
    addresses: list[str] = []
    for href in page_links:
        if not href.lower().startswith("mailto:"):
            continue
        # mailto: may carry query params, e.g. mailto:x@y.com?subject=Hi
        address = href[len("mailto:"):].split("?")[0].strip()
        if address and is_valid_email_format(address) and address.lower() not in seen:
            seen.add(address.lower())
            addresses.append(address)
    return addresses


def extract_text_addresses(text: str) -> list[str]:
    """Pull email-shaped strings out of visible page text.

    Args:
        text: Lowercased visible text (see
            app.services.job_signals.extract_visible_text).

    Returns:
        Deduplicated, format-valid addresses, in the order first seen.
    """
    seen: set[str] = set()
    addresses: list[str] = []
    for match in EMAIL_IN_TEXT_PATTERN.finditer(text):
        candidate = match.group(0)
        if is_valid_email_format(candidate) and candidate not in seen:
            seen.add(candidate)
            addresses.append(candidate)
    return addresses


def _name_parts(contact_name: str) -> tuple[str, str] | None:
    """Split a contact name into (first, last), lowercased and cleaned.

    Args:
        contact_name: Free-text name, e.g. "Dr. Jane O'Connor".

    Returns:
        (first, last), or None if fewer than two usable name parts remain.
    """
    cleaned = re.sub(r"[^a-zA-Z\s\-]", "", contact_name).strip().lower()
    parts = [part for part in cleaned.split() if part not in {"dr", "mr", "mrs", "ms", "prof"}]
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def _domain_from_website(website_url: str) -> str:
    """Extract a bare domain from a website URL, dropping a leading www.

    Args:
        website_url: The lead's website URL.

    Returns:
        The domain, or an empty string if none could be parsed.
    """
    if not website_url.strip():
        return ""
    netloc = urlparse(normalize_url(website_url)).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def guess_pattern_emails(website_url: str, contact_name: str | None) -> list[EmailCandidate]:
    """Generate common name@domain permutations for a lead.

    Pure string generation -- no network call, no deliverability check of
    any kind (format-only, per the request that created this feature). Only
    attempted by the caller as a fallback when the contact-page check found
    nothing (see app/services/email_enrichment_scanner.py).

    Args:
        website_url: The lead's website, used to derive the domain.
        contact_name: The lead's contact name, if known. Without at least a
            first and last name, no patterns can be generated -- this
            function does not fall back to generic role addresses
            (info@, contact@, ...), since those were not part of what was
            asked for and are not tied to a specific person.

    Returns:
        Candidates, in a fixed plausibility order (all share the same
        confidence score -- none has stronger evidence than another). Empty
        if no domain or no usable name is available.
    """
    domain = _domain_from_website(website_url)
    if not domain or not contact_name:
        return []

    parts = _name_parts(contact_name)
    if parts is None:
        return []
    first, last = parts

    patterns = [
        f"{first}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}@{domain}",
        f"{first}_{last}@{domain}",
        f"{last}.{first}@{domain}",
    ]

    candidates: list[EmailCandidate] = []
    seen: set[str] = set()
    for pattern in patterns[:MAX_PATTERN_GUESSES]:
        if pattern in seen or not is_valid_email_format(pattern):
            continue
        seen.add(pattern)
        candidates.append(
            EmailCandidate(
                email=pattern,
                source=EmailSource.PATTERN_GUESS,
                confidence_score=CONFIDENCE_PATTERN_GUESS,
                evidence=f"pattern guess from contact name and domain {domain}",
            )
        )
    logger.info(
        "Pattern-guess emails generated", extra={"domain": domain, "count": len(candidates)}
    )
    return candidates
