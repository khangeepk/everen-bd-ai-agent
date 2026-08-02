"""PII scrubbing for Sentry event payloads.

``send_default_pii=False`` stops Sentry from *automatically* attaching request
bodies, cookies, and user data, but it does NOT scrub PII that ends up inside
an exception message, ``extra`` context, a breadcrumb, or a captured stack
frame's local variables. Lead contact details (emails, phone numbers) must
never leave this system in an error report (AGENTS.md section 9 / Phase 25).

This module provides a ``before_send`` hook that walks the whole event and
redacts anything that looks like an email address or phone number. It is
paired in ``app.main`` with ``include_local_variables=False`` (defence in
depth: don't capture frame locals at all) so this hook is the second line,
covering message strings, ``extra``, and breadcrumbs.

Fail-closed: if scrubbing raises for any reason, the event is dropped
(returns ``None``) rather than risk sending un-scrubbed PII.
"""

from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A run starting with an optional + and a digit, then 6+ more phone-ish chars,
# ending in a digit. The replacement double-checks it has >= 7 real digits so
# short numbers (ports, counts, IDs) aren't over-redacted.
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")

_EMAIL_TOKEN = "[redacted-email]"
_PHONE_TOKEN = "[redacted-phone]"


def _redact_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return _PHONE_TOKEN if len(digits) >= 7 else match.group(0)


def scrub_text(text: str) -> str:
    """Redact email addresses and phone numbers from a single string."""
    text = _EMAIL_RE.sub(_EMAIL_TOKEN, text)
    text = _PHONE_RE.sub(_redact_phone, text)
    return text


def _scrub_value(value: Any) -> Any:
    """Recursively redact PII from strings inside dicts, lists, and tuples."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: _scrub_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def scrub_pii_from_event(event: dict[str, Any], _hint: Any = None) -> dict[str, Any] | None:
    """Sentry ``before_send`` hook: strip emails and phone numbers from an event.

    Args:
        event: The Sentry event dict about to be sent.
        _hint: Sentry's hint object (unused).

    Returns:
        The scrubbed event, or ``None`` to drop it if scrubbing failed
        (fail-closed so un-scrubbed PII is never sent).
    """
    try:
        scrubbed = _scrub_value(event)
        # _scrub_value preserves dict shape for a dict input.
        return scrubbed if isinstance(scrubbed, dict) else None
    except Exception:
        return None
