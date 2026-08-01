"""Lead trigger-event signals: lead_signals + lead_signal_checkpoints.

Backs the new signal-detection feature (app/services/signal_detection.py,
app/services/signal_scanner.py): job-posting changes, Google business-status
changes, and review-count jumps for existing leads.

See app/db/models/signal.py's module docstring for why
lead_signal_checkpoints.fingerprint_hash is a keyed hash of a bucketed value
rather than a raw Places field -- required by the existing
app/services/places_policy.py Google Maps Content restrictions.

Revision ID: 0011_lead_signals
Revises: 0010_cost_events
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_lead_signals"
down_revision: str | None = "0010_cost_events"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment on
#: why generic sa.Enum autogeneration is not used here).
lead_signal_type = postgresql.ENUM(
    "job_posting",
    "business_status_change",
    "review_count_jump",
    name="lead_signal_type",
    create_type=False,
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
    """Create lead_signals and lead_signal_checkpoints."""
    _create_enum_type(
        "lead_signal_type", ["job_posting", "business_status_change", "review_count_jump"]
    )

    op.create_table(
        "lead_signals",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_type", lead_signal_type, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(500), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_lead_signals_lead_detected", "lead_signals", ["lead_id", "detected_at"])
    op.create_index(
        "ix_lead_signals_type_detected", "lead_signals", ["signal_type", "detected_at"]
    )

    op.create_table(
        "lead_signal_checkpoints",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_type", lead_signal_type, nullable=False),
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("lead_id", "signal_type", name="uq_signal_checkpoints_lead_type"),
    )


def downgrade() -> None:
    """Drop lead_signal_checkpoints and lead_signals."""
    op.drop_table("lead_signal_checkpoints")
    op.drop_table("lead_signals")
    _drop_enum_type("lead_signal_type")
