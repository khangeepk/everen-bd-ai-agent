"""Job-posting signal detection: has a lead's careers/jobs page changed?

Unlike the two Places-derived signals, this one is free (no billed API) and
carries no Google Maps Content restriction -- it looks only at a page on the
lead's own website, using the same politeness rules as the website audit
crawler in ``app/services/site_checks.py`` (robots.txt respected, identifying
User-Agent, single page fetch rather than a full crawl).

This is a heuristic, not a structured job-listing parser: it fingerprints
the visible text of the best-guess careers/jobs page and flags a change
since the last check, optionally noting whether hiring-related keywords are
present. A change could be a new posting, a removed one, or an unrelated
copy edit -- the signal means "this page is worth a human glance," not
"a new role was definitely posted." That caveat is surfaced in the detail
text written to LeadSignal.detail (see app/services/signal_scanner.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.web_parsing import normalize_url, parse_page, resolve_links, same_origin

logger = logging.getLogger(__name__)

#: Reuses the audit crawler's identity -- same bot, same contact URL, so a
#: site owner seeing it in their logs finds one explanation, not two.
USER_AGENT = "EverenBDAuditBot/1.0 (+https://everentechno.example/audit-bot)"

DEFAULT_TIMEOUT_SECONDS = 15.0

#: Substrings in a link's path that suggest it points at a careers/jobs page.
CAREERS_PATH_HINTS: tuple[str, ...] = (
    "career", "careers", "jobs", "job-openings", "join-us", "join-our-team",
    "work-with-us", "employment", "positions", "hiring", "vacancies",
)

#: Common careers-page paths to try when no link on the home page matches --
#: many sites have one of these even when it's not linked from the nav.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/careers", "/jobs", "/careers/", "/jobs/", "/about/careers",
    "/company/careers", "/join-us", "/about-us/careers",
)

#: Phrases suggesting the page is actively advertising open roles, as opposed
#: to a generic "we're a great place to work" page with no current openings.
JOB_POSTING_KEYWORDS: tuple[str, ...] = (
    "we're hiring", "we are hiring", "open position", "open positions",
    "current openings", "join our team", "apply now", "career opportunities",
    "job opening", "now hiring",
)

#: Bound on how much text is fingerprinted/held in memory per page -- large
#: enough to cover a realistic careers page, small enough to not become an
#: unbounded copy of a prospect's site sitting in a request's memory.
MAX_TEXT_EXCERPT_CHARS = 8000


class JobSignalError(RuntimeError):
    """Raised when the careers-page check cannot be completed at all."""


@dataclass(frozen=True)
class CareersPageSnapshot:
    """What was found when checking a lead's site for a careers/jobs page.

    Attributes:
        found: Whether a plausible careers/jobs page was located.
        url: The page's URL, if found.
        text_excerpt: Visible text extracted from the page (lowercased,
            bounded to MAX_TEXT_EXCERPT_CHARS), for fingerprinting. Never
            persisted verbatim -- only its hash is (see
            app/services/signal_detection.py::content_change_fingerprint).
        looks_like_job_posting: Whether hiring-related keywords were found,
            i.e. this looks like it's actively advertising roles right now.
    """

    found: bool
    url: str | None = None
    text_excerpt: str | None = None
    looks_like_job_posting: bool = False


class _VisibleTextExtractor(HTMLParser):
    """Collects visible text, skipping script/style content."""

    def __init__(self) -> None:
        """Initialize parser state."""
        super().__init__(convert_charrefs=True)
        self._skip = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start skipping text inside script/style elements.

        Args:
            tag: Lowercased tag name.
            attrs: Attribute pairs (unused).
        """
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        """Stop skipping once a script/style element closes.

        Args:
            tag: Lowercased tag name.
        """
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        """Capture non-skipped text content.

        Args:
            data: Text between tags.
        """
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def extract_visible_text(html: str) -> str:
    """Extract lowercased visible text from an HTML document.

    Deliberately crude (no layout/semantic awareness) -- this only needs to
    be stable enough for change detection and keyword matching, not a
    faithful rendering.

    Args:
        html: Raw HTML source.

    Returns:
        Lowercased, space-joined visible text.
    """
    parser = _VisibleTextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(parser.chunks).lower()


