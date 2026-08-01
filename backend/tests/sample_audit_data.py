"""Sample Lighthouse payloads and HTML fixtures for audit tests.

All fabricated. The HTML fixtures deliberately include malformed markup,
because real audited sites frequently are.
"""

from __future__ import annotations

from typing import Any


def _audit(score: float | None, display: str | None = None) -> dict[str, Any]:
    """Build a Lighthouse audit entry.

    Args:
        score: Audit score, or None for informational audits.
        display: Optional display value.

    Returns:
        An audit dict.
    """
    entry: dict[str, Any] = {"score": score}
    if display is not None:
        entry["displayValue"] = display
    return entry


#: A site in poor shape: slow, insecure, no viewport, no title.
POOR_SITE_MOBILE: dict[str, Any] = {
    "lighthouseResult": {
        "requestedUrl": "http://example-poor.test/",
        "finalUrl": "http://example-poor.test/",
        "lighthouseVersion": "12.0.0",
        "categories": {
            "performance": {"id": "performance", "score": 0.21},
            "accessibility": {"id": "accessibility", "score": 0.44},
            "best-practices": {"id": "best-practices", "score": 0.58},
            "seo": {"id": "seo", "score": 0.35},
        },
        "audits": {
            "is-on-https": _audit(0.0),
            "viewport": _audit(0.0),
            "document-title": _audit(0.0),
            "meta-description": _audit(0.0),
            "image-alt": _audit(0.0),
            "color-contrast": _audit(0.0),
            "errors-in-console": _audit(0.0),
            "largest-contentful-paint": _audit(0.1, "6.4 s"),
            "first-contentful-paint": _audit(0.2, "3.1 s"),
            "total-blocking-time": _audit(0.3, "890 ms"),
            "unminified-css": _audit(1.0),
        },
    }
}

#: The same poor site on desktop: better performance, same structural issues.
POOR_SITE_DESKTOP: dict[str, Any] = {
    "lighthouseResult": {
        "requestedUrl": "http://example-poor.test/",
        "finalUrl": "http://example-poor.test/",
        "lighthouseVersion": "12.0.0",
        "categories": {
            "performance": {"id": "performance", "score": 0.62},
            "accessibility": {"id": "accessibility", "score": 0.44},
            "best-practices": {"id": "best-practices", "score": 0.58},
            "seo": {"id": "seo", "score": 0.35},
        },
        "audits": {
            "is-on-https": _audit(0.0),
            "viewport": _audit(0.0),
            "document-title": _audit(0.0),
            "largest-contentful-paint": _audit(0.6, "2.4 s"),
        },
    }
}

#: A healthy site: everything passes.
GOOD_SITE_MOBILE: dict[str, Any] = {
    "lighthouseResult": {
        "requestedUrl": "https://example-good.test/",
        "finalUrl": "https://example-good.test/",
        "lighthouseVersion": "12.0.0",
        "categories": {
            "performance": {"id": "performance", "score": 0.97},
            "accessibility": {"id": "accessibility", "score": 0.95},
            "best-practices": {"id": "best-practices", "score": 1.0},
            "seo": {"id": "seo", "score": 1.0},
        },
        "audits": {
            "is-on-https": _audit(1.0),
            "viewport": _audit(1.0),
            "document-title": _audit(1.0),
            "meta-description": _audit(1.0),
            "largest-contentful-paint": _audit(0.98, "1.1 s"),
        },
    }
}

#: PSI sometimes omits categories it could not evaluate.
PARTIAL_RESULT: dict[str, Any] = {
    "lighthouseResult": {
        "requestedUrl": "https://example-partial.test/",
        "finalUrl": "https://example-partial.test/",
        "categories": {"performance": {"id": "performance", "score": 0.5}},
        "audits": {"is-on-https": _audit(1.0)},
    }
}

#: A malformed response with no lighthouseResult at all.
MALFORMED_RESULT: dict[str, Any] = {"error": {"code": 500, "message": "Internal error"}}


HTML_WITH_CONTACT_FORM = """
<!DOCTYPE html>
<html>
<head>
  <title>Congress Dental - Book an appointment</title>
  <meta name="description" content="Family dentistry in central Austin.">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="canonical" href="https://example-good.test/">
  <meta property="og:title" content="Congress Dental">
</head>
<body>
  <h1>Congress Dental</h1>
  <nav>
    <a href="/about">About</a>
    <a href="/services">Services</a>
    <a href="https://external-site.test/partner">Partner</a>
    <a href="mailto:hello@example-good.test">Email us</a>
    <a href="tel:+15125550101">Call us</a>
  </nav>
  <form action="/submit-enquiry" method="post">
    <input type="text" name="full_name" required>
    <input type="email" name="email" required>
    <textarea name="message" required></textarea>
    <button type="submit">Send</button>
  </form>
</body>
</html>
"""

#: No form, no viewport, no description, no h1 - and unclosed tags.
HTML_NO_CONTACT_FORM = """
<html>
<head><title>Poor Site</title>
<body>
  <p>Welcome to our site
  <a href="/about">About</a>
  <a href="/broken-page">Broken
  <a href="https://external.test/">External</a>
</body>
"""

#: A newsletter signup, which must NOT be mistaken for a contact form.
HTML_NEWSLETTER_ONLY = """
<html>
<head><title>Newsletter</title><meta name="viewport" content="width=device-width"></head>
<body>
  <form action="/subscribe" method="post">
    <input type="email" name="email">
    <button>Subscribe</button>
  </form>
</body>
</html>
"""

#: A search box: GET, no email field. Also not a contact form.
HTML_SEARCH_ONLY = """
<html><head><title>Search</title></head>
<body>
  <form action="/search" method="get">
    <input type="text" name="q">
  </form>
</body></html>
"""

#: Contact form posting over plain HTTP from an HTTPS page.
HTML_INSECURE_FORM = """
<html><head><title>Insecure</title></head>
<body>
  <form action="http://insecure.test/submit" method="POST">
    <input type="email" name="email">
    <textarea name="message"></textarea>
  </form>
</body></html>
"""

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
ROBOTS_PARTIAL = "User-agent: *\nDisallow: /admin\nDisallow: /private\n"
