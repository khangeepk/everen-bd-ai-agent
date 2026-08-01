"""Contact-page crawl step of the email-enrichment fallback chain.

The network-touching half of ``app/services/email_enrichment.py`` (which
holds the pure types/logic reused here and by the pattern-guess step). Split
out specifically so the pure module has no httpx/crawling dependency and is
safely importable from a DB model (see email_enrichment.py's docstring).

Same politeness rules as ``app.services.job_signals``: robots.txt respected,
identifying User-Agent, single best-guess page fetched rather than a full
site crawl.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.email_enrichment import (
    CANDIDATE_CONTACT_PATHS,
    CONFIDENCE_MAILTO_CONTACT_PAGE,
    CONFIDENCE_MAILTO_HOME_FOOTER,
    CONFIDENCE_TEXT_MATCH_CONTACT_PAGE,
    CONFIDENCE_TEXT_MATCH_HOME_FOOTER,
    CONTACT_PATH_HINTS,
    EmailCandidate,
    EmailEnrichmentError,
    EmailSource,
    extract_mailto_addresses,
    extract_text_addresses,
)
from app.services.job_signals import USER_AGENT, extract_visible_text
from app.services.web_parsing import normalize_url, parse_page, resolve_links, same_origin

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


async def _robots_allows(client: httpx.AsyncClient, url: str, user_agent: str) -> bool:
    """Check whether robots.txt permits fetching a URL.

    Deliberately duplicated from app.services.job_signals's identical helper
    rather than imported -- keeps this module decoupled from job_signals'
    internals (only its two public helpers, USER_AGENT and
    extract_visible_text, are reused).

    Args:
        client: HTTP client to use.
        url: URL to check.
        user_agent: This crawler's identifying User-Agent.

    Returns:
        True if the fetch is permitted. A missing/unreadable robots.txt is
        treated as permissive.
    """
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)

    try:
        response = await client.get(robots_url)
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
        else:
            parser.allow_all = True
    except httpx.HTTPError:
        parser.allow_all = True

    return parser.can_fetch(user_agent, url)


def _looks_like_contact_link(href: str) -> bool:
    """Whether a resolved link's path suggests a contact/about page.

    Args:
        href: An absolute URL.

    Returns:
        True if the path contains a recognized hint.
    """
    path = urlparse(href).path.lower()
    return any(hint in path for hint in CONTACT_PATH_HINTS)


async def find_contact_page_emails(
    website_url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> list[EmailCandidate]:
    """Look for a contact email on the lead's own website.

    Checks a dedicated contact/about page first if one can be found (via a
    home-page link match or a common-path guess), then falls back to the
    home page's own footer/body. A ``mailto:`` link is stronger evidence
    than a plain-text match, and either found on a contact page outranks the
    same found on the home page.

    Args:
        website_url: The lead's website root URL.
        timeout_seconds: Per-request timeout.

    Returns:
        Candidates found, highest confidence first. Empty if the site has no
        detectable contact page and nothing in its home page -- not an
        error, most sites this doesn't work for simply have no visible
        email at all.

    Raises:
        EmailEnrichmentError: If the site could not be reached at all.
    """
    origin = normalize_url(website_url)
    headers = {"User-Agent": USER_AGENT}
    candidates: list[EmailCandidate] = []

    async with httpx.AsyncClient(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
        if not await _robots_allows(client, origin, USER_AGENT):
            logger.info("robots.txt disallows the contact-page check", extra={"url": origin})
            return []

        try:
            home_response = await client.get(origin)
        except httpx.HTTPError as exc:
            raise EmailEnrichmentError(f"Could not fetch {origin}: {exc}") from exc

        contact_url: str | None = None
        home_page = None
        if "html" in home_response.headers.get("content-type", "").lower():
            home_page = parse_page(home_response.text)
            for link in resolve_links(home_page, origin):
                if same_origin(link, origin) and _looks_like_contact_link(link):
                    contact_url = link
                    break

        if contact_url is None:
            for candidate_path in CANDIDATE_CONTACT_PATHS:
                candidate_url = normalize_url(origin.rstrip("/") + candidate_path)
                if not await _robots_allows(client, candidate_url, USER_AGENT):
                    continue
                try:
                    probe = await client.head(candidate_url)
                    if probe.status_code in (405, 501):
                        probe = await client.get(candidate_url)
                    if probe.status_code < 400:
                        contact_url = candidate_url
                        break
                except httpx.HTTPError:
                    continue

        if contact_url is not None:
            try:
                contact_response = await client.get(contact_url)
                contact_page = parse_page(contact_response.text)
                mailtos = extract_mailto_addresses(contact_page.links)
                for address in mailtos:
                    candidates.append(
                        EmailCandidate(
                            email=address,
                            source=EmailSource.WEBSITE_CONTACT_PAGE,
                            confidence_score=CONFIDENCE_MAILTO_CONTACT_PAGE,
                            evidence=f"mailto: link on {contact_url}",
                        )
                    )
                if not mailtos:
                    text = extract_visible_text(contact_response.text)
                    for address in extract_text_addresses(text):
                        candidates.append(
                            EmailCandidate(
                                email=address,
                                source=EmailSource.WEBSITE_CONTACT_PAGE,
                                confidence_score=CONFIDENCE_TEXT_MATCH_CONTACT_PAGE,
                                evidence=f"email-shaped text on {contact_url}",
                            )
                        )
            except httpx.HTTPError:
                logger.info("Contact page found but could not be fetched", extra={"url": contact_url})

        if not candidates and home_page is not None:
            mailtos = extract_mailto_addresses(home_page.links)
            for address in mailtos:
                candidates.append(
                    EmailCandidate(
                        email=address,
                        source=EmailSource.WEBSITE_CONTACT_PAGE,
                        confidence_score=CONFIDENCE_MAILTO_HOME_FOOTER,
                        evidence=f"mailto: link on {origin} (home page)",
                    )
                )
            if not mailtos:
                text = extract_visible_text(home_response.text)
                for address in extract_text_addresses(text):
                    candidates.append(
                        EmailCandidate(
                            email=address,
                            source=EmailSource.WEBSITE_CONTACT_PAGE,
                            confidence_score=CONFIDENCE_TEXT_MATCH_HOME_FOOTER,
                            evidence=f"email-shaped text on {origin} (home page)",
                        )
                    )

    logger.info(
        "Contact-page email check complete",
        extra={"origin": origin, "candidates_found": len(candidates)},
    )
    return candidates
