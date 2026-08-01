"""Language detection service — pure stdlib, no paid API.

Detects the language a lead's business operates in using four free, offline
signals (in priority order):

1. HTML ``<html lang="...">`` attribute on the lead's website.
2. ``<link rel="alternate" hreflang="...">`` tags on the same page.
3. Hardcoded country → BCP-47 mapping derived from ``lead.country``.
4. Unicode block sampling of visible page body text (Arabic, CJK, Cyrillic,
   Devanagari, Greek, Hebrew, Thai, etc.).

The only network call is a single short-timeout (5 s) GET of the lead's
website homepage — the same page the audit agent already fetches, but
invoked here in isolation so detection can be re-run without a full audit.
Any network error silently returns ``None``; detection must never block a
lead from being created or a draft from being generated.

The public API is the single coroutine :func:`detect_language`.

See AGENTS.md section 7 (docstrings) and section 6 (logging).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Country → language mapping
# Covers the countries most commonly encountered in BD pipelines.
# Uses ISO 3166-1 alpha-2 (upper-case) → BCP-47 (lower-case).
# ---------------------------------------------------------------------------

COUNTRY_TO_LANGUAGE: dict[str, str] = {
    # English-primary
    "US": "en", "GB": "en", "AU": "en", "CA": "en", "NZ": "en",
    "IE": "en", "ZA": "en", "SG": "en", "NG": "en", "KE": "en",
    "GH": "en", "IN": "en",  # India has many languages; en is business default
    # Spanish
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "PE": "es",
    "VE": "es", "CL": "es", "EC": "es", "GT": "es", "CU": "es",
    "DO": "es", "HN": "es", "PY": "es", "SV": "es", "NI": "es",
    "BO": "es", "CR": "es", "PA": "es", "UY": "es",
    # Portuguese
    "BR": "pt", "PT": "pt", "AO": "pt", "MZ": "pt",
    # French
    "FR": "fr", "BE": "fr", "CH": "fr", "LU": "fr",
    "CI": "fr", "CM": "fr", "SN": "fr", "ML": "fr", "BF": "fr",
    "NE": "fr", "TG": "fr", "BJ": "fr", "CD": "fr", "MG": "fr",
    "HT": "fr",
    # German
    "DE": "de", "AT": "de",
    # Dutch
    "NL": "nl",
    # Italian
    "IT": "it",
    # Arabic
    "SA": "ar", "AE": "ar", "EG": "ar", "IQ": "ar", "MA": "ar",
    "DZ": "ar", "TN": "ar", "LY": "ar", "JO": "ar", "LB": "ar",
    "KW": "ar", "QA": "ar", "BH": "ar", "OM": "ar", "YE": "ar",
    "SD": "ar", "SY": "ar",
    # Chinese (Simplified)
    "CN": "zh",
    # Chinese (Traditional)
    "TW": "zh-TW", "HK": "zh-HK",
    # Japanese
    "JP": "ja",
    # Korean
    "KR": "ko",
    # Russian
    "RU": "ru",
    # Hindi
    # IN already mapped to "en" above (business default); override per override field if needed
    # Turkish
    "TR": "tr",
    # Polish
    "PL": "pl",
    # Swedish
    "SE": "sv",
    # Norwegian
    "NO": "nb",
    # Danish
    "DK": "da",
    # Finnish
    "FI": "fi",
    # Hebrew
    "IL": "he",
    # Thai
    "TH": "th",
    # Vietnamese
    "VN": "vi",
    # Indonesian / Malay
    "ID": "id", "MY": "ms",
    # Greek
    "GR": "el",
    # Czech
    "CZ": "cs",
    # Romanian
    "RO": "ro",
    # Hungarian
    "HU": "hu",
    # Ukrainian
    "UA": "uk",
}


# ---------------------------------------------------------------------------
# Language display names for LLM prompts
# ---------------------------------------------------------------------------

LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ar": "Arabic",
    "zh": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "zh-HK": "Chinese (Traditional)",
    "ja": "Japanese",
    "ko": "Korean",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "sv": "Swedish",
    "nb": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "el": "Greek",
    "cs": "Czech",
    "ro": "Romanian",
    "hu": "Hungarian",
    "uk": "Ukrainian",
}


# ---------------------------------------------------------------------------
# Unicode block ranges for script detection
# ---------------------------------------------------------------------------

# Each entry: (start_codepoint, end_codepoint, bcp47_code)
_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0600, 0x06FF, "ar"),   # Arabic
    (0x4E00, 0x9FFF, "zh"),   # CJK Unified Ideographs (also used by ja/ko, but zh is most common)
    (0x3040, 0x30FF, "ja"),   # Hiragana + Katakana → Japanese
    (0xAC00, 0xD7AF, "ko"),   # Hangul Syllables → Korean
    (0x0400, 0x04FF, "ru"),   # Cyrillic → default Russian
    (0x0900, 0x097F, "hi"),   # Devanagari → Hindi
    (0x0370, 0x03FF, "el"),   # Greek
    (0x0590, 0x05FF, "he"),   # Hebrew
    (0x0E00, 0x0E7F, "th"),   # Thai
    (0x1000, 0x109F, "my"),   # Myanmar / Burmese
]


# ---------------------------------------------------------------------------
# HTML parser for language signals
# ---------------------------------------------------------------------------


class _LangParser(HTMLParser):
    """Extracts ``lang`` attribute and ``hreflang`` link tags from HTML.

    Attributes:
        html_lang: Value of the ``<html lang="...">`` attribute, if found.
        hreflang_values: Collected values from alternate hreflang links.
        body_text: Visible text content sampled from the page body.
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        super().__init__()
        self.html_lang: str | None = None
        self.hreflang_values: list[str] = []
        self.body_text: list[str] = []
        self._in_script_or_style: bool = False
        self._capture_text: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Process opening tags.

        Args:
            tag: HTML tag name (lower-case).
            attrs: List of (name, value) attribute pairs.
        """
        attr_dict = dict(attrs)

        if tag == "html" and attr_dict.get("lang"):
            self.html_lang = (attr_dict["lang"] or "").strip().lower() or None

        if tag == "link":
            rel = (attr_dict.get("rel") or "").lower()
            hreflang = attr_dict.get("hreflang")
            if "alternate" in rel and hreflang:
                lang = hreflang.strip().lower()
                # Skip "x-default" — that's not a real language code
                if lang and lang != "x-default":
                    self.hreflang_values.append(lang)

        if tag in ("script", "style", "noscript"):
            self._in_script_or_style = True
            self._capture_text = False
        elif tag in ("p", "h1", "h2", "h3", "li", "span", "div", "a", "td"):
            self._capture_text = True

    def handle_endtag(self, tag: str) -> None:
        """Process closing tags.

        Args:
            tag: HTML tag name (lower-case).
        """
        if tag in ("script", "style", "noscript"):
            self._in_script_or_style = False

    def handle_data(self, data: str) -> None:
        """Collect visible text for script sampling.

        Args:
            data: Raw text content.
        """
        if self._capture_text and not self._in_script_or_style:
            stripped = data.strip()
            if stripped:
                self.body_text.append(stripped)


def _detect_from_html(html: str) -> str | None:
    """Parse language signals from raw HTML.

    Checks ``<html lang>`` first (most authoritative), then ``hreflang``
    alternate links (secondary), then returns ``None`` if neither is found.

    Args:
        html: Raw HTML string from the page fetch.

    Returns:
        A BCP-47 language code (lower-case, e.g. ``"fr"``), or ``None``.
    """
    parser = _LangParser()
    try:
        parser.feed(html[:200_000])  # cap at 200 KB to keep parsing fast
    except Exception:
        logger.debug("HTML lang parser raised; skipping HTML-based detection")
        return None

    if parser.html_lang and len(parser.html_lang) >= 2:
        # Normalise: "en-GB" → "en", but keep "zh-TW"/"zh-HK" variants intact
        lang = parser.html_lang
        if lang.startswith("zh"):
            return lang[:5]  # "zh-TW", "zh-HK", or just "zh"
        return lang[:2]  # strip region subtag for everything else

    # Fall back to the most common hreflang value (exclude "en" — it's
    # almost always present as a fallback; a non-en hreflang is more signal)
    non_en = [v for v in parser.hreflang_values if not v.startswith("en")]
    if non_en:
        lang = non_en[0]
        if lang.startswith("zh"):
            return lang[:5]
        return lang[:2]

    return None


def _detect_from_script(html: str) -> str | None:
    """Detect language from Unicode block distribution in visible page text.

    Samples up to 2 000 characters of visible text (skipping ASCII, which
    is ambiguous) and returns the language whose script block is most
    represented. Returns ``None`` if no non-ASCII script is dominant.

    Args:
        html: Raw HTML string from the page fetch.

    Returns:
        A BCP-47 language code, or ``None``.
    """
    parser = _LangParser()
    try:
        parser.feed(html[:200_000])
    except Exception:
        return None

    text = " ".join(parser.body_text)[:2000]
    if not text:
        return None

    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        if cp < 0x0080:
            continue  # skip ASCII
        for start, end, lang in _SCRIPT_RANGES:
            if start <= cp <= end:
                counts[lang] = counts.get(lang, 0) + 1
                break

    if not counts:
        return None

    # Only assert a language if at least 5 % of the sampled characters match
    best_lang = max(counts, key=lambda k: counts[k])
    best_count = counts[best_lang]
    total_non_ascii = sum(counts.values())
    if total_non_ascii == 0 or best_count / total_non_ascii < 0.05:
        return None

    return best_lang


def _detect_from_country(country: str | None) -> str | None:
    """Map a country name or code to a BCP-47 language.

    Args:
        country: The lead's ``country`` field value (free text, e.g.
            ``"Spain"``, ``"ES"``, or ``"Spain (ES)``). Attempts an
            upper-case two-letter code lookup; falls back to a partial
            name match for common country names.

    Returns:
        A BCP-47 code, or ``None`` if unmapped.
    """
    if not country:
        return None

    country_clean = country.strip()

    # Try direct ISO 3166-1 alpha-2 code (e.g. "ES", "FR")
    code = country_clean.upper()
    if code in COUNTRY_TO_LANGUAGE:
        return COUNTRY_TO_LANGUAGE[code]

    # Try extracting a 2-letter code from strings like "Spain (ES)"
    m = re.search(r"\(([A-Z]{2})\)", country_clean)
    if m and m.group(1) in COUNTRY_TO_LANGUAGE:
        return COUNTRY_TO_LANGUAGE[m.group(1)]

    # Case-insensitive name lookup for common country names
    _NAME_MAP: dict[str, str] = {
        "spain": "es", "mexico": "es", "france": "fr", "germany": "de",
        "germany": "de", "brazil": "pt", "portugal": "pt",
        "united states": "en", "united kingdom": "en",
        "australia": "en", "canada": "en",
        "japan": "ja", "china": "zh", "south korea": "ko",
        "russia": "ru", "india": "hi", "argentina": "es",
        "colombia": "es", "chile": "es", "peru": "es",
        "italy": "it", "netherlands": "nl", "poland": "pl",
        "ukraine": "uk", "turkey": "tr", "saudi arabia": "ar",
        "united arab emirates": "ar", "egypt": "ar",
    }
    return _NAME_MAP.get(country_clean.lower())


async def detect_language(
    website: str | None,
    country: str | None,
) -> str | None:
    """Detect the language of a lead's business from available signals.

    Runs four heuristics in priority order and returns the first confident
    result. Never raises — any error returns ``None`` so callers degrade
    gracefully to English.

    Args:
        website: The lead's website URL, or ``None`` if unavailable.
        country: The lead's country field value, or ``None``.

    Returns:
        A BCP-47 language code (e.g. ``"es"``, ``"fr"``), or ``None`` if
        no confident signal was found. ``None`` means the draft generator
        will use English as the safe default.
    """
    html: str | None = None

    # --- Fetch website HTML (if URL is available) ---------------------------
    if website:
        try:
            import httpx  # noqa: PLC0415 — optional import; httpx is already in requirements

            async with httpx.AsyncClient(
                timeout=5.0,
                follow_redirects=True,
                headers={"User-Agent": "EverenBD-Bot/1.0 (language detection)"},
            ) as client:
                resp = await client.get(str(website))
                if resp.status_code < 400:
                    html = resp.text
                else:
                    logger.debug(
                        "Language detection: website returned %s, skipping HTML parse",
                        resp.status_code,
                        extra={"website": website},
                    )
        except Exception:
            logger.debug(
                "Language detection: HTTP fetch failed for website, skipping",
                exc_info=True,
                extra={"website": website},
            )

    # --- Priority 1: HTML lang attribute / hreflang -------------------------
    if html:
        lang = _detect_from_html(html)
        if lang:
            logger.info(
                "Language detected from HTML",
                extra={"language": lang, "website": website},
            )
            return lang

    # --- Priority 2: Country → language mapping ----------------------------
    country_lang = _detect_from_country(country)
    if country_lang:
        logger.info(
            "Language detected from country mapping",
            extra={"language": country_lang, "country": country},
        )
        return country_lang

    # --- Priority 3: Unicode script heuristic (on page body text) ----------
    if html:
        script_lang = _detect_from_script(html)
        if script_lang:
            logger.info(
                "Language detected from Unicode script heuristic",
                extra={"language": script_lang, "website": website},
            )
            return script_lang

    logger.debug(
        "Language detection: no confident signal found",
        extra={"website": website, "country": country},
    )
    return None
