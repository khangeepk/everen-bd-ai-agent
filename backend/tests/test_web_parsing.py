"""Tests for :mod:`app.services.web_parsing`."""

from __future__ import annotations

import pytest

from app.services.web_parsing import (
    is_crawlable,
    normalize_url,
    parse_page,
    resolve_links,
    same_origin,
)
from tests.sample_audit_data import (
    HTML_INSECURE_FORM,
    HTML_NEWSLETTER_ONLY,
    HTML_NO_CONTACT_FORM,
    HTML_SEARCH_ONLY,
    HTML_WITH_CONTACT_FORM,
)


def test_meta_is_extracted() -> None:
    """Title, description, viewport, canonical, and og:title are read."""
    page = parse_page(HTML_WITH_CONTACT_FORM)

    assert page.meta.title == "Congress Dental - Book an appointment"
    assert page.meta.description == "Family dentistry in central Austin."
    assert page.meta.viewport == "width=device-width, initial-scale=1"
    assert page.meta.canonical == "https://example-good.test/"
    assert page.meta.og_title == "Congress Dental"
    assert page.meta.h1_count == 1


def test_missing_viewport_is_detected() -> None:
    """A page with no viewport tag reports it, which drives the mobile finding."""
    assert parse_page(HTML_NO_CONTACT_FORM).meta.has_viewport() is False


def test_present_viewport_is_detected() -> None:
    """A page with a viewport tag reports it."""
    assert parse_page(HTML_WITH_CONTACT_FORM).meta.has_viewport() is True


def test_contact_form_is_detected() -> None:
    """A POST form collecting an email and a message is a contact form."""
    forms = parse_page(HTML_WITH_CONTACT_FORM).contact_forms()

    assert len(forms) == 1
    assert forms[0].method == "POST"
    assert forms[0].collects_email() is True
    assert forms[0].collects_message() is True


def test_newsletter_signup_is_not_a_contact_form() -> None:
    """An email-only signup must not be mistaken for a contact form."""
    page = parse_page(HTML_NEWSLETTER_ONLY)

    assert page.forms
    assert page.contact_forms() == []


def test_search_box_is_not_a_contact_form() -> None:
    """A GET search form is not a contact form."""
    page = parse_page(HTML_SEARCH_ONLY)

    assert page.forms
    assert page.contact_forms() == []


def test_page_with_no_forms_reports_none() -> None:
    """A page without forms yields no contact forms."""
    assert parse_page(HTML_NO_CONTACT_FORM).contact_forms() == []


def test_malformed_html_is_tolerated() -> None:
    """Unclosed tags do not raise -- audited sites are frequently broken."""
    page = parse_page(HTML_NO_CONTACT_FORM)

    assert page.meta.title == "Poor Site"
    assert len(page.links) >= 3


def test_form_action_resolves_against_page_url() -> None:
    """A relative action resolves to an absolute URL."""
    form = parse_page(HTML_WITH_CONTACT_FORM).contact_forms()[0]

    assert (
        form.resolved_action("https://example-good.test/contact")
        == "https://example-good.test/submit-enquiry"
    )


def test_empty_form_action_falls_back_to_page_url() -> None:
    """An absent action posts back to the page, matching browser behaviour."""
    page = parse_page(
        '<form method="post"><input type="email" name="email">'
        "<textarea name='message'></textarea></form>"
    )
    form = page.contact_forms()[0]

    assert form.resolved_action("https://x.test/contact") == "https://x.test/contact"


def test_insecure_form_action_is_visible() -> None:
    """An http:// action is preserved so the insecure-form finding can fire."""
    form = parse_page(HTML_INSECURE_FORM).contact_forms()[0]

    assert form.resolved_action("https://secure.test/").startswith("http://")


def test_mailto_and_tel_fallbacks_are_detected() -> None:
    """Visible email and phone links count as contact fallbacks."""
    page = parse_page(HTML_WITH_CONTACT_FORM)

    assert page.has_mailto_fallback() is True
    assert page.has_tel_fallback() is True


def test_absent_fallbacks_are_reported() -> None:
    """A page with neither fallback reports neither."""
    page = parse_page(HTML_NO_CONTACT_FORM)

    assert page.has_mailto_fallback() is False
    assert page.has_tel_fallback() is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.test/About/", "https://example.test/About"),
        ("https://example.test/about#team", "https://example.test/about"),
        ("HTTPS://EXAMPLE.TEST/", "https://example.test/"),
        ("https://example.test", "https://example.test/"),
    ],
)
def test_url_normalization(raw: str, expected: str) -> None:
    """Normalization collapses case, trailing slashes, and fragments."""
    assert normalize_url(raw) == expected


def test_normalization_makes_variants_equal() -> None:
    """Three spellings of one page normalize identically."""
    variants = [
        "https://example.test/about",
        "https://example.test/about/",
        "https://example.test/about#team",
    ]
    assert len({normalize_url(url) for url in variants}) == 1


@pytest.mark.parametrize(
    "url", ["mailto:a@b.test", "tel:+15125550101", "javascript:void(0)", "sms:+1234"]
)
def test_non_http_schemes_are_not_crawlable(url: str) -> None:
    """Contact and script links are never fetched."""
    assert is_crawlable(url) is False


@pytest.mark.parametrize("url", ["https://example.test/a", "http://example.test/b", "/relative"])
def test_http_and_relative_urls_are_crawlable(url: str) -> None:
    """Ordinary page links are crawlable."""
    assert is_crawlable(url) is True


def test_same_origin_ignores_www_and_case() -> None:
    """www and casing do not make a URL foreign."""
    origin = "https://example.test/"

    assert same_origin("https://www.example.test/about", origin) is True
    assert same_origin("https://EXAMPLE.test/about", origin) is True


def test_outbound_links_are_not_same_origin() -> None:
    """Third-party hosts are recognized as off-site."""
    assert same_origin("https://other.test/page", "https://example.test/") is False


def test_resolve_links_produces_absolute_deduplicated_urls() -> None:
    """Relative hrefs resolve, duplicates collapse, non-HTTP schemes drop."""
    page = parse_page(HTML_WITH_CONTACT_FORM)
    links = resolve_links(page, "https://example-good.test/")

    assert "https://example-good.test/about" in links
    assert "https://external-site.test/partner" in links
    assert not any(link.startswith("mailto:") for link in links)
    assert not any(link.startswith("tel:") for link in links)
    assert len(links) == len(set(links))
