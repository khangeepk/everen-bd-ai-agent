"""Objection-response drafts: objection_type + triggering_message_id on outreach_drafts.

Backs app/services/objection_response_scanner.py: when a reply is classified
as an objection (price/timing/not_interested_yet -- see
app/services/reply_classification.py's classify_objection()), a suggested
response draft is generated with status=pending_review, same as any other
outreach draft. objection_type records which kind of objection it addresses
(null for every ordinary cold-outreach draft); triggering_message_id records
which InboundMessage prompted it (mirrors CallCenterCard.triggering_message_id),
used to avoid generating a second draft for the same message on
re-classification.

No backfill needed -- both columns are nullable and every pre-existing draft
correctly has neither set (nothing before this migration was
objection-triggered).

Revision ID: 0013_objection_response
Revises: 0012_email_enrichment
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_objection_response"
down_revision: str | None = "0012_email_enrichment"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below, per
#: the established convention in this codebase (see 0001_initial's comment).
objection_type = postgresql.ENUM(
    "price", "timing", "not_interested_yet", name="objection_type", create_type=False
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
    """Add objection_type and triggering_message_id to outreach_drafts."""
    _create_enum_type("objection_type", ["price", "timing", "not_interested_yet"])

    op.add_column(
        "outreach_drafts",
        sa.Column("objection_type", objection_type, nullable=True),
    )
    op.add_column(
        "outreach_drafts",
        sa.Column(
            "triggering_message_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("inbound_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_outreach_drafts_triggering_message",
        "outreach_drafts",
        ["triggering_message_id"],
    )


def downgrade() -> None:
    """Drop triggering_message_id and objection_type from outreach_drafts."""
    op.drop_index("ix_outreach_drafts_triggering_message", table_name="outreach_drafts")
    op.drop_column("outreach_drafts", "triggering_message_id")
    op.drop_column("outreach_drafts", "objection_type")
    _drop_enum_type("objection_type")
