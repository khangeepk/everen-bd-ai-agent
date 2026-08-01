"""Direct site checks: SSL, contact form, and bounded broken-link crawl.

CRAWLING POLICY -- this module fetches pages from a prospect's own website.
That is only defensible if it stays polite, so the crawler:

* fetches and obeys ``robots.txt`` before any other request
* identifies itself in the User-Agent with a contact URL
* stays on the audited host and never follows outbound links
* caps total pages (:data:`DEFAULT_MAX_PAGES`) and crawl depth
* rate-limits to one request per :data:`DEFAULT_DELAY_SECONDS`
* uses HEAD before GET so link checks transfer almost no data

It never submits a form. Contact-form checking is detection plus a HEAD probe
of the form endpoint -- enough to tell whether the endpoint exists, without
sending a message to anyone's inbox.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.audit_scoring import Finding, FindingCategory, Severity
from app.services.web_parsing import (
    ParsedPage,
    normalize_url,
    parse_page,
    resolve_links,
    same_origin,
)

logger = logging.getLogger(__name__)

#: Identifies the crawler and points at a page explaining it. Replace the URL
#: with a real one before production -- an unidentified crawler is the thing
#: site owners block and complain about.
USER_AGENT = "EverenBDAuditBot/1.0 (+https://everentechno.example/audit-bot)"

DEFAULT_MAX_PAGES = 25
DEFAULT_MAX_DEPTH = 2
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 15.0

#: Certificates expiring within this window are flagged.
SSL_EXPIRY_WARNING_DAYS = 30


class SiteCheckError(RuntimeError):
    """Raised when a site check cannot be completed."""


@dataclass(frozen=True)
class SSLStatus:
    """Result of inspecting a site's TLS certificate.

    Attributes:
        supports_https: Whether an HTTPS connection succeeded.
        valid: Whether the certificate validated against the trust store.
        expires_at: Certificate expiry, if it could be read.
        days_until_expiry: Days remaining, negative if already expired.
        issuer: Certificate issuer common name.
        error: Failure description when validation failed.
    """

    supports_https: bool
    valid: bool
    expires_at: datetime | None = None
    days_until_expiry: int | None = None
    issuer: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BrokenLink:
    """A link that did not resolve successfully.

    Attributes:
        url: The link target.
        status_code: HTTP status returned, if any.
        found_on: Page the link appeared on.
        error: Transport error description, if the request failed outright.
    """

    url: str
    status_code: int | None
    found_on: str
    error: str | None = None


@dataclass
class CrawlResult:
    """Outcome of a bounded crawl.

    Attributes:
        pages_crawled: How many pages were fetched.
        links_checked: How many unique links were tested.
        broken_links: Links that failed.
        robots_blocked: True if robots.txt disallowed the starting URL.
        home_page: The parsed home page, when it was fetched.
    """

    pages_crawled: int = 0
    links_checked: int = 0
    broken_links: list[BrokenLink] = field(default_factory=list)
    robots_blocked: bool = False
    home_page: ParsedPage | None = None


@dataclass(frozen=True)
class ContactFormStatus:
    """Result of contact form detection.

    Attributes:
        form_found: Whether a plausible contact form was detected.
        endpoint: Resolved form action URL, if a form was found.
        endpoint_reachable: Whether a HEAD probe of the endpoint succeeded.
            None when not probed.
        endpoint_status: Status code returned by the probe.
        posts_over_https: Whether the endpoint uses HTTPS.
        has_mailto_fallback: Whether the page exposes a ``mailto:`` link.
        has_tel_fallback: Whether the page exposes a ``tel:`` link.
    """

    form_found: bool
    endpoint: str | None = None
    endpoint_reachable: bool | None = None
    endpoint_status: int | None = None
    posts_over_https: bool = False
    has_mailto_fallback: bool = False
    has_tel_fallback: bool = False


async def check_ssl(hostname: str, port: int = 443, timeout: float = 10.0) -> SSLStatus:
    """Inspect a host's TLS certificate.

    Args:
        hostname: Host to connect to, without scheme.
        port: TLS port.
        timeout: Connection timeout in seconds.

    Returns:
        The certificate status. Failures are reported in the result rather than
        raised -- an invalid certificate is a finding, not an error.
    """
    context = ssl.create_default_context()

    try:
        future = asyncio.open_connection(hostname, port, ssl=context, server_hostname=hostname)
        reader, writer = await asyncio.wait_for(future, timeout=timeout)
    except ssl.SSLCertVerificationError as exc:
        logger.info("TLS certificate failed verification", extra={"host": hostname})
        return SSLStatus(supports_https=True, valid=False, error=str(exc))
    except (TimeoutError, OSError) as exc:
        logger.info("HTTPS connection failed", extra={"host": hostname})
        return SSLStatus(supports_https=False, valid=False, error=str(exc))

    try:
        certificate = writer.get_extra_info("ssl_object").getpeercert()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass

    expires_at: datetime | None = None
    days_left: int | None = None
    if certificate and certificate.get("notAfter"):
        try:
            expires_at = datetime.strptime(
                certificate["notAfter"], "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=timezone.utc)
            days_left = (expires_at - datetime.now(timezone.utc)).days
        except ValueError:
            logger.warning("Could not parse certificate expiry", extra={"host": hostname})

    issuer = None
    for group in certificate.get("issuer", ()) if certificate else ():
        for key, value in group:
            if key == "organizationName":
                issuer = value

    return SSLStatus(
        supports_https=True,
        valid=True,
        expires_at=expires_at,
        days_until_expiry=days_left,
        issuer=issuer,
    )


def findings_from_ssl(status: SSLStatus) -> list[Finding]:
    """Derive findings from a TLS check.

    Args:
        status: The certificate status.

    Returns:
        Security findings, empty when the certificate is healthy.
    """
    if not status.supports_https:
        return [
            Finding(
                code="ssl_no_https",
                category=FindingCategory.SECURITY,
                severity=Severity.CRITICAL,
                title="Website does not support HTTPS",
                detail=(
                    "Visitors see a 'Not secure' warning in their browser, and any "
                    "information they type is sent unencrypted. Search engines also "
                    "rank insecure sites lower."
                ),
                score=0.0,
            )
        ]

    if not status.valid:
        return [
            Finding(
                code="ssl_invalid_certificate",
                category=FindingCategory.SECURITY,
                severity=Severity.CRITICAL,
                title="Security certificate is not valid",
                detail=(
                    "Browsers show a full-page security warning before visitors can "
                    "reach the site. Most people turn back at that point."
                ),
                evidence=(status.error,) if status.error else (),
                score=0.0,
            )
        ]

    if status.days_until_expiry is not None and status.days_until_expiry < 0:
        return [
            Finding(
                code="ssl_expired",
                category=FindingCategory.SECURITY,
                severity=Severity.CRITICAL,
                title="Security certificate has expired",
                detail="Visitors are being blocked by a browser security warning right now.",
                score=0.0,
            )
        ]

    if (
        status.days_until_expiry is not None
        and status.days_until_expiry <= SSL_EXPIRY_WARNING_DAYS
    ):
        return [
            Finding(
                code="ssl_expiring_soon",
                category=FindingCategory.SECURITY,
                severity=Severity.HIGH,
                title=f"Security certificate expires in {status.days_until_expiry} days",
                detail=(
                    "When it expires, browsers will block visitors with a security "
                    "warning. Renewal is usually automatic but clearly is not here."
                ),
                score=0.5,
            )
        ]

    return []


class SiteCrawler:
    """Bounded, robots-respecting crawler for broken-link detection."""

    def __init__(
        self,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
        """Initialize the crawler.

        Args:
            max_pages: Hard cap on pages fetched.
            max_depth: Hard cap on link depth from the start URL.
            delay_seconds: Minimum delay between requests.
            timeout_seconds: Per-request timeout.
            user_agent: Identifying User-Agent string.

        Raises:
            ValueError: If any bound is non-positive or the delay is negative.
        """
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")

        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    async def _load_robots(self, client: httpx.AsyncClient, origin: str) -> RobotFileParser:
        """Fetch and parse robots.txt for the origin.

        A missing or unreadable robots.txt is treated as permissive, matching
        the convention every major crawler follows.

        Args:
            client: HTTP client to use.
            origin: Absolute URL of the audited site.

        Returns:
            A parser reflecting the site's rules.
        """
        parts = urlparse(origin)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            response = await client.get(robots_url)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                logger.info("robots.txt loaded", extra={"url": robots_url})
            else:
                parser.allow_all = True
                logger.info(
                    "No robots.txt; treating as permissive",
                    extra={"url": robots_url, "status": response.status_code},
                )
        except httpx.HTTPError:
            parser.allow_all = True
            logger.info("robots.txt unreachable; treating as permissive", extra={"url": robots_url})

        return parser

    async def crawl(self, start_url: str) -> CrawlResult:
        """Crawl a site within the configured bounds and report broken links.

        Args:
            start_url: Absolute URL of the site's home page.

        Returns:
            The crawl outcome. If robots.txt disallows the start URL, returns
            immediately with ``robots_blocked=True``.

        Raises:
            SiteCheckError: If the start URL cannot be fetched at all.
        """
        origin = normalize_url(start_url)
        result = CrawlResult()
        headers = {"User-Agent": self.user_agent}

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=headers, follow_redirects=True
        ) as client:
            robots = await self._load_robots(client, origin)

            if not robots.can_fetch(self.user_agent, origin):
                logger.info("robots.txt disallows the audit", extra={"url": origin})
                result.robots_blocked = True
                return result

            queue: list[tuple[str, int]] = [(origin, 0)]
            visited: set[str] = set()
            checked_links: set[str] = set()

            while queue and result.pages_crawled < self.max_pages:
                url, depth = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                if not robots.can_fetch(self.user_agent, url):
                    logger.info("Skipping robots-disallowed page", extra={"url": url})
                    continue

                try:
                    response = await client.get(url)
                except httpx.HTTPError as exc:
                    if url == origin:
                        raise SiteCheckError(f"Could not fetch {origin}: {exc}") from exc
                    logger.info("Page fetch failed", extra={"url": url})
                    continue

                result.pages_crawled += 1
                await asyncio.sleep(self.delay_seconds)

                content_type = response.headers.get("content-type", "")
                if "html" not in content_type.lower():
                    continue

                page = parse_page(response.text)
                if url == origin:
                    result.home_page = page

                links = resolve_links(page, url)

                for link in links:
                    if link in checked_links:
                        continue
                    checked_links.add(link)

                    broken = await self._check_link(client, link, found_on=url)
                    result.links_checked += 1
                    if broken is not None:
                        result.broken_links.append(broken)

                    await asyncio.sleep(self.delay_seconds)

                    if same_origin(link, origin) and depth < self.max_depth:
                        if link not in visited:
                            queue.append((link, depth + 1))

        logger.info(
            "Crawl complete",
            extra={
                "origin": origin,
                "pages": result.pages_crawled,
                "links_checked": result.links_checked,
                "broken": len(result.broken_links),
            },
        )
        return result

    async def _check_link(
        self, client: httpx.AsyncClient, url: str, found_on: str
    ) -> BrokenLink | None:
        """Test one link with HEAD, falling back to GET.

        Some servers reject HEAD with 405 or 501 while serving GET fine, so a
        HEAD rejection is retried rather than reported as broken.

        Args:
            client: HTTP client to use.
            url: Link to test.
            found_on: Page the link appeared on.

        Returns:
            A :class:`BrokenLink` if the link failed, else None.
        """
        try:
            response = await client.head(url)
            if response.status_code in (405, 501):
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return BrokenLink(url=url, status_code=None, found_on=found_on, error=str(exc))

        if response.status_code >= 400:
            return BrokenLink(url=url, status_code=response.status_code, found_on=found_on)
        return None


def findings_from_crawl(result: CrawlResult) -> list[Finding]:
    """Derive findings from a crawl result.

    Args:
        result: The crawl outcome.

    Returns:
        Broken-link findings, empty when nothing was broken.
    """
    if result.robots_blocked:
        return [
            Finding(
                code="crawl_robots_blocked",
                category=FindingCategory.BROKEN_LINKS,
                severity=Severity.INFO,
                title="Link checking skipped",
                detail=(
                    "The site's robots.txt asks automated tools not to crawl it, so "
                    "we did not check its links."
                ),
            )
        ]

    if not result.broken_links:
        return []

    count = len(result.broken_links)
    severity = Severity.HIGH if count >= 5 else Severity.MEDIUM
    evidence = tuple(
        f"{link.url} ({link.status_code or link.error})" for link in result.broken_links[:10]
    )

    return [
        Finding(
            code="broken_links",
            category=FindingCategory.BROKEN_LINKS,
            severity=severity,
            title=f"{count} broken link{'s' if count != 1 else ''} found",
            detail=(
                "Visitors clicking these reach an error page instead of the content "
                "they wanted. Search engines also treat broken links as a quality signal."
            ),
            evidence=evidence,
        )
    ]


async def check_contact_form(
    page: ParsedPage, page_url: str, *, probe_endpoint: bool = True, timeout: float = 10.0
) -> ContactFormStatus:
    """Detect a contact form and optionally probe its endpoint.

    NEVER submits the form. The probe is a HEAD request that sends no data --
    enough to tell whether the endpoint exists, without delivering a message to
    anyone.

    Args:
        page: The parsed page to inspect.
        page_url: URL the page was fetched from.
        probe_endpoint: Whether to HEAD the form's action URL.
        timeout: Probe timeout in seconds.

    Returns:
        The contact form status.
    """
    forms = page.contact_forms()
    if not forms:
        return ContactFormStatus(
            form_found=False,
            has_mailto_fallback=page.has_mailto_fallback(),
            has_tel_fallback=page.has_tel_fallback(),
        )

    form = forms[0]
    endpoint = form.resolved_action(page_url)
    posts_over_https = urlparse(endpoint).scheme.lower() == "https"

    reachable: bool | None = None
    status_code: int | None = None

    if probe_endpoint:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            ) as client:
                response = await client.head(endpoint)
                status_code = response.status_code
                # 405 means the endpoint exists but rejects HEAD, which is
                # normal for a POST-only handler and counts as reachable.
                reachable = response.status_code < 400 or response.status_code == 405
        except httpx.HTTPError as exc:
            logger.info("Contact form endpoint probe failed", extra={"endpoint": endpoint})
            reachable = False
            status_code = None
            del exc

    return ContactFormStatus(
        form_found=True,
        endpoint=endpoint,
        endpoint_reachable=reachable,
        endpoint_status=status_code,
        posts_over_https=posts_over_https,
        has_mailto_fallback=page.has_mailto_fallback(),
        has_tel_fallback=page.has_tel_fallback(),
    )


def findings_from_contact_form(status: ContactFormStatus) -> list[Finding]:
    """Derive findings from contact form detection.

    Args:
        status: The contact form status.

    Returns:
        Contact form findings, empty when the form looks healthy.
    """
    if not status.form_found:
        has_fallback = status.has_mailto_fallback or status.has_tel_fallback
        return [
            Finding(
                code="contact_form_missing",
                category=FindingCategory.CONTACT_FORM,
                severity=Severity.MEDIUM if has_fallback else Severity.HIGH,
                title="No contact form found",
                detail=(
                    "Visitors have to copy an email address or dial a number rather "
                    "than getting in touch in one click. Adding a form typically "
                    "increases enquiries."
                    if has_fallback
                    else "There is no contact form and no visible email or phone link, "
                    "so an interested visitor has no obvious way to get in touch."
                ),
            )
        ]

    findings: list[Finding] = []

    if status.endpoint_reachable is False:
        findings.append(
            Finding(
                code="contact_form_endpoint_unreachable",
                category=FindingCategory.CONTACT_FORM,
                severity=Severity.CRITICAL,
                title="Contact form may be broken",
                detail=(
                    "The form's submission address did not respond. Enquiries "
                    "submitted through it may never arrive. This is worth testing by "
                    "hand as a priority."
                ),
                evidence=(status.endpoint,) if status.endpoint else (),
            )
        )

    if not status.posts_over_https:
        findings.append(
            Finding(
                code="contact_form_insecure",
                category=FindingCategory.CONTACT_FORM,
                severity=Severity.HIGH,
                title="Contact form submits over an insecure connection",
                detail=(
                    "Details typed into the form are sent unencrypted, and browsers "
                    "warn visitors before they submit."
                ),
                evidence=(status.endpoint,) if status.endpoint else (),
            )
        )

    return findings
