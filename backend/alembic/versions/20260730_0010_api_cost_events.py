"""API cost guard: api_cost_events ledger.

Backs the daily budget cap + 80% alert on Places and OpenAI chat-completion
calls (app/services/cost_guard.py, app/services/cost_tracking.py).

Revision ID: 0010_cost_events
Revises: 0009_rbac_roles
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_cost_events"
down_revision: str | None = "0009_rbac_roles"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below.
#: Uses postgresql.ENUM, not the generic sa.Enum -- see 0001_initial's
#: comment for why.
cost_provider = postgresql.ENUM("places", "openai", name="cost_provider", create_type=False)


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
    """Create the api_cost_events table."""
    _create_enum_type("cost_provider", ["places", "openai"])

    op.create_table(
        "api_cost_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", cost_provider, nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_api_cost_events_provider_occurred", "api_cost_events", ["provider", "occurred_at"]
    )


def downgrade() -> None:
    """Drop the api_cost_events table."""
    op.drop_table("api_cost_events")
    _drop_enum_type("cost_provider")
