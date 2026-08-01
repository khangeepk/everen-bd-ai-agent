"""Analytics: prompt version log, email open tracking, A/B tagging on drafts.

Adds `prompt_versions` (the "old vs new prompt" log; two active rows sharing
an `experiment_group` form an A/B test) and `email_open_events` (backs the
dashboard's open rate, fed by the new tracking-pixel endpoint). Adds
`prompt_version_id` and `ab_variant` to `outreach_drafts` so a sent draft's
performance can be attributed back to the prompt that generated it.

Revision ID: 0007_analytics
Revises: 0006_pipeline
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_analytics"
down_revision: str | None = "0006_pipeline"
branch_labels: str | None = None
depends_on: str | None = None

# Reuses the existing outreach_channel enum type (created in 0005_outreach)
# rather than redefining it -- prompt_versions.channel and outreach_drafts
# both describe the same set of channels. Uses postgresql.ENUM, not the
# generic sa.Enum -- see 0001_initial's comment for why: the generic type's
# create_type=False was not reliably honored by op.create_table()/
# op.add_column(), so it kept re-emitting CREATE TYPE for an enum that
# already existed from 0005_outreach.
outreach_channel = postgresql.ENUM(
    "email", "whatsapp", "call_script", name="outreach_channel", create_type=False
)


def _timestamps() -> list[sa.Column]:
    """Standard created_at / updated_at columns.

    Returns:
        The two timestamp columns.
    """
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    """Create prompt_versions and email_open_events; extend outreach_drafts."""
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("channel", outreach_channel, nullable=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("experiment_group", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_prompt_versions_agent_channel_active",
        "prompt_versions",
        ["agent_name", "channel", "is_active"],
    )
    op.create_index(
        "ix_prompt_versions_experiment_group", "prompt_versions", ["experiment_group"]
    )

    op.add_column(
        "outreach_drafts",
        sa.Column(
            "prompt_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("prompt_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("outreach_drafts", sa.Column("ab_variant", sa.String(50), nullable=True))

    op.create_table(
        "email_open_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_hash", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["draft_id"], ["outreach_drafts.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_email_open_events_draft_opened", "email_open_events", ["draft_id", "opened_at"]
    )


def downgrade() -> None:
    """Drop email_open_events, the new outreach_drafts columns, and prompt_versions."""
    op.drop_table("email_open_events")
    op.drop_column("outreach_drafts", "ab_variant")
    op.drop_column("outreach_drafts", "prompt_version_id")
    op.drop_table("prompt_versions")