async def _robots_allows(client: httpx.AsyncClient, url: str, user_agent: str) -> bool:
    """Check whether robots.txt permits fetching a URL.

    A missing or unreadable robots.txt is treated as permissive, matching
    app.services.site_checks's convention.

    Args:
        client: HTTP client to use.
        url: URL to check.
        user_agent: This crawler's identifying User-Agent.

    Returns:
        True if the fetch is permitted.
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


def _looks_like_careers_link(href: str) -> bool:
    """Whether a resolved link's path suggests a careers/jobs page.

    Args:
        href: An absolute URL.

    Returns:
        True if the path contains a recognized hint.
    """
    path = urlparse(href).path.lower()
    return any(hint in path for hint in CAREERS_PATH_HINTS)


async def find_careers_page(
    website_url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """Locate a lead's careers/jobs page, if one can be found.

    First checks the home page's own links for one matching a careers-like
    path; if none match, tries a short list of common careers-page paths.

    Args:
        website_url: The lead's website root URL.
        timeout_seconds: Per-request timeout.

    Returns:
        The careers page's absolute URL, or None if nothing plausible was
        found (not an error -- most small businesses have no careers page).

    Raises:
        JobSignalError: If the home page itself could not be fetched at all.
    """
    origin = normalize_url(website_url)
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(
        timeout=timeout_seconds, headers=headers, follow_redirects=True
    ) as client:
        if not await _robots_allows(client, origin, USER_AGENT):
            logger.info(
                "robots.txt disallows the careers-page check", extra={"url": origin}
            )
            return None

        try:
            response = await client.get(origin)
        except httpx.HTTPError as exc:
            raise JobSignalError(f"Could not fetch {origin}: {exc}") from exc

        if "html" in response.headers.get("content-type", "").lower():
            page = parse_page(response.text)
            for link in resolve_links(page, origin):
                if same_origin(link, origin) and _looks_like_careers_link(link):
                    logger.info("Found careers page via home page link", extra={"url": link})
                    return link

        for candidate_path in CANDIDATE_PATHS:
            candidate_url = normalize_url(origin.rstrip("/") + candidate_path)
            if not await _robots_allows(client, candidate_url, USER_AGENT):
                continue
            try:
                probe = await client.head(candidate_url)
                if probe.status_code in (405, 501):
                    probe = await client.get(candidate_url)
                if probe.status_code < 400:
                    logger.info("Found careers page via path guess", extra={"url": candidate_url})
                    return candidate_url
            except httpx.HTTPError:
                continue

    logger.info("No careers/jobs page found", extra={"origin": origin})
    return None


async def check_careers_page(
    website_url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> CareersPageSnapshot:
    """Fetch and fingerprint a lead's careers/jobs page, if one exists.

    Args:
        website_url: The lead's website root URL.
        timeout_seconds: Per-request timeout.

    Returns:
        A snapshot of what was found. ``found=False`` is a normal outcome,
        not an error -- most small businesses have no careers page.

    Raises:
        JobSignalError: If the site could not be reached at all.
    """
    careers_url = await find_careers_page(website_url, timeout_seconds=timeout_seconds)
    if careers_url is None:
        return CareersPageSnapshot(found=False)

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout_seconds, headers=headers, follow_redirects=True
    ) as client:
        try:
            response = await client.get(careers_url)
        except httpx.HTTPError as exc:
            logger.warning(
                "Careers page found but could not be fetched",
                extra={"url": careers_url},
            )
            raise JobSignalError(f"Could not fetch {careers_url}: {exc}") from exc

    text = extract_visible_text(response.text)[:MAX_TEXT_EXCERPT_CHARS]
    looks_like_posting = any(keyword in text for keyword in JOB_POSTING_KEYWORDS)

    return CareersPageSnapshot(
        found=True,
        url=careers_url,
        text_excerpt=text,
        looks_like_job_posting=looks_like_posting,
    )
