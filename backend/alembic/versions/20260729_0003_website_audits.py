"""Website audit, findings, social review, and report tables.

Revision ID: 0003_audits
Revises: 0002_places
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_audits"
down_revision: str | None = "0002_places"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False on every enum here -- each is explicitly created via
#: _create_enum_type() below. Uses postgresql.ENUM, not the generic
#: sa.Enum -- see 0001_initial's comment for why.
audit_status = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    "robots_blocked",
    name="audit_status",
    create_type=False,
)
finding_category = postgresql.ENUM(
    "performance",
    "seo",
    "accessibility",
    "best_practices",
    "security",
    "mobile",
    "broken_links",
    "contact_form",
    "social",
    name="finding_category",
    create_type=False,
)
finding_severity = postgresql.ENUM(
    "critical", "high", "medium", "low", "info", name="finding_severity", create_type=False
)
social_platform = postgresql.ENUM(
    "linkedin",
    "facebook",
    "instagram",
    "x",
    "youtube",
    "tiktok",
    "google_business",
    name="social_platform",
    create_type=False,
)
posting_cadence = postgresql.ENUM(
    "weekly_or_more",
    "monthly",
    "quarterly",
    "dormant",
    "none",
    name="posting_cadence",
    create_type=False,
)

_ENUMS = (audit_status, finding_category, finding_severity, social_platform, posting_cadence)


def _timestamps() -> list[sa.Column]:
    """Standard created_at / updated_at columns.

    Returns:
        The two timestamp columns.
    """
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _create_enum_type(name: str, values: list[str]) -> None:
    """Create a PostgreSQL ENUM type if it does not already exist.

    See the identical helper in 0001_initial for why this uses a raw DO
    block rather than sa.Enum.create(bind, checkfirst=True).

    Args:
        name: The SQL type name.
        values: The enum's labels, in order.
    """
    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({values_sql}); "
        f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )


def _drop_enum_type(name: str) -> None:
    """Drop a PostgreSQL ENUM type if it exists.

    Args:
        name: The SQL type name.
    """
    op.execute(f"DROP TYPE IF EXISTS {name}")


def upgrade() -> None:
    """Create the audit tables."""
    _create_enum_type(
        "audit_status", ["pending", "running", "completed", "failed", "robots_blocked"]
    )
    _create_enum_type(
        "finding_category",
        [
            "performance",
            "seo",
            "accessibility",
            "best_practices",
            "security",
            "mobile",
            "broken_links",
            "contact_form",
            "social",
        ],
    )
    _create_enum_type("finding_severity", ["critical", "high", "medium", "low", "info"])
    _create_enum_type(
        "social_platform",
        ["linkedin", "facebook", "instagram", "x", "youtube", "tiktok", "google_business"],
    )
    _create_enum_type(
        "posting_cadence", ["weekly_or_more", "monthly", "quarterly", "dormant", "none"]
    )

    op.create_table(
        "website_audits",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("status", audit_status, nullable=False, server_default="pending"),
        sa.Column("requested_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("performance_score", sa.Float(), nullable=True),
        sa.Column("seo_score", sa.Float(), nullable=True),
        sa.Column("accessibility_score", sa.Float(), nullable=True),
        sa.Column("best_practices_score", sa.Float(), nullable=True),
        sa.Column("mobile_score", sa.Float(), nullable=True),
        sa.Column("ssl_valid", sa.Boolean(), nullable=True),
        sa.Column("ssl_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_form_found", sa.Boolean(), nullable=True),
        sa.Column("contact_form_reachable", sa.Boolean(), nullable=True),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("links_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("broken_link_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("robots_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "health_score IS NULL OR (health_score >= 0.0 AND health_score <= 1.0)",
            name="ck_website_audits_health_score_range",
        ),
        sa.CheckConstraint(
            "pages_crawled >= 0 AND links_checked >= 0 AND broken_link_count >= 0",
            name="ck_website_audits_counts_nonneg",
        ),
    )
    op.create_index("ix_website_audits_lead_created", "website_audits", ["lead_id", "created_at"])
    op.create_index("ix_website_audits_status", "website_audits", ["status"])

    op.create_table(
        "audit_findings",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("audit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("category", finding_category, nullable=False),
        sa.Column("severity", finding_severity, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("mapped_service_id", sa.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["audit_id"], ["website_audits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mapped_service_id"], ["services.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("audit_id", "code", name="uq_audit_findings_audit_code"),
    )
    op.create_index(
        "ix_audit_findings_audit_severity", "audit_findings", ["audit_id", "severity"]
    )

    op.create_table(
        "social_profile_reviews",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", social_platform, nullable=False),
        sa.Column("profile_url", sa.String(500), nullable=True),
        sa.Column("profile_exists", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_profile_image", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_cover_image", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_description", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_website_link", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_contact_details", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cadence", posting_cadence, nullable=False, server_default="none"),
        sa.Column("follower_band", sa.String(50), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("completeness_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("reviewed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("lead_id", "platform", name="uq_social_reviews_lead_platform"),
        sa.CheckConstraint(
            "completeness_score >= 0.0 AND completeness_score <= 1.0",
            name="ck_social_reviews_score_range",
        ),
    )
    op.create_index("ix_social_profile_reviews_lead_id", "social_profile_reviews", ["lead_id"])

    op.create_table(
        "audit_reports",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("audit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("headline", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("generated_by_agent", sa.String(100), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("social_score", sa.Float(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["audit_id"], ["website_audits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("audit_id", name="uq_audit_reports_audit_id"),
    )


def downgrade() -> None:
    """Drop the audit tables and their enum types."""
    op.drop_table("audit_reports")
    op.drop_table("social_profile_reviews")
    op.drop_table("audit_findings")
    op.drop_table("website_audits")

    for name in (
        "posting_cadence",
        "social_platform",
        "finding_severity",
        "finding_category",
        "audit_status",
    ):
        _drop_enum_type(name)
