"""Initial schema: users, leads, services knowledge base with pgvector.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

#: Must match Settings.embedding_dimension / the EMBEDDING_MODEL in use.
EMBEDDING_DIM = 1536

#: create_type=False on every enum here: each type is explicitly created via
#: _create_enum_type() below; letting the column definition also auto-emit
#: CREATE TYPE would double-create it and fail with DuplicateObjectError.
#:
#: Uses postgresql.ENUM (the dialect-specific type), not the generic
#: sa.Enum -- the generic version's create_type flag was found NOT to
#: suppress the automatic CREATE TYPE that op.create_table()/op.add_column()
#: emit for an enum column, so it kept double-creating types even with
#: create_type=False set. The PG-specific ENUM class honors the flag
#: correctly in every DDL code path.
lead_source = postgresql.ENUM(
    "manual",
    "web_research",
    "linkedin",
    "referral",
    "inbound_form",
    "csv_import",
    "partner",
    name="lead_source",
    create_type=False,
)
lead_status = postgresql.ENUM(
    "new",
    "enriching",
    "qualified",
    "contacted",
    "responded",
    "won",
    "lost",
    "disqualified",
    name="lead_status",
    create_type=False,
)
user_role = postgresql.ENUM(
    "admin", "bd_manager", "bd_rep", "viewer", name="user_role", create_type=False
)
pricing_model = postgresql.ENUM(
    "fixed",
    "hourly",
    "monthly_retainer",
    "project_range",
    "custom",
    name="pricing_model",
    create_type=False,
)
chunk_source_type = postgresql.ENUM(
    "service", "portfolio", name="chunk_source_type", create_type=False
)


def _create_enum_type(name: str, values: list[str]) -> None:
    """Create a PostgreSQL ENUM type if it does not already exist.

    A raw ``DO $$ ... EXCEPTION WHEN duplicate_object THEN null; END $$;``
    block, rather than ``sa.Enum.create(bind, checkfirst=True)`` -- the
    latter's existence check proved unreliable in practice here (it raised
    ``DuplicateObjectError`` even against a freshly created database), so
    PostgreSQL itself is asked to swallow the "already exists" case instead
    of relying on a pre-check that can be wrong.

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
    """Create the pgvector extension, all tables, and the HNSW index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    _create_enum_type(
        "lead_source",
        [
            "manual",
            "web_research",
            "linkedin",
            "referral",
            "inbound_form",
            "csv_import",
            "partner",
        ],
    )
    _create_enum_type(
        "lead_status",
        [
            "new",
            "enriching",
            "qualified",
            "contacted",
            "responded",
            "won",
            "lost",
            "disqualified",
        ],
    )
    _create_enum_type("user_role", ["admin", "bd_manager", "bd_rep", "viewer"])
    _create_enum_type(
        "pricing_model", ["fixed", "hourly", "monthly_retainer", "project_range", "custom"]
    )
    _create_enum_type("chunk_source_type", ["service", "portfolio"])

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="clerk"),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("role", user_role, nullable=False, server_default="bd_rep"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider_subject", name="uq_users_provider_subject"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_provider_subject", "users", ["provider_subject"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "leads",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("contact_title", sa.String(150), nullable=True),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("linkedin_url", sa.String(500), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("source", lead_source, nullable=False, server_default="manual"),
        sa.Column("source_detail", sa.String(500), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", lead_status, nullable=False, server_default="new"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("contact_email", name="uq_leads_contact_email"),
        sa.CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_leads_confidence_score_range",
        ),
    )
    op.create_index("ix_leads_name", "leads", ["name"])
    op.create_index("ix_leads_category", "leads", ["category"])
    op.create_index("ix_leads_contact_email", "leads", ["contact_email"])
    op.create_index("ix_leads_status_created_at", "leads", ["status", "created_at"])
    op.create_index("ix_leads_category_status", "leads", ["category", "status"])

    op.create_table(
        "services",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("pricing_model", pricing_model, nullable=False, server_default="project_range"),
        sa.Column("typical_duration_weeks", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_services_slug"),
        sa.CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_services_price_range_ordered",
        ),
        sa.CheckConstraint("price_min IS NULL OR price_min >= 0", name="ck_services_price_min_nonneg"),
    )
    op.create_index("ix_services_slug", "services", ["slug"])
    op.create_index("ix_services_category", "services", ["category"])
    op.create_index("ix_services_category_active", "services", ["category", "is_active"])

    op.create_table(
        "portfolio_items",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("client_name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_portfolio_items_service_id", "portfolio_items", ["service_id"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", chunk_source_type, nullable=False),
        sa.Column("source_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source_type", "source_id", "chunk_index", name="uq_knowledge_chunks_source_index"
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index_nonneg"),
    )
    op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source_type", "source_id"])

    # HNSW index for cosine distance (AGENTS.md 9.1). m/ef_construction are the
    # pgvector defaults; raise ef_construction for better recall at index-build cost.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """Drop all tables and enum types created by this migration."""
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_table("knowledge_chunks")
    op.drop_table("portfolio_items")
    op.drop_table("services")
    op.drop_table("leads")
    op.drop_table("users")

    for name in ("chunk_source_type", "pricing_model", "user_role", "lead_status", "lead_source"):
        _drop_enum_type(name)
