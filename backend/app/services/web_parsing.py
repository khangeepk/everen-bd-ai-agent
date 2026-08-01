"""HTML parsing for the website audit.

Standard library only (``html.parser``, ``urllib.parse``) so parsing is
testable offline and adds no dependency.

Covers three things Lighthouse does not report directly in a usable shape:

* contact form detection -- whether a form exists, what it posts to, and
  whether it collects an email or message field
* link extraction -- for the bounded broken-link crawl
* SEO meta extraction -- title, description, canonical, viewport, Open Graph
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger(__name__)

#: Input names/types suggesting a field collects an email address.
_EMAIL_HINTS = ("email", "e-mail", "mail")

#: Input/textarea names suggesting a free-text message field.
_MESSAGE_HINTS = ("message", "comment", "enquiry", "inquiry", "details", "question")

#: Link rel values that should not be followed during a crawl.
_SKIP_RELS = frozenset({"nofollow", "noopener noreferrer nofollow"})

#: URL schemes that are not crawlable pages.
_NON_HTTP_SCHEMES = frozenset({"mailto", "tel", "javascript", "sms", "data", "ftp"})


@dataclass
class FormField:
    """One input inside a form.

    Attributes:
        tag: Originating tag, ``"input"``, ``"textarea"``, or ``"select"``.
        name: The ``name`` attribute, if present.
        input_type: The ``type`` attribute for inputs.
        required: Whether the field carries the ``required`` attribute.
    """

    tag: str
    name: str | None
    input_type: str | None
    required: bool = False

    def looks_like_email(self) -> bool:
        """Whether this field appears to collect an email address.

        Returns:
            True if the type is ``email`` or the name hints at email.
        """
        if (self.input_type or "").lower() == "email":
            return True
        name = (self.name or "").lower()
        return any(hint in name for hint in _EMAIL_HINTS)

    def looks_like_message(self) -> bool:
        """Whether this field appears to collect free-text.

        Returns:
            True for textareas or names hinting at a message.
        """
        if self.tag == "textarea":
            return True
        name = (self.name or "").lower()
        return any(hint in name for hint in _MESSAGE_HINTS)


@dataclass
class DetectedForm:
    """A form found in the page.

    Attributes:
        action: Raw ``action`` attribute, if present.
        method: HTTP method, uppercased. Defaults to ``GET`` per the spec.
        fields: Fields discovered inside the form.
    """

    action: str | None
    method: str
    fields: list[FormField] = field(default_factory=list)

    def collects_email(self) -> bool:
        """Whether any field looks like an email input.

        Returns:
            True if at least one field looks like email.
        """
        return any(item.looks_like_email() for item in self.fields)

    def collects_message(self) -> bool:
        """Whether any field looks like a free-text message input.

        Returns:
            True if at least one field looks like a message.
        """
        return any(item.looks_like_message() for item in self.fields)

    def is_contact_form(self) -> bool:
        """Whether this form is plausibly a contact form.

        A contact form collects an email address and posts somewhere. Search
        boxes and newsletter signups typically fail one of those.

        Returns:
            True if the form POSTs and collects both an email and a message.
        """
        return self.method == "POST" and self.collects_email() and self.collects_message()

    def resolved_action(self, page_url: str) -> str:
        """Resolve the form action against the page URL.

        Args:
            page_url: URL the form was found on.

        Returns:
            An absolute URL. Falls back to the page URL when ``action`` is
            absent or empty, matching browser behavior.
        """
        if not self.action or not self.action.strip():
            return page_url
        return urljoin(page_url, self.action.strip())


@dataclass
class PageMeta:
    """SEO-relevant metadata extracted from a page.

    Attributes:
        title: Contents of ``<title>``.
        description: ``<meta name="description">`` content.
        canonical: ``<link rel="canonical">`` href.
        viewport: ``<meta name="viewport">`` content.
        robots: ``<meta name="robots">`` content.
        og_title: ``og:title`` content.
        og_image: ``og:image`` content.
        h1_count: Number of ``<h1>`` elements.
    """

    title: str | None = None
    description: str | None = None
    canonical: str | None = None
    viewport: str | None = None
    robots: str | None = None
    og_title: str | None = None
    og_image: str | None = None
    h1_count: int = 0

    def has_viewport(self) -> bool:
        """Whether a viewport meta tag is present.

        Its absence is the single most common cause of a site rendering
        desktop-width on phones.

        Returns:
            True if a non-empty viewport is declared.
        """
        return bool(self.viewport and self.viewport.strip())


class _PageParser(HTMLParser):
    """Single-pass collector for forms, links, and meta tags."""

    def __init__(self) -> None:
        """Initialize parser state."""
        super().__init__(convert_charrefs=True)
        self.meta = PageMeta()
        self.forms: list[DetectedForm] = []
        self.links: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._current_form: DetectedForm | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record an opening tag.

        Args:
            tag: Lowercased tag name.
            attrs: Attribute pairs.
        """
        attributes = {key.lower(): (value or "") for key, value in attrs}

        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self.meta.h1_count += 1
        elif tag == "meta":
            self._handle_meta(attributes)
        elif tag == "link" and "canonical" in attributes.get("rel", "").lower():
            self.meta.canonical = attributes.get("href")
        elif tag == "a":
            href = attributes.get("href", "").strip()
            rel = attributes.get("rel", "").strip().lower()
            if href and rel not in _SKIP_RELS:
                self.links.append(href)
        elif tag == "form":
            self._current_form = DetectedForm(
                action=attributes.get("action"),
                method=(attributes.get("method") or "GET").strip().upper(),
            )
        elif tag in {"input", "textarea", "select"} and self._current_form is not None:
            self._current_form.fields.append(
                FormField(
                    tag=tag,
                    name=attributes.get("name"),
                    input_type=attributes.get("type"),
                    required="required" in attributes,
                )
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle self-closing tags such as ``<meta />``.

        Args:
            tag: Lowercased tag name.
            attrs: Attribute pairs.
        """
        self.handle_starttag(tag, attrs)

    def _handle_meta(self, attributes: dict[str, str]) -> None:
        """Record a meta tag.

        Args:
            attributes: Lowercased attribute mapping.
        """
        name = (attributes.get("name") or attributes.get("property") or "").lower()
        content = attributes.get("content")
        if not content:
            return
        if name == "description":
            self.meta.description = content
        elif name == "viewport":
            self.meta.viewport = content
        elif name == "robots":
            self.meta.robots = content
        elif name == "og:title":
            self.meta.og_title = content
        elif name == "og:image":
            self.meta.og_image = content

    def handle_endtag(self, tag: str) -> None:
        """Record a closing tag.

        Args:
            tag: Lowercased tag name.
        """
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None

    def handle_data(self, data: str) -> None:
        """Capture text content.

        Args:
            data: Text between tags.
        """
        if self._in_title and data.strip():
            self.meta.title = (self.meta.title or "") + data.strip()


@dataclass
class ParsedPage:
    """Everything extracted from one page.

    Attributes:
        meta: SEO metadata.
        forms: Forms found on the page.
        links: Raw href values, unresolved.
    """

    meta: PageMeta
    forms: list[DetectedForm]
    links: list[str]

    def contact_forms(self) -> list[DetectedForm]:
        """Forms that look like contact forms.

        Returns:
            The subset passing :meth:`DetectedForm.is_contact_form`.
        """
        return [form for form in self.forms if form.is_contact_form()]

    def has_mailto_fallback(self) -> bool:
        """Whether the page exposes a ``mailto:`` link.

        A site with no form but a visible email address is reachable, which the
        report should not flag as hard a failure.

        Returns:
            True if any link uses the ``mailto:`` scheme.
        """
        return any(link.lower().startswith("mailto:") for link in self.links)

    def has_tel_fallback(self) -> bool:
        """Whether the page exposes a ``tel:`` link.

        Returns:
            True if any link uses the ``tel:`` scheme.
        """
        return any(link.lower().startswith("tel:") for link in self.links)


def parse_page(html: str) -> ParsedPage:
    """Parse an HTML document.

    Malformed markup is tolerated -- ``HTMLParser`` recovers rather than
    raising, which matters because audited sites are frequently broken.

    Args:
        html: Raw HTML source.

    Returns:
        The extracted :class:`ParsedPage`.
    """
    parser = _PageParser()
    parser.feed(html)
    parser.close()

    # A form left unclosed by malformed markup still counts.
    if parser._current_form is not None:
        parser.forms.append(parser._current_form)

    return ParsedPage(meta=parser.meta, forms=parser.forms, links=parser.links)


def normalize_url(url: str) -> str:
    """Normalize a URL for dedup during crawling.

    Drops the fragment, lowercases scheme and host, and removes a trailing
    slash from non-root paths, so ``/about``, ``/about/``, and ``/about#team``
    are recognized as one page.

    Args:
        url: Absolute URL.

    Returns:
        The normalized URL.
    """
    parts = urlparse(url)
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(
        (parts.scheme.lower(), parts.netloc.lower(), path or "/", parts.params, parts.query, "")
    )


def is_crawlable(url: str) -> bool:
    """Whether a URL points at a fetchable HTTP page.

    Args:
        url: Absolute or relative URL.

    Returns:
        False for ``mailto:``, ``tel:``, ``javascript:`` and similar schemes.
    """
    scheme = urlparse(url).scheme.lower()
    if not scheme:
        return True
    if scheme in _NON_HTTP_SCHEMES:
        return False
    return scheme in {"http", "https"}


def same_origin(url: str, origin: str) -> bool:
    """Whether a URL belongs to the same host as the audited site.

    The crawler never leaves the prospect's own domain -- following outbound
    links would mean fetching third parties who are not part of the audit.

    Args:
        url: Absolute URL to test.
        origin: Absolute URL of the audited site.

    Returns:
        True if the hostnames match, ignoring case and a leading ``www.``.
    """

    def host(value: str) -> str:
        netloc = urlparse(value).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc

    return bool(host(url)) and host(url) == host(origin)


def resolve_links(page: ParsedPage, page_url: str) -> list[str]:
    """Resolve a page's hrefs into absolute, crawlable, deduplicated URLs.

    Args:
        page: The parsed page.
        page_url: URL the page was fetched from.

    Returns:
        Absolute URLs, order preserved, duplicates removed.
    """
    seen: set[str] = set()
    resolved: list[str] = []

    for href in page.links:
        if not is_crawlable(href):
            continue
        absolute = normalize_url(urljoin(page_url, href))
        if absolute not in seen:
            seen.add(absolute)
            resolved.append(absolute)

    return resolved
