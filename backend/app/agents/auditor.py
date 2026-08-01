"""Website audit orchestration and business-friendly report generation.

Runs the website checks, folds in the human social review, maps findings onto
Everen Techno services via the existing knowledge base, and asks the LLM for a
plain-language report.

Two constraints shape the design:

* The report is grounded strictly in actual findings. The LLM is given the
  findings and the retrieved services and told not to invent either. A
  deterministic fallback report is produced if the LLM is unavailable, so an
  outage degrades wording rather than inventing facts.
* A report is a document, not outreach. Nothing here sends anything. Turning a
  report into an email goes through the pending_review / approved gate in
  AGENTS.md section 8.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.knowledge_base import Service
from app.services.audit_scoring import (
    Finding,
    FindingCategory,
    Severity,
    count_by_severity,
    deduplicate,
    grade,
    health_score,
    prioritize,
)
from app.services.cost_guard import BudgetExceededError, CostProvider, estimate_openai_cost
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.knowledge_base import KnowledgeBaseService
from app.services.pagespeed import PageSpeedClient
from app.services.pagespeed_parsing import (
    PageSpeedError,
    Strategy,
    findings_from_pagespeed,
)
from app.services.site_checks import (
    ContactFormStatus,
    CrawlResult,
    SiteCheckError,
    SiteCrawler,
    SSLStatus,
    check_contact_form,
    check_ssl,
    findings_from_contact_form,
    findings_from_crawl,
    findings_from_ssl,
)
from app.services.social_review import ProfileChecklist, build_findings, score_presence

logger = logging.getLogger(__name__)

AGENT_NAME = "website-audit-agent-v1"

#: Finding category -> the service-search phrasing used to find a matching
#: Everen Techno service in the knowledge base.
_CATEGORY_QUERIES: dict[FindingCategory, str] = {
    FindingCategory.PERFORMANCE: "website performance optimisation, page speed, slow loading site",
    FindingCategory.SEO: "search engine optimisation, SEO, search visibility, meta tags",
    FindingCategory.ACCESSIBILITY: "web accessibility, WCAG compliance, screen reader support",
    FindingCategory.SECURITY: "website security, SSL certificates, HTTPS, secure hosting",
    FindingCategory.MOBILE: "mobile responsive design, mobile-friendly website, responsive rebuild",
    FindingCategory.BROKEN_LINKS: "website maintenance, support retainer, site health monitoring",
    FindingCategory.CONTACT_FORM: "website development, contact forms, lead capture, conversion",
    FindingCategory.SOCIAL: "social media presence, digital marketing, brand profile setup",
    FindingCategory.BEST_PRACTICES: "website modernisation, technical health, code quality",
}

_SYSTEM_PROMPT = """You are writing a website review for a small business owner \
on behalf of Everen Techno.

You will receive a list of concrete findings from an automated audit, and a list \
of Everen Techno services that may address them.

