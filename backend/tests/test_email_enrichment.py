"""Tests for :mod:`app.services.email_enrichment`.

Covers the pure half of the email-enrichment fallback chain only (format
validation, extraction, and pattern guessing) -- the network-touching crawl
in app/services/email_discovery.py needs a live/mocked HTTP client and is
exercised separately. These tests also encode the "format-only, no paid
verifier" constraint from the request that created this feature: nothing
here ever claims a candidate is a working address, only that it is
syntactically well-formed and where it came from.
"""

from __future__ import annotations

import pytest

from app.services.email_enrichment import (
    CONFIDENCE_PATTERN_GUESS,
    MAX_PATTERN_GUESSES,
    EmailSource,
    _domain_from_website,
    _name_parts,
    extract_mailto_addresses,
    extract_text_addresses,
    guess_pattern_emails,
    is_valid_email_format,
)


# --- is_valid_email_format ---------------------------------------------------


def test_is_valid_email_format_accepts_plausible_address() -> None:
    """A normal-looking address passes the format check."""
    assert is_valid_email_format("jane.doe@example.com") is True


def test_is_valid_email_format_strips_surrounding_whitespace() -> None:
    """Whitespace around an otherwise valid address is not disqualifying."""
    assert is_valid_email_format("  jane.doe@example.com  ") is True


@pytest.mark.parametrize(
    "candidate",
    [
        "not-an-email",
        "missing-domain@",
        "@missing-local.com",
        "no-tld@example",
        "spaces in@example.com",
        "",
    ],
)
def test_is_valid_email_format_rejects_malformed_input(candidate: str) -> None:
    """Garbage input is rejected -- this is a syntax check, not a verifier."""
    assert is_valid_email_format(candidate) is False


# --- extract_mailto_addresses -------------------------------------------------


def test_extract_mailto_addresses_pulls_address_out_of_mailto_link() -> None:
    """A plain mailto: link yields its address."""
    links = ["mailto:info@example.com", "/about", "https://example.com/contact"]
    assert extract_mailto_addresses(links) == ["info@example.com"]


def test_extract_mailto_addresses_strips_query_params() -> None:
    """A mailto: link with a subject/body query string still yields a clean address."""
    links = ["mailto:sales@example.com?subject=Hello%20there"]
    assert extract_mailto_addresses(links) == ["sales@example.com"]


def test_extract_mailto_addresses_deduplicates_case_insensitively() -> None:
    """The same address in different casing is only returned once."""
    links = ["mailto:Info@Example.com", "mailto:info@example.com"]
    assert extract_mailto_addresses(links) == ["Info@Example.com"]


def test_extract_mailto_addresses_skips_malformed_mailto() -> None:
    """A mailto: link with no valid address contributes nothing."""
    links = ["mailto:", "mailto:not-an-email"]
    assert extract_mailto_addresses(links) == []


def test_extract_mailto_addresses_ignores_non_mailto_links() -> None:
    """Ordinary hrefs are not mistaken for mailto: links."""
    assert extract_mailto_addresses(["https://example.com", "/contact", "tel:+15551234567"]) == []


# --- extract_text_addresses ---------------------------------------------------


def test_extract_text_addresses_finds_email_shaped_string_in_text() -> None:
    """An email-shaped string embedded in prose is found."""
    text = "reach out any time at hello@example.com and we'll respond"
    assert extract_text_addresses(text) == ["hello@example.com"]


def test_extract_text_addresses_finds_multiple_in_order() -> None:
    """Multiple addresses are returned in first-seen order."""
    text = "sales@example.com or support@example.com"
    assert extract_text_addresses(text) == ["sales@example.com", "support@example.com"]


def test_extract_text_addresses_deduplicates() -> None:
    """A repeated address in the same text is only returned once."""
    text = "email hello@example.com or just email hello@example.com again"
    assert extract_text_addresses(text) == ["hello@example.com"]


def test_extract_text_addresses_returns_empty_for_no_match() -> None:
    """Text with no email-shaped substring yields nothing."""
    assert extract_text_addresses("give us a call, no email listed here") == []


# --- _name_parts ---------------------------------------------------------------


def test_name_parts_splits_first_and_last() -> None:
    """A plain two-word name splits cleanly."""
    assert _name_parts("Jane Doe") == ("jane", "doe")


def test_name_parts_strips_honorific() -> None:
    """A leading honorific is dropped rather than treated as a first name."""
    assert _name_parts("Dr. Jane O'Connor") == ("jane", "oconnor")


def test_name_parts_returns_none_for_single_word() -> None:
    """A single-word name has no usable last name, so no guess can be made."""
    assert _name_parts("Jane") is None


def test_name_parts_returns_none_for_empty_string() -> None:
    """An empty name yields nothing to split."""
    assert _name_parts("") is None


# --- _domain_from_website -------------------------------------------------------


def test_domain_from_website_strips_scheme_and_path() -> None:
    """Only the bare host is kept."""
    assert _domain_from_website("https://www.example.com/about") == "example.com"


def test_domain_from_website_drops_leading_www() -> None:
    """A www. prefix is stripped so guesses use the canonical domain."""
    assert _domain_from_website("https://www.acme.co") == "acme.co"


def test_domain_from_website_returns_empty_for_blank_input() -> None:
    """No website on file means no domain to guess against."""
    assert _domain_from_website("") == ""


# --- guess_pattern_emails -------------------------------------------------------


def test_guess_pattern_emails_generates_expected_permutations() -> None:
    """The standard first.last / flast / first / etc. permutations are produced."""
    candidates = guess_pattern_emails("https://example.com", "Jane Doe")
    emails = {c.email for c in candidates}
    assert "jane.doe@example.com" in emails
    assert "janedoe@example.com" in emails
    assert "jdoe@example.com" in emails
    assert "jane@example.com" in emails


def test_guess_pattern_emails_all_candidates_are_pattern_guess_source() -> None:
    """Every candidate from this function is marked PATTERN_GUESS, never a stronger source."""
    candidates = guess_pattern_emails("https://example.com", "Jane Doe")
    assert candidates
    assert all(c.source == EmailSource.PATTERN_GUESS for c in candidates)


def test_guess_pattern_emails_all_candidates_share_the_conservative_confidence() -> None:
    """A guess is a guess -- no permutation is scored higher than another."""
    candidates = guess_pattern_emails("https://example.com", "Jane Doe")
    assert candidates
    assert all(c.confidence_score == CONFIDENCE_PATTERN_GUESS for c in candidates)


def test_guess_pattern_emails_respects_max_cap() -> None:
    """The candidate list never exceeds MAX_PATTERN_GUESSES."""
    candidates = guess_pattern_emails("https://example.com", "Jane Doe")
    assert len(candidates) <= MAX_PATTERN_GUESSES


def test_guess_pattern_emails_returns_empty_without_a_website() -> None:
    """No domain means no pattern can be built."""
    assert guess_pattern_emails("", "Jane Doe") == []


def test_guess_pattern_emails_returns_empty_without_a_contact_name() -> None:
    """No name on file means this function does not fall back to role addresses."""
    assert guess_pattern_emails("https://example.com", None) == []


def test_guess_pattern_emails_returns_empty_for_single_word_name() -> None:
    """A single-word name has no usable last name to build a pattern from."""
    assert guess_pattern_emails("https://example.com", "Cher") == []
