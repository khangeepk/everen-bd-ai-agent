"""LinkedIn outreach channel: connection-request + follow-up text, no send.

Backs the new draft-only LinkedIn channel (app/services/outreach_policy.py's
OutreachChannel.LINKEDIN, app/agents/outreach.py's generate_linkedin_content):
extends the existing outreach_channel enum with 'linkedin' and adds
outreach_drafts.linkedin_followup_message, the follow-up text a rep sends
manually after a prospect accepts the connection request (which lives in the
existing `body` column, same as every other channel's primary text).

This system never sends or scrapes LinkedIn -- see outreach_policy.py's
module docstring. No new send-gate logic is needed: POST
/outreach/drafts/{id}/send already rejects every non-EMAIL channel outright,
so LinkedIn drafts are automatically covered by that existing check.

Revision ID: 0017_linkedin_channel
Revises: 0016_calendar_booking
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017_linkedin_channel"
down_revision: str | None = "0016_calendar_booking"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add 'linkedin' to outreach_channel and the follow-up message column."""
    # Same "ALTER TYPE ... ADD VALUE IF NOT EXISTS" pattern already used in
    # 0002_places_discovery (lead_source) and 0016_calendar_booking
    # (pipeline_stage) -- no autocommit block needed for a plain add-value.
    op.execute("ALTER TYPE outreach_channel ADD VALUE IF NOT EXISTS 'linkedin'")

    op.add_column(
        "outreach_drafts",
        sa.Column("linkedin_followup_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the follow-up message column.

    Note:
        PostgreSQL cannot remove a value from an enum type, so the
        'linkedin' outreach_channel value is intentionally left in place,
        same as 'google_places' was left on lead_source in
        0002_places_discovery's downgrade and 'meeting_booked' was left on
        pipeline_stage in 0016_calendar_booking's downgrade.
    """
    op.drop_column("outreach_drafts", "linkedin_followup_message")
