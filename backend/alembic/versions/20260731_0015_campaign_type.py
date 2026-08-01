"""Campaign type: cold/warm/re_engagement segmentation for leads and drafts.

Backs the new campaign-type module (app/services/outreach_policy.py's
CampaignType, app/services/campaign_cadence.py,
app/services/campaign_followup_scanner.py): leads.campaign_type is the
segmentation a lead is currently being pursued under; outreach_drafts gets
its own campaign_type (a snapshot taken at draft-generation time, mirroring
recipient_email's snapshot pattern -- see app/db/models/outreach.py) plus
follow_up_sequence, which tracks how far along a lead's cadence a given
draft represents.

Both new columns backfill existing rows to 'cold' / 0 respectively: every
draft and lead created before this migration was implicitly a first cold
outreach, so this preserves that behavior exactly rather than leaving
history in an ambiguous state.

Revision ID: 0015_campaign_type
Revises: 0014_deliverability
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_campaign_type"
down_revision: str | None = "0014_deliverability"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment).
campaign_type = postgresql.ENUM(
    "cold", "warm", "re_engagement", name="campaign_type", create_type=False
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
    """Add campaign_type to leads and outreach_drafts, plus follow_up_sequence."""
    _create_enum_type("campaign_type", ["cold", "warm", "re_engagement"])

    op.add_column(
        "leads",
        sa.Column(
            "campaign_type", campaign_type, nullable=False, server_default="cold"
        ),
    )
    op.create_index(
        "ix_leads_campaign_type_pipeline_stage",
        "leads",
        ["campaign_type", "pipeline_stage"],
    )

    op.add_column(
        "outreach_drafts",
        sa.Column(
            "campaign_type", campaign_type, nullable=False, server_default="cold"
        ),
    )
    op.add_column(
        "outreach_drafts",
        sa.Column(
            "follow_up_sequence", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index(
        "ix_outreach_drafts_lead_channel_sent",
        "outreach_drafts",
        ["lead_id", "channel", "sent_at"],
    )


def downgrade() -> None:
    """Drop follow_up_sequence and campaign_type from both tables."""
    op.drop_index("ix_outreach_drafts_lead_channel_sent", table_name="outreach_drafts")
    op.drop_column("outreach_drafts", "follow_up_sequence")
    op.drop_column("outreach_drafts", "campaign_type")

    op.drop_index("ix_leads_campaign_type_pipeline_stage", table_name="leads")
    op.drop_column("leads", "campaign_type")

    _drop_enum_type("campaign_type")
