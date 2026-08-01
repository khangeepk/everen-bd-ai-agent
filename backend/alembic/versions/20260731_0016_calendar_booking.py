"""Calendar booking: MEETING_BOOKED pipeline stage + meetings table.

Backs the new calendar-booking module (app/services/booking_slots.py,
booking_token.py, google_calendar.py; app/api/v1/booking.py): extends the
existing pipeline_stage and pipeline_transition_reason enums with a
'meeting_booked' value (see app/services/pipeline.py's _ALLOWED_TRANSITIONS
docstring for why it's reachable directly from CONTACTED/INTERESTED/HOT),
and adds the meetings table recording each confirmed booking.

Revision ID: 0016_calendar_booking
Revises: 0015_campaign_type
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_calendar_booking"
down_revision: str | None = "0015_campaign_type"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment).
meeting_status = postgresql.ENUM(
    "booked", "cancelled", name="meeting_status", create_type=False
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
    """Add the meeting_booked enum values and the meetings table."""
    # Same "ALTER TYPE ... ADD VALUE IF NOT EXISTS" pattern already used in
    # 0002_places_discovery to extend lead_source with 'google_places' --
    # no autocommit block needed for a plain add-value (only reusing the new
    # label within the same transaction that added it would require one).
    op.execute("ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'meeting_booked'")
    op.execute(
        "ALTER TYPE pipeline_transition_reason ADD VALUE IF NOT EXISTS 'meeting_booked'"
    )

    _create_enum_type("meeting_status", ["booked", "cancelled"])

    op.create_table(
        "meetings",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("triggering_message_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attendee_email", sa.Text(), nullable=False),
        sa.Column("calendar_event_id", sa.Text(), nullable=False),
        sa.Column("calendar_event_link", sa.Text(), nullable=True),
        sa.Column("status", meeting_status, nullable=False, server_default="booked"),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["triggering_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_meetings_lead_scheduled_start", "meetings", ["lead_id", "scheduled_start"])


def downgrade() -> None:
    """Drop the meetings table.

    Note:
        PostgreSQL cannot remove a value from an enum type, so the
        'meeting_booked' pipeline_stage and pipeline_transition_reason
        values are intentionally left in place, same as 'google_places' was
        left on lead_source in 0002_places_discovery's downgrade.
    """
    op.drop_index("ix_meetings_lead_scheduled_start", table_name="meetings")
    op.drop_table("meetings")
    _drop_enum_type("meeting_status")
