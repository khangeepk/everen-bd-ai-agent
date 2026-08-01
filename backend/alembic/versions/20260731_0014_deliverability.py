"""Deliverability checklist: SPF/DKIM/DMARC checks + warmup schedules.

Backs the new deliverability module (app/services/deliverability.py,
dns_lookup.py, deliverability_checker.py, warmup.py, warmup_tracker.py,
readiness_report.py): deliverability_checks is an append-only history of DNS
checks against a sending domain; warmup_schedules holds the configured
send-volume ramp per channel (only the most recent is_active=True row per
channel is live -- see PromptVersion.is_active for the established
precedent this mirrors).

Revision ID: 0014_deliverability
Revises: 0013_objection_response
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_deliverability"
down_revision: str | None = "0013_objection_response"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment).
deliverability_check_status = postgresql.ENUM(
    "pass", "warn", "missing", "fail", name="deliverability_check_status", create_type=False
)
#: Already created by 0005_outreach -- referenced here with create_type=False
#: and NOT re-created; this migration only adds a new column using it.
outreach_channel = postgresql.ENUM(
    "email", "whatsapp", "call_script", name="outreach_channel", create_type=False
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
    """Create deliverability_checks and warmup_schedules."""
    _create_enum_type("deliverability_check_status", ["pass", "warn", "missing", "fail"])

    op.create_table(
        "deliverability_checks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("spf_status", deliverability_check_status, nullable=False),
        sa.Column("spf_record", sa.Text(), nullable=True),
        sa.Column("spf_detail", sa.Text(), nullable=True),
        sa.Column("dmarc_status", deliverability_check_status, nullable=False),
        sa.Column("dmarc_record", sa.Text(), nullable=True),
        sa.Column("dmarc_detail", sa.Text(), nullable=True),
        sa.Column("dkim_status", deliverability_check_status, nullable=False),
        sa.Column("dkim_selectors_checked", sa.String(500), nullable=False),
        sa.Column("dkim_detail", sa.Text(), nullable=True),
        sa.Column("overall_status", deliverability_check_status, nullable=False),
        sa.Column("checked_by_agent", sa.String(100), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_deliverability_checks_domain_created",
        "deliverability_checks",
        ["domain", "created_at"],
    )

    op.create_table(
        "warmup_schedules",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel", outreach_channel, nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("start_volume", sa.Integer(), nullable=False),
        sa.Column("target_daily_volume", sa.Integer(), nullable=False),
        sa.Column("ramp_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("start_volume >= 1", name="ck_warmup_schedules_start_volume_positive"),
        sa.CheckConstraint(
            "target_daily_volume >= start_volume", name="ck_warmup_schedules_target_gte_start"
        ),
        sa.CheckConstraint("ramp_days >= 1", name="ck_warmup_schedules_ramp_days_positive"),
    )
    op.create_index(
        "ix_warmup_schedules_channel_active", "warmup_schedules", ["channel", "is_active"]
    )


def downgrade() -> None:
    """Drop warmup_schedules and deliverability_checks."""
    op.drop_table("warmup_schedules")
    op.drop_table("deliverability_checks")
    _drop_enum_type("deliverability_check_status")