Rules:
- Use ONLY the findings supplied. Never invent a problem, a score, or a statistic.
- Recommend ONLY services from the supplied list. Never invent a service or a price.
- Write for a business owner, not a developer. No jargon: say "your site takes 6 \
seconds to load on a phone", not "LCP is 6.1s".
- Lead with what costs them customers. Be direct but not alarmist, and never \
imply the business is failing.
- If the audit found little wrong, say so plainly rather than manufacturing concern.
- Structure: a two-sentence summary, then the findings worth acting on, then how \
Everen Techno can help.
"""


@dataclass
class AuditOutcome:
    """Everything one audit run produced.

    Attributes:
        url: The audited URL.
        findings: Deduplicated, prioritized findings.
        category_scores: Score per category where one exists.
        health: Overall weighted health score.
        ssl: TLS check result.
        contact_form: Contact form status.
        crawl: Crawl outcome.
        social_score: Social presence score, when a review was supplied.
        errors: Non-fatal failures, e.g. PageSpeed timing out.
    """

    url: str
    findings: list[Finding] = field(default_factory=list)
    category_scores: dict[FindingCategory, float] = field(default_factory=dict)
    health: float = 0.0
    ssl: SSLStatus | None = None
    contact_form: ContactFormStatus | None = None
    crawl: CrawlResult | None = None
    social_score: float | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedReport:
    """A business-friendly audit report.

    Attributes:
        headline: One-line summary suitable for a subject line or title.
        summary: Two-sentence plain-language overview.
        body_markdown: The full report.
        recommended_service_ids: Services referenced, in priority order.
        used_fallback: True when the LLM was unavailable and the deterministic
            report was used instead.
    """

    headline: str
    summary: str
    body_markdown: str
    recommended_service_ids: tuple[uuid.UUID, ...]
    used_fallback: bool


class WebsiteAuditAgent:
    """Runs website checks and produces a grounded, business-readable report."""

    def __init__(
        self,
        db: AsyncSession,
        kb: KnowledgeBaseService,
        pagespeed: PageSpeedClient | None = None,
        crawler: SiteCrawler | None = None,
    ) -> None:
        """Initialize the agent.

        Args:
            db: Active database session.
            kb: Knowledge base service, used to map findings to services.
            pagespeed: PageSpeed client. Defaults to a configured one.
            crawler: Site crawler. Defaults to one bounded by settings.
        """
        self._db = db
        self._kb = kb
        self._pagespeed = pagespeed or PageSpeedClient()
        self._crawler = crawler or SiteCrawler(
            max_pages=settings.audit_crawl_max_pages,
            max_depth=settings.audit_crawl_max_depth,
            delay_seconds=settings.audit_crawl_delay_seconds,
        )

    async def run_audit(
        self, url: str, social_checklists: Sequence[ProfileChecklist] | None = None
    ) -> AuditOutcome:
        """Run every check against a site and assemble the findings.

        Individual check failures are recorded in ``errors`` rather than
        aborting -- a partial audit is more useful than none, and a site being
        down is itself a finding.

        Args:
            url: Absolute URL of the site to audit.
            social_checklists: Optional human social reviews to fold in.

        Returns:
            The assembled :class:`AuditOutcome`.

        Raises:
            ValueError: If ``url`` is not an absolute http(s) URL.
        """
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"{url!r} is not an absolute http(s) URL")

        outcome = AuditOutcome(url=url)
        raw_findings: list[Finding] = []

        # PageSpeed: mobile first, since it drives the mobile-friendliness view.
        for strategy in (Strategy.MOBILE, Strategy.DESKTOP):
            try:
                result = await self._pagespeed.run(url, strategy)
            except PageSpeedError as exc:
                outcome.errors.append(f"PageSpeed ({strategy.value}) failed: {exc}")
                logger.warning("PageSpeed failed", extra={"url": url, "strategy": strategy.value})
                continue

            raw_findings.extend(findings_from_pagespeed(result))
            for category in (
                FindingCategory.PERFORMANCE,
                FindingCategory.SEO,
                FindingCategory.ACCESSIBILITY,
                FindingCategory.BEST_PRACTICES,
            ):
                score = result.score_for(category)
                if score is None:
                    continue
                # Mobile scores win: most visitors are on phones.
                if strategy is Strategy.MOBILE or category not in outcome.category_scores:
                    outcome.category_scores[category] = score

            if strategy is Strategy.MOBILE:
                mobile = result.category_scores.get("performance")
                if mobile is not None:
                    outcome.category_scores[FindingCategory.MOBILE] = mobile

        # SSL.
        try:
            outcome.ssl = await check_ssl(parts.hostname or "")
            raw_findings.extend(findings_from_ssl(outcome.ssl))
            outcome.category_scores[FindingCategory.SECURITY] = (
                1.0 if outcome.ssl.valid else 0.0
            )
        except Exception as exc:
            outcome.errors.append(f"SSL check failed: {exc}")
            logger.exception("SSL check failed", extra={"url": url})

        # Bounded crawl for broken links, which also gives us the home page.
        try:
            outcome.crawl = await self._crawler.crawl(url)
            raw_findings.extend(findings_from_crawl(outcome.crawl))
        except SiteCheckError as exc:
            outcome.errors.append(f"Crawl failed: {exc}")
            logger.warning("Crawl failed", extra={"url": url})

        # Contact form, from the home page the crawl already fetched.
        if outcome.crawl is not None and outcome.crawl.home_page is not None:
            try:
                outcome.contact_form = await check_contact_form(
                    outcome.crawl.home_page, url, probe_endpoint=True
                )
                raw_findings.extend(findings_from_contact_form(outcome.contact_form))
                outcome.category_scores[FindingCategory.CONTACT_FORM] = (
                    1.0 if outcome.contact_form.form_found else 0.0
                )
            except Exception as exc:
                outcome.errors.append(f"Contact form check failed: {exc}")
                logger.exception("Contact form check failed", extra={"url": url})

        # Social review, if a human supplied one.
        if social_checklists:
            raw_findings.extend(build_findings(social_checklists))
            outcome.social_score = score_presence(social_checklists)

        outcome.findings = deduplicate(raw_findings)
        outcome.health = health_score(outcome.category_scores)

        logger.info(
            "Audit complete",
            extra={
                "url": url,
                "findings": len(outcome.findings),
                "health": outcome.health,
                "errors": len(outcome.errors),
            },
        )
        return outcome

    async def map_findings_to_services(
        self, findings: Sequence[Finding], top_k: int = 3
    ) -> dict[FindingCategory, list[Service]]:
        """Find services addressing each finding category.

        Args:
            findings: The audit findings.
            top_k: Maximum services per category.

        Returns:
            A mapping of category to matching services, best first.
        """
        categories = {finding.category for finding in findings}
        mapping: dict[FindingCategory, list[Service]] = {}

        for category in categories:
            query = _CATEGORY_QUERIES.get(category)
            if query is None:
                continue

            chunks = await self._kb.search(query, top_k=top_k * 3)
            scored = KnowledgeBaseService.collapse_to_services(chunks)[:top_k]
            if not scored:
                continue

            rows = (
                (
                    await self._db.execute(
                        select(Service).where(Service.id.in_([entry.item for entry in scored]))
                    )
                )
                .scalars()
                .all()
            )
            by_id = {service.id: service for service in rows}
            mapping[category] = [
                by_id[entry.item] for entry in scored if entry.item in by_id
            ]

        logger.info("Mapped findings to services", extra={"categories": len(mapping)})
        return mapping

    def build_fallback_report(
        self, outcome: AuditOutcome, services: dict[FindingCategory, list[Service]]
    ) -> GeneratedReport:
        """Build a deterministic report without calling the LLM.

        Used when the LLM is unavailable. Wording is plainer than the generated
        version, but every fact comes from the same findings, so the report is
        never wrong -- only less polished.

        Args:
            outcome: The audit outcome.
            services: Services mapped per category.

        Returns:
            The deterministic report.
        """
        counts = count_by_severity(outcome.findings)
        urgent = counts[Severity.CRITICAL] + counts[Severity.HIGH]
        overall = grade(outcome.health)

        if urgent:
            headline = (
                f"{urgent} issue{'s' if urgent != 1 else ''} on your website "
                "needs attention"
            )
        elif outcome.findings:
            headline = "Your website is in reasonable shape, with room to improve"
        else:
            headline = "Your website passed every check we ran"

        summary = (
            f"We reviewed {outcome.url} and gave it an overall grade of {overall}. "
            + (
                f"We found {len(outcome.findings)} thing"
                f"{'s' if len(outcome.findings) != 1 else ''} worth looking at, "
                f"{urgent} of which we would treat as urgent."
                if outcome.findings
                else "We did not find anything that needs fixing."
            )
        )

        lines = [f"# Website review: {outcome.url}", "", summary, ""]

        if outcome.findings:
            lines.append("## What we found")
            lines.append("")
            for finding in prioritize(outcome.findings, limit=10):
                lines.append(f"### {finding.title}")
                lines.append("")
                lines.append(f"*Priority: {finding.severity.value}*")
                lines.append("")
                lines.append(finding.detail)
                if finding.evidence:
                    lines.append("")
                    for item in finding.evidence[:5]:
                        lines.append(f"- {item}")
                lines.append("")

        if services:
            lines.append("## How we can help")
            lines.append("")
            seen: set[uuid.UUID] = set()
            for category, matches in services.items():
                for service in matches:
                    if service.id in seen:
                        continue
                    seen.add(service.id)
                    lines.append(
                        f"- **{service.name}** ({service.price_range_label()}) — "
                        f"{service.summary}"
                    )
            lines.append("")

        if outcome.errors:
            lines.append("## Checks we could not complete")
            lines.append("")
            lines.extend(f"- {error}" for error in outcome.errors)

        ordered_ids = tuple(
            dict.fromkeys(
                service.id for matches in services.values() for service in matches
            )
        )

        return GeneratedReport(
            headline=headline,
            summary=summary,
            body_markdown="\n".join(lines),
            recommended_service_ids=ordered_ids,
            used_fallback=True,
        )

    async def generate_report(
        self, outcome: AuditOutcome, services: dict[FindingCategory, list[Service]]
    ) -> GeneratedReport:
        """Generate the business-friendly report, preferring the LLM.

        Args:
            outcome: The audit outcome.
            services: Services mapped per category.

        Returns:
            The report. Falls back to :meth:`build_fallback_report` on any LLM
            failure.
        """
        fallback = self.build_fallback_report(outcome, services)

        findings_block = "\n".join(
            f"- [{finding.severity.value}] {finding.title}: {finding.detail}"
            for finding in prioritize(outcome.findings, limit=15)
        ) or "(no issues found)"

        services_block = "\n".join(
            f"- {service.name} ({service.price_range_label()}): {service.summary}"
            for matches in services.values()
            for service in matches
        ) or "(no matching services)"

        try:
            await enforce_budget_before_call(
                self._db, CostProvider.OPENAI, settings.cost_guard_daily_budget_openai_usd
            )

            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model=settings.recommendation_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Website: {outcome.url}\n"
                            f"Overall grade: {grade(outcome.health)}\n\n"
                            f"Findings:\n{findings_block}\n\n"
                            f"Available Everen Techno services:\n{services_block}\n\n"
                            "Write the review in Markdown."
                        ),
                    },
                ],
                temperature=0.3,
            )
            body = (response.choices[0].message.content or "").strip()

            usage = getattr(response, "usage", None)
            cost = estimate_openai_cost(
                settings.recommendation_model,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
            await record_spend(
                self._db,
                CostProvider.OPENAI,
                "auditor.generate_report",
                cost,
                daily_budget_usd=settings.cost_guard_daily_budget_openai_usd,
            )
        except BudgetExceededError:
            logger.warning("LLM report generation skipped: daily OpenAI budget exhausted")
            return fallback
        except Exception:
            logger.exception("LLM report generation failed; using deterministic fallback")
            return fallback

        if not body:
            logger.warning("LLM returned an empty report; using deterministic fallback")
            return fallback

        logger.info(
            "Report generated", extra={"agent": AGENT_NAME, "chars": len(body)}
        )
        return GeneratedReport(
            headline=fallback.headline,
            summary=fallback.summary,
            body_markdown=body,
            recommended_service_ids=fallback.recommended_service_ids,
            used_fallback=False,
        )
