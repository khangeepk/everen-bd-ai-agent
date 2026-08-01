"""Email enrichment: contact_email provenance/trust on leads + attempt history.

Backs the fallback email-discovery chain (app/services/email_enrichment.py,
email_discovery.py, email_enrichment_scanner.py): leads gain
contact_email_source/contact_email_confidence/contact_email_verified, and
every candidate the chain ever considered is recorded in the new
email_enrichment_attempts table (encrypted candidate_email, same treatment
as leads.contact_email).

Existing rows backfill to contact_email_source='manual',
contact_email_verified=true -- preserving today's behavior exactly (every
email on file before this migration was either typed in by a human or
supplied during a Places candidate promotion, i.e. trusted under the old
regime) so nothing already in the system suddenly gets blocked from
drafting.

Revision ID: 0012_email_enrichment
Revises: 0011_lead_signals
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_email_enrichment"
down_revision: str | None = "0011_lead_signals"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment).
email_source = postgresql.ENUM(
    "manual", "website_contact_page", "pattern_guess", name="email_source", create_type=False
)


def _create_enum_type(name: str, values: list[str]) -> None:
    """Create a PostgreSQL ENUM type if it does not already exist.

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
    """Add contact-email provenance columns and the attempt-history table."""
    _create_enum_type("email_source", ["manual", "website_contact_page", "pattern_guess"])

    op.add_column(
        "leads",
        sa.Column(
            "contact_email_source",
            email_source,
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "leads", sa.Column("contact_email_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "leads",
        sa.Column(
            "contact_email_verified", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )

    op.create_table(
        "email_enrichment_attempts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", email_source, nullable=False),
        sa.Column("candidate_email", sa.Text(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("was_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_email_enrichment_attempts_lead_detected",
        "email_enrichment_attempts",
        ["lead_id", "detected_at"],
    )


def downgrade() -> None:
    """Drop the attempt-history table and the new lead columns."""
    op.drop_table("email_enrichment_attempts")
    op.drop_column("leads", "contact_email_verified")
    op.drop_column("leads", "contact_email_confidence")
    op.drop_column("leads", "contact_email_source")
    _drop_enum_type("email_source")
