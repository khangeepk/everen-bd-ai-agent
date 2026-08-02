"""Tests for the Sentry before_send PII scrubber (app/core/sentry_scrub.py).

Proves lead contact details are stripped from an error-report payload before
it would leave the system, covering exception messages, extra context, and
breadcrumbs -- the places send_default_pii=False does NOT reach.
"""

from __future__ import annotations

from app.core.sentry_scrub import scrub_pii_from_event, scrub_text


def test_scrub_text_redacts_email_and_phone() -> None:
    """Emails and phone numbers are replaced with redaction tokens."""
    text = "Failed to email jane.doe@example.com at +1 (555) 123-4567 today"
    scrubbed = scrub_text(text)
    assert "jane.doe@example.com" not in scrubbed
    assert "555" not in scrubbed
    assert "[redacted-email]" in scrubbed
    assert "[redacted-phone]" in scrubbed


def test_scrub_does_not_over_redact_short_numbers() -> None:
    """Short numeric values (ports, counts) are not mistaken for phone numbers."""
    text = "Retried 3 times on port 5432 with status 404"
    assert scrub_text(text) == text


def test_scrub_event_walks_nested_structure() -> None:
    """A realistic Sentry event has PII stripped from message, extra, and crumbs."""
    event = {
        "exception": {
            "values": [
                {"type": "ValueError", "value": "bad lead bob@acme.co / 447700900123"},
            ]
        },
        "extra": {"lead_contact": "carol@shop.io", "note": "call 212-555-0198"},
        "breadcrumbs": {
            "values": [{"message": "sending to dave@firm.net"}],
        },
        "level": "error",
    }
    scrubbed = scrub_pii_from_event(event)
    assert scrubbed is not None
    flat = repr(scrubbed)
    for leaked in ("bob@acme.co", "carol@shop.io", "dave@firm.net", "447700900123", "212-555-0198"):
        assert leaked not in flat, f"{leaked} leaked into the Sentry event"
    # Non-PII fields are preserved.
    assert scrubbed["level"] == "error"
    assert scrubbed["exception"]["values"][0]["type"] == "ValueError"


def test_scrub_event_fail_closed_on_bad_input() -> None:
    """A non-dict event is dropped rather than sent un-scrubbed."""
    assert scrub_pii_from_event("not-a-dict") is None  # type: ignore[arg-type]
