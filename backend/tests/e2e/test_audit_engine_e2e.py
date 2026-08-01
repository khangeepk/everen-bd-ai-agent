"""E2E tests: the website audit engine (POST /api/v1/audits).

The real agent talks to PageSpeed Insights, opens a TLS socket for the SSL
check, and crawls the target site -- all forbidden in tests (AGENTS.md section
11). So PageSpeed and the crawler are injected as fakes through the
``get_audit_agent`` dependency, and the free functions ``check_ssl`` /
``check_contact_form`` (imported directly into ``app.agents.auditor``, not
behind an injectable dependency) are monkeypatched at that import site.

Report generation always takes its deterministic fallback in this
environment: ``WebsiteAuditAgent.generate_report`` imports the OpenAI SDK
lazily inside a try/except and the SDK is not installed here, so every
assertion about the produced report treats ``used_fallback`` as True by
design, not as something to work around.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import Depends
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.agents.auditor as auditor_module
from app.agents.auditor import WebsiteAuditAgent
from app.api.v1.audits import get_audit_agent
from app.db.session import get_db
from app.main import app
from app.services.knowledge_base import KnowledgeBaseService
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.pagespeed_parsing import PageSpeedError, Strategy, parse_pagespeed
from app.services.site_checks import ContactFormStatus, CrawlResult, SiteCheckError, SSLStatus
from tests.sample_audit_data import GOOD_SITE_MOBILE, POOR_SITE_DESKTOP, POOR_SITE_MOBILE

pytestmark = pytest.mark.asyncio

GOOD_RESULT = parse_pagespeed(GOOD_SITE_MOBILE, Strategy.MOBILE)
POOR_MOBILE_RESULT = parse_pagespeed(POOR_SITE_MOBILE, Strategy.MOBILE)
POOR_DESKTOP_RESULT = parse_pagespeed(POOR_SITE_DESKTOP, Strategy.DESKTOP)

VALID_SSL = SSLStatus(supports_https=True, valid=True, issuer="Let's Encrypt")
VALID_CONTACT_FORM = ContactFormStatus(
    form_found=True, endpoint="/submit-enquiry", endpoint_reachable=True, posts_over_https=True
)
GOOD_CRAWL = CrawlResult(pages_crawled=3, links_checked=10, broken_links=[], home_page=object())


@dataclass
class FakePageSpeedClient:
    """Scripted PageSpeed client.

    Attributes:
        by_strategy: Result to return for each strategy.
        error: If set, raised for every strategy instead of returning a result.
    """

    by_strategy: dict[Strategy, object] = field(default_factory=dict)
    error: PageSpeedError | None = None

    async def run(self, url: str, strategy: Strategy = Strategy.MOBILE):
        """Return the scripted result for a strategy, or raise.

        Args:
            url: Ignored by the fake.
            strategy: Which scripted result to return.

        Returns:
            The scripted :class:`PageSpeedResult`.

        Raises:
            PageSpeedError: If one was configured.
        """
        if self.error is not None:
            raise self.error
        return self.by_strategy[strategy]


@dataclass
class FakeCrawler:
    """Scripted site crawler.

    Attributes:
        result: Crawl result to return.
        error: If set, raised instead of returning ``result``.
    """

    result: CrawlResult | None = None
    error: SiteCheckError | None = None

    async def crawl(self, start_url: str) -> CrawlResult:
        """Return the scripted crawl result, or raise.

        Args:
            start_url: Ignored by the fake.

        Returns:
            The scripted :class:`CrawlResult`.

        Raises:
            SiteCheckError: If one was configured.
        """
        if self.error is not None:
            raise self.error
        return self.result


def _install_fake_audit_agent(
    monkeypatch: pytest.MonkeyPatch,
    pagespeed: FakePageSpeedClient,
    crawler: FakeCrawler,
    *,
    ssl: SSLStatus | Exception = VALID_SSL,
) -> None:
    """Override the audit-agent dependency with fakes wired in.

    ``check_ssl`` and ``check_contact_form`` are imported directly into
    ``app.agents.auditor`` (not passed to the agent's constructor), so they
    are patched at that import site via ``monkeypatch`` rather than the
    constructor -- and automatically restored when the test ends.

    Args:
        monkeypatch: Pytest's monkeypatch fixture, for automatic teardown.
        pagespeed: The scripted PageSpeed client.
        crawler: The scripted crawler.
        ssl: An :class:`SSLStatus` to return from the patched ``check_ssl``,
            or an exception instance to raise instead.
    """

    async def _fake_check_ssl(hostname: str, port: int = 443, timeout: float = 10.0):
        if isinstance(ssl, Exception):
            raise ssl
        return ssl

    async def _fake_check_contact_form(home_page, base_url, *, probe_endpoint: bool = True):
        return VALID_CONTACT_FORM

    monkeypatch.setattr(auditor_module, "check_ssl", _fake_check_ssl)
    monkeypatch.setattr(auditor_module, "check_contact_form", _fake_check_contact_form)

    async def _override(db: AsyncSession = Depends(get_db)) -> WebsiteAuditAgent:
        kb = KnowledgeBaseService(db=db, embedder=OpenAIEmbeddingClient())
        return WebsiteAuditAgent(db=db, kb=kb, pagespeed=pagespeed, crawler=crawler)

    app.dependency_overrides[get_audit_agent] = _override


async def test_audit_happy_path_produces_completed_report(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean run against a healthy site completes with a fallback report."""
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(
            by_strategy={Strategy.MOBILE: GOOD_RESULT, Strategy.DESKTOP: GOOD_RESULT}
        ),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "https://good-site.example", "include_social": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["audit"]["status"] == "completed"
    assert body["audit"]["ssl_valid"] is True
    assert body["audit"]["contact_form_found"] is True
    assert body["report"]["used_fallback"] is True
    assert body["report"]["headline"]

    fetched = await e2e_client.get(f"/api/v1/audits/{body['audit']['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["audit"]["id"] == body["audit"]["id"]


async def test_audit_poor_site_produces_findings_mapped_to_health_score(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site with real problems yields findings and a depressed health score."""
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(
            by_strategy={Strategy.MOBILE: POOR_MOBILE_RESULT, Strategy.DESKTOP: POOR_DESKTOP_RESULT}
        ),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "https://poor-site.example", "include_social": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["findings"]) > 0
    assert body["audit"]["health_score"] < 0.6


async def test_pagespeed_api_failure_does_not_fail_the_whole_audit(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PageSpeed outages are non-fatal: recorded as errors, audit still completes.

    Covers the "API failure" edge case for this phase. Both the mobile and
    desktop PageSpeed calls fail here; SSL and the crawl still succeed, so the
    audit reaches COMPLETED with a lower-confidence health score rather than
    aborting the whole request.
    """
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(error=PageSpeedError("PageSpeed API error 500: internal error")),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "https://flaky-pagespeed.example", "include_social": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["audit"]["status"] == "completed"
    assert body["audit"]["performance_score"] is None
    assert "PageSpeed" in (body["audit"]["error_detail"] or "")


async def test_pagespeed_rate_limit_hit_is_recorded_as_a_non_fatal_error(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 from PageSpeed is handled identically to any other PageSpeedError.

    Covers the "rate-limit hit" edge case: PSI's PageSpeedError branch in
    app.agents.auditor.WebsiteAuditAgent.run_audit does not distinguish status
    codes, so a 429 degrades the audit the same way a 500 does -- gracefully.
    """
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(error=PageSpeedError("PageSpeed API error 429: RESOURCE_EXHAUSTED")),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "https://rate-limited.example", "include_social": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["audit"]["status"] == "completed"
    assert "429" in (body["audit"]["error_detail"] or "") or "RESOURCE_EXHAUSTED" in (
        body["audit"]["error_detail"] or ""
    )


async def test_crawl_failure_is_recorded_but_audit_still_completes(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken crawl (robots fetch failure, DNS failure, etc.) degrades gracefully."""
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(
            by_strategy={Strategy.MOBILE: GOOD_RESULT, Strategy.DESKTOP: GOOD_RESULT}
        ),
        FakeCrawler(error=SiteCheckError("Could not resolve host")),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "https://unreachable-crawl.example", "include_social": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["audit"]["status"] == "completed"
    assert body["audit"]["contact_form_found"] is None, "no home page fetched, so no form check ran"
    assert "Crawl failed" in (body["audit"]["error_detail"] or "")


async def test_invalid_url_is_rejected_before_any_check_runs(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-http(s) or relative URL is a 400, never reaches the network layer."""
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(
            by_strategy={Strategy.MOBILE: GOOD_RESULT, Strategy.DESKTOP: GOOD_RESULT}
        ),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits", json={"url": "ftp://not-a-website.example", "include_social": False}
    )

    assert response.status_code in (400, 422)  # 422 if pydantic's HttpUrl rejects it first


async def test_audit_for_missing_lead_id_is_404(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Referencing a lead_id that does not exist is rejected before auditing."""
    _install_fake_audit_agent(
        monkeypatch,
        FakePageSpeedClient(
            by_strategy={Strategy.MOBILE: GOOD_RESULT, Strategy.DESKTOP: GOOD_RESULT}
        ),
        FakeCrawler(result=GOOD_CRAWL),
    )

    response = await e2e_client.post(
        "/api/v1/audits",
        json={
            "url": "https://good-site.example",
            "lead_id": "00000000-0000-0000-0000-000000000000",
            "include_social": False,
        },
    )

    assert response.status_code == 404
