"""alert_log table + 'paused' DraftStatus enum value.

Adds:
  1. ``'paused'`` to the ``draftstatus`` PostgreSQL enum. A ``PAUSED`` draft
     is set by the n8n SendGrid health-monitor webhook when a sending domain
     breaches a deliverability threshold. A human must explicitly restore the
     draft to ``PENDING_REVIEW`` via the approval UI; no Celery task may
     un-pause automatically. (See AGENTS.md section 8 and the PAUSED entry in
     app/db/models/outreach.py's DraftStatus.)

  2. The ``alert_log`` table that the webhook endpoint writes to — one row per
     deliverability event (domain × alert_type). Columns:
       - id                  UUID PK
       - alert_type          VARCHAR(64)  e.g. 'bounce_rate_exceeded'
       - domain              VARCHAR(253) sending domain
       - metric_value        FLOAT        computed value at trigger time
       - threshold_value     FLOAT        configured threshold crossed
       - triggered_at        TIMESTAMPTZ  UTC, never null
       - resolved_at         TIMESTAMPTZ  null until human resolution
       - drafts_paused_count INTEGER      drafts bulk-moved to PAUSED

Revision ID: 0018_alert_log_paused_status
Revises: 0017_linkedin_channel
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018_alert_log_paused_status"
down_revision: str | None = "0017_linkedin_channel"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add 'paused' to draftstatus enum and create alert_log table.

    PostgreSQL cannot add an enum value inside a transaction that also
    contains DDL referencing the enum, so the ADD VALUE runs first in
    its own execute() call before the CREATE TABLE.
    """
    # 1. Extend the draftstatus enum.
    # Uses the same "IF NOT EXISTS" guard already established by earlier
    # migrations (0002_places_discovery, 0017_linkedin_channel).
    op.execute("ALTER TYPE draftstatus ADD VALUE IF NOT EXISTS 'paused'")

    # 2. Create the alert_log table.
    op.create_table(
        "alert_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drafts_paused_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Composite index: fast domain-scoped timeline queries.
    op.create_index(
        "ix_alert_log_domain_triggered_at",
        "alert_log",
        ["domain", "triggered_at"],
    )
    # Composite index: fast alert-type queries (e.g. all unresolved bounce alerts).
    op.create_index(
        "ix_alert_log_type_triggered_at",
        "alert_log",
        ["alert_type", "triggered_at"],
    )
    # Single-column index declared in model via Index() — replicated here for
    # Alembic round-trip fidelity.
    op.create_index(
        "ix_alert_log_alert_type",
        "alert_log",
        ["alert_type"],
    )


def downgrade() -> None:
    """Drop alert_log table.

    Note:
        PostgreSQL cannot remove a value from an enum type once added, so
        ``'paused'`` remains in the ``draftstatus`` enum after downgrade.
        Any application code running against the downgraded schema must not
        write that value (the ORM model change would be reverted alongside
        this migration).
    """
    op.drop_index("ix_alert_log_alert_type", table_name="alert_log")
    op.drop_index("ix_alert_log_type_triggered_at", table_name="alert_log")
    op.drop_index("ix_alert_log_domain_triggered_at", table_name="alert_log")
    op.drop_table("alert_log")
