"""Seed the Services Knowledge Base with Everen Techno content.

PLACEHOLDER CONTENT. The services, pricing ranges, and portfolio write-ups
below are plausible stand-ins so the RAG pipeline is exercisable end-to-end.
Replace them with real Everen Techno material before any client-facing use --
the recommender quotes these prices verbatim.

Run with::

    python -m app.db.seed
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.models.knowledge_base import PortfolioItem, PricingModel, Service
from app.db.session import SessionFactory
from app.services.embeddings import OpenAIEmbeddingClient
from app.services.knowledge_base import KnowledgeBaseService

logger = logging.getLogger(__name__)

SEED_SERVICES: list[dict] = [
    {
        "name": "Custom Web Application Development",
        "slug": "custom-web-application-development",
        "category": "Software Engineering",
        "summary": (
            "End-to-end design and build of bespoke web applications on a modern "
            "TypeScript and Python stack."
        ),
        "description": (
            "We design, build, and ship production web applications tailored to a "
            "client's operating model rather than forcing their process into an "
            "off-the-shelf tool.\n\n"
            "Typical engagements cover discovery and technical design, UI/UX design, "
            "frontend implementation in React or Next.js with strict TypeScript, a "
            "Python FastAPI backend, PostgreSQL data modelling, automated testing, "
            "and CI/CD setup on GitHub Actions.\n\n"
            "We work in two-week iterations with a demo at the end of each. Clients "
            "own the source code outright from day one. Post-launch we offer a "
            "warranty period followed by an optional support retainer."
        ),
        "price_min": Decimal("25000.00"),
        "price_max": Decimal("120000.00"),
        "pricing_model": PricingModel.PROJECT_RANGE,
        "typical_duration_weeks": 16,
    },
    {
        "name": "AI Agent & LLM Integration",
        "slug": "ai-agent-llm-integration",
        "category": "Artificial Intelligence",
        "summary": (
            "Retrieval-augmented assistants and workflow agents built on OpenAI or "
            "Anthropic models, with human review built in."
        ),
        "description": (
            "We build LLM-powered features that hold up in production: retrieval-"
            "augmented question answering over internal documents, drafting "
            "assistants, classification and routing pipelines, and multi-step agents "
            "that call internal APIs.\n\n"
            "Our standard architecture uses PostgreSQL with pgvector for embeddings, "
            "hybrid keyword-plus-vector retrieval, explicit grounding so answers cite "
            "source documents, and evaluation harnesses that catch regressions before "
            "release.\n\n"
            "Every agent we deliver that touches external communication is built "
            "draft-first with a mandatory human approval step. We do not ship "
            "systems that message a client's customers autonomously.\n\n"
            "Engagements usually start with a four-week paid discovery and prototype "
            "phase so the business case is proven before committing to a full build."
        ),
        "price_min": Decimal("35000.00"),
        "price_max": Decimal("180000.00"),
        "pricing_model": PricingModel.PROJECT_RANGE,
        "typical_duration_weeks": 20,
    },
    {
        "name": "Data Platform & Analytics Engineering",
        "slug": "data-platform-analytics-engineering",
        "category": "Data Engineering",
        "summary": (
            "Warehouse design, ELT pipelines, and self-serve dashboards that give "
            "leadership one trustworthy set of numbers."
        ),
        "description": (
            "We consolidate fragmented operational data into a governed warehouse and "
            "build the transformation layer on top of it.\n\n"
            "Scope typically includes source system audit, warehouse design on "
            "PostgreSQL, ELT pipelines, dimensional modelling, data quality tests that "
            "run on every pipeline execution, and BI dashboards.\n\n"
            "We emphasise lineage and testing: every metric traces back to a source "
            "column, and every model carries assertions that fail loudly rather than "
            "silently producing wrong numbers."
        ),
        "price_min": Decimal("20000.00"),
        "price_max": Decimal("90000.00"),
        "pricing_model": PricingModel.PROJECT_RANGE,
        "typical_duration_weeks": 12,
    },
    {
        "name": "Cloud Infrastructure & DevOps",
        "slug": "cloud-infrastructure-devops",
        "category": "Infrastructure",
        "summary": (
            "Infrastructure-as-code, container orchestration, and CI/CD for teams "
            "outgrowing manual deployment."
        ),
        "description": (
            "We move teams from hand-managed servers to reproducible, version-"
            "controlled infrastructure.\n\n"
            "Work covers Terraform modules for AWS or GCP, Docker containerisation, "
            "orchestration on ECS or Kubernetes, GitHub Actions pipelines with staged "
            "rollouts, centralised logging and metrics, alerting, and runbooks.\n\n"
            "We also run cost reviews; most clients see meaningful monthly savings "
            "from rightsizing and scheduled scale-down of non-production environments."
        ),
        "price_min": Decimal("15000.00"),
        "price_max": Decimal("70000.00"),
        "pricing_model": PricingModel.PROJECT_RANGE,
        "typical_duration_weeks": 10,
    },
    {
        "name": "Technical Due Diligence",
        "slug": "technical-due-diligence",
        "category": "Advisory",
        "summary": (
            "Independent assessment of a target company's codebase, architecture, "
            "team, and technical risk for investors and acquirers."
        ),
        "description": (
            "A structured review producing an investment-grade report: architecture "
            "and scalability assessment, code quality and test coverage analysis, "
            "security and compliance posture, third-party dependency and licence "
            "risk, infrastructure cost trajectory, team capability and key-person "
            "risk, and a remediation roadmap with costed effort estimates.\n\n"
            "Delivered in two to four weeks depending on codebase size. Fixed fee, "
            "quoted after a short scoping call."
        ),
        "price_min": Decimal("12000.00"),
        "price_max": Decimal("45000.00"),
        "pricing_model": PricingModel.FIXED,
        "typical_duration_weeks": 3,
    },
    {
        "name": "Managed Engineering Retainer",
        "slug": "managed-engineering-retainer",
        "category": "Support",
        "summary": (
            "An ongoing embedded engineering team with a fixed monthly capacity and "
            "a defined response SLA."
        ),
        "description": (
            "For clients who need continuous capacity rather than a fixed-scope "
            "project. A dedicated pod handles feature work, maintenance, dependency "
            "upgrades, and incident response.\n\n"
            "Retainers are sold in blocks of engineering days per month with a "
            "documented SLA: four business hours for critical incidents, two business "
            "days for standard requests. Unused capacity rolls over for one month.\n\n"
            "Minimum commitment is three months. Most clients transition onto a "
            "retainer after a project engagement concludes."
        ),
        "price_min": Decimal("8000.00"),
        "price_max": Decimal("40000.00"),
        "pricing_model": PricingModel.MONTHLY_RETAINER,
        "typical_duration_weeks": None,
    },
]

SEED_PORTFOLIO: list[dict] = [
    {
        "service_slug": "ai-agent-llm-integration",
        "client_name": "Meridian Logistics",
        "industry": "Freight & Logistics",
        "title": "RAG assistant over 40,000 carrier contracts",
        "body": (
            "Meridian's operations team spent hours per week hunting through scanned "
            "carrier contracts to answer questions about liability caps and fuel "
            "surcharge clauses.\n\n"
            "We built an ingestion pipeline that OCR'd the archive, chunked each "
            "contract by clause, embedded the chunks into PostgreSQL with pgvector, "
            "and exposed a search interface that answers questions with citations "
            "linking back to the exact clause and page."
        ),
        "outcome": (
            "Average contract lookup dropped from roughly 25 minutes to under two "
            "minutes. Adopted by 60 operations staff within the first quarter."
        ),
    },
    {
        "service_slug": "custom-web-application-development",
        "client_name": "Northbridge Property Group",
        "industry": "Real Estate",
        "title": "Tenant lifecycle platform replacing six spreadsheets",
        "body": (
            "Northbridge managed 1,200 tenancies across a set of shared spreadsheets "
            "and an email inbox, with no single view of arrears or maintenance status."
            "\n\n"
            "We delivered a Next.js and FastAPI platform covering tenancy records, "
            "rent scheduling, arrears tracking, maintenance ticketing with contractor "
            "assignment, and a document vault, migrating all historical data across."
        ),
        "outcome": (
            "Arrears reporting moved from a two-day manual exercise to a live "
            "dashboard. Maintenance resolution time fell 34% in six months."
        ),
    },
    {
        "service_slug": "data-platform-analytics-engineering",
        "client_name": "Calder Health",
        "industry": "Healthcare",
        "title": "Unified clinical and financial reporting warehouse",
        "body": (
            "Calder ran nine clinics on three different practice management systems, "
            "so group-level reporting was assembled by hand each month.\n\n"
            "We built an ELT pipeline into a governed PostgreSQL warehouse with a "
            "dimensional model covering appointments, billing, and clinician "
            "utilisation, plus data quality assertions on every load."
        ),
        "outcome": (
            "Monthly board pack preparation went from nine working days to "
            "under one. Identified $340k of annually under-billed procedures."
        ),
    },
    {
        "service_slug": "technical-due-diligence",
        "client_name": "Harlow Capital",
        "industry": "Private Equity",
        "title": "Pre-acquisition diligence on a vertical SaaS target",
        "body": (
            "Harlow engaged us ahead of a majority stake acquisition in a scheduling "
            "SaaS business.\n\n"
            "Our review covered a 400,000-line codebase, the deployment topology, "
            "customer data handling against GDPR obligations, and the engineering "
            "team's structure and retention risk."
        ),
        "outcome": (
            "We identified an undocumented single-tenant deployment model that "
            "materially changed the scaling cost model. Findings informed a "
            "renegotiated price and a funded 12-month remediation plan."
        ),
    },
]


async def seed(*, embed: bool = True) -> None:
    """Insert seed content and optionally build its embeddings.

    Idempotent on service slug -- rerunning skips services that already exist.

    Args:
        embed: When False, rows are inserted without calling the embeddings
            API. Useful for local setup without an OpenAI key.
    """
    async with SessionFactory() as session:
        kb = KnowledgeBaseService(db=session, embedder=OpenAIEmbeddingClient())
        by_slug: dict[str, Service] = {}

        for payload in SEED_SERVICES:
            existing = (
                await session.execute(select(Service).where(Service.slug == payload["slug"]))
            ).scalar_one_or_none()
            if existing is not None:
                by_slug[payload["slug"]] = existing
                logger.info("Service already present", extra={"slug": payload["slug"]})
                continue

            service = Service(**payload)
            session.add(service)
            await session.flush()
            by_slug[payload["slug"]] = service
            if embed:
                await kb.index_service(service)
            logger.info("Seeded service", extra={"slug": service.slug})

        for payload in SEED_PORTFOLIO:
            data = dict(payload)
            service = by_slug.get(data.pop("service_slug"))
            item = PortfolioItem(**data, service_id=service.id if service else None)
            session.add(item)
            await session.flush()
            if embed:
                await kb.index_portfolio_item(item)
            logger.info("Seeded portfolio item", extra={"title": item.title})

        await session.commit()
        logger.info(
            "Seeding complete",
            extra={"services": len(SEED_SERVICES), "portfolio_items": len(SEED_PORTFOLIO)},
        )


if __name__ == "__main__":
    configure_logging()
    asyncio.run(seed())
