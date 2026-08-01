"""Tests for app.services.language_detection (pure stdlib, offline).

Coverage:
* test_detect_from_html_lang_attr               — <html lang="fr"> → 'fr'
* test_detect_from_hreflang                     — <link rel="alternate" hreflang="de"> → 'de'
* test_detect_from_country_mapping               — country 'ES' or 'Spain' → 'es'
* test_detect_from_arabic_script                — Arabic text → 'ar'
* test_detect_from_cjk_script                   — CJK text → 'zh'
* test_detect_returns_none_when_no_signal        — no HTML / country → None
* test_detect_language_coroutine_with_mock_httpx — detect_language() mock fetch
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.language_detection import (
    _detect_from_country,
    _detect_from_html,
    _detect_from_script,
    detect_language,
)


def test_detect_from_html_lang_attr() -> None:
    """<html lang="fr-FR"> should be normalized to 'fr'."""
    html = '<!DOCTYPE html><html lang="fr-FR"><head><title>Test</title></head><body>Bonjour</body></html>'
    assert _detect_from_html(html) == "fr"


def test_detect_from_html_lang_attr_chinese_variants() -> None:
    """<html lang="zh-TW"> should preserve region variant 'zh-tw'."""
    html = '<html lang="zh-TW"><head><title>Test</title></head><body>繁體中文</body></html>'
    assert _detect_from_html(html) == "zh-tw"


def test_detect_from_hreflang() -> None:
    """Alternate hreflang links should be extracted when <html lang> is missing."""
    html = (
        '<html><head>'
        '<link rel="alternate" hreflang="de" href="https://example.de" />'
        '</head><body>Willkommen</body></html>'
    )
    assert _detect_from_html(html) == "de"


def test_detect_from_country_mapping() -> None:
    """Country codes and common names should map to standard BCP-47 codes."""
    assert _detect_from_country("ES") == "es"
    assert _detect_from_country("Spain") == "es"
    assert _detect_from_country("Spain (ES)") == "es"
    assert _detect_from_country("FR") == "fr"
    assert _detect_from_country("France") == "fr"
    assert _detect_from_country("Germany") == "de"
    assert _detect_from_country("Brazil") == "pt"
    assert _detect_from_country("Japan") == "ja"
    assert _detect_from_country(None) is None
    assert _detect_from_country("UnknownCountryXYZ") is None


def test_detect_from_arabic_script() -> None:
    """Text with Arabic script should detect as 'ar' via Unicode block analysis."""
    html = "<html><body><p>مرحبا بكم في موقعنا الإلكتروني</p></body></html>"
    assert _detect_from_script(html) == "ar"


def test_detect_from_cjk_script() -> None:
    """Text with CJK Unified Ideographs should detect as 'zh' via script analysis."""
    html = "<html><body><p>欢迎来到我们的网站</p></body></html>"
    assert _detect_from_script(html) == "zh"


def test_detect_returns_none_when_no_signal() -> None:
    """HTML without lang attr, hreflang, or non-ASCII script should return None."""
    html = "<html><body><p>Hello world, welcome to our site.</p></body></html>"
    assert _detect_from_html(html) is None
    assert _detect_from_script(html) is None


@pytest.mark.asyncio
async def test_detect_language_coroutine_with_mock_httpx() -> None:
    """detect_language() should combine HTTP fetch, HTML parsing, and country fallback."""
    fake_html = '<html lang="es"><head></head><body>Hola</body></html>'

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        lang = await detect_language("https://example.es", "Spain")
        assert lang == "es"


@pytest.mark.asyncio
async def test_detect_language_falls_back_to_country_on_network_error() -> None:
    """HTTP failure during website fetch should degrade gracefully to country mapping."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.side_effect = Exception("Connection refused")
        lang = await detect_language("https://offline-site.es", "Spain")
        assert lang == "es"
