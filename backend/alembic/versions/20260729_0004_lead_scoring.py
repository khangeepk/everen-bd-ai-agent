"""Lead scoring: compliance fields on leads, and lead_scores history table.

Revision ID: 0004_scoring
Revises: 0003_audits
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_scoring"
down_revision: str | None = "0003_audits"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below.
#: Uses postgresql.ENUM, not the generic sa.Enum -- see 0001_initial's
#: comment for why.
lead_score_label = postgresql.ENUM(
    "hot", "warm", "cold", "do_not_contact", name="lead_score_label", create_type=False
)


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
    """Add compliance fields to leads and create lead_scores."""
    _create_enum_type("lead_score_label", ["hot", "warm", "cold", "do_not_contact"])

    op.add_column(
        "leads",
        sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("leads", sa.Column("do_not_contact_reason", sa.String(300), nullable=True))
    op.add_column("leads", sa.Column("consent_basis", sa.String(50), nullable=True))
    op.create_index("ix_leads_do_not_contact", "leads", ["do_not_contact"])

    op.create_table(
        "lead_scores",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("need_score", sa.Float(), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("contactability_score", sa.Float(), nullable=False),
        sa.Column("revenue_score", sa.Float(), nullable=False),
        sa.Column("compliance_score", sa.Float(), nullable=False),
        sa.Column("need_reasons", sa.Text(), nullable=True),
        sa.Column("fit_reasons", sa.Text(), nullable=True),
        sa.Column("contactability_reasons", sa.Text(), nullable=True),
        sa.Column("revenue_reasons", sa.Text(), nullable=True),
        sa.Column("compliance_reasons", sa.Text(), nullable=True),
        sa.Column("gate_triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gate_reasons", sa.Text(), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("label", lead_score_label, nullable=False),
        sa.Column("formula_version", sa.String(20), nullable=False),
        sa.Column("computed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["computed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "need_score >= 0.0 AND need_score <= 1.0 "
            "AND fit_score >= 0.0 AND fit_score <= 1.0 "
            "AND contactability_score >= 0.0 AND contactability_score <= 1.0 "
            "AND revenue_score >= 0.0 AND revenue_score <= 1.0 "
            "AND compliance_score >= 0.0 AND compliance_score <= 1.0 "
            "AND total_score >= 0.0 AND total_score <= 1.0",
            name="ck_lead_scores_component_ranges",
        ),
    )
    op.create_index("ix_lead_scores_lead_computed", "lead_scores", ["lead_id", "computed_at"])
    op.create_index("ix_lead_scores_label", "lead_scores", ["label"])


def downgrade() -> None:
    """Drop lead_scores and the compliance fields on leads."""
    op.drop_table("lead_scores")
    _drop_enum_type("lead_score_label")

    op.drop_index("ix_leads_do_not_contact", table_name="leads")
    op.drop_column("leads", "consent_basis")
    op.drop_column("leads", "do_not_contact_reason")
    op.drop_column("leads", "do_not_contact")
