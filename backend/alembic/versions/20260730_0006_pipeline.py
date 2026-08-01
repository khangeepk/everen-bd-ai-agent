"""CRM pipeline: stage history, inbound messages, call-center cards.

Adds `pipeline_stage` to leads (coexisting with the existing `status` column
-- see the docstring on Lead.pipeline_stage for why they are kept separate),
plus three new tables: pipeline_events (insert-only transition log),
inbound_messages (logged replies with LLM classification), and
call_center_cards (generated briefings for Hot leads).

Revision ID: 0006_pipeline
Revises: 0005_outreach
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_pipeline"
down_revision: str | None = "0005_outreach"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False on every enum here -- each is explicitly created via
#: _create_enum_type() below. Uses postgresql.ENUM, not the generic
#: sa.Enum -- see 0001_initial's comment for why.
pipeline_stage = postgresql.ENUM(
    "new",
    "contacted",
    "interested",
    "hot",
    "converted",
    "lost",
    name="pipeline_stage",
    create_type=False,
)
pipeline_transition_reason = postgresql.ENUM(
    "manual",
    "outreach_sent",
    "reply_classified",
    "suppressed",
    "forced",
    name="pipeline_transition_reason",
    create_type=False,
)
inbound_channel = postgresql.ENUM(
    "email", "whatsapp", "phone_note", name="inbound_channel", create_type=False
)
reply_intent = postgresql.ENUM(
    "book_call",
    "interested",
    "pricing",
    "not_interested",
    "unclear",
    name="reply_intent",
    create_type=False,
)

_ENUMS = (pipeline_stage, pipeline_transition_reason, inbound_channel, reply_intent)


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
    """Create pipeline tables and add pipeline_stage to leads."""
    _create_enum_type(
        "pipeline_stage", ["new", "contacted", "interested", "hot", "converted", "lost"]
    )
    _create_enum_type(
        "pipeline_transition_reason",
        ["manual", "outreach_sent", "reply_classified", "suppressed", "forced"],
    )
    _create_enum_type("inbound_channel", ["email", "whatsapp", "phone_note"])
    _create_enum_type(
        "reply_intent", ["book_call", "interested", "pricing", "not_interested", "unclear"]
    )

    op.add_column(
        "leads",
        sa.Column("pipeline_stage", pipeline_stage, nullable=False, server_default="new"),
    )

    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", inbound_channel, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("related_draft_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("logged_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("classified_intent", reply_intent, nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("classification_reasons", sa.Text(), nullable=True),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classified_by_agent", sa.Text(), nullable=True),
        sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["related_draft_id"], ["outreach_drafts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["logged_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0.0 AND classification_confidence <= 1.0)",
            name="ck_inbound_messages_confidence_range",
        ),
    )
    op.create_index(
        "ix_inbound_messages_lead_received", "inbound_messages", ["lead_id", "received_at"]
    )

    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("from_stage", pipeline_stage, nullable=True),
        sa.Column("to_stage", pipeline_stage, nullable=False),
        sa.Column("reason", pipeline_transition_reason, nullable=False),
        sa.Column("triggered_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_message_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_pipeline_events_lead_changed", "pipeline_events", ["lead_id", "changed_at"]
    )

    op.create_table(
        "call_center_cards",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("triggering_message_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("contact_title", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.Text(), nullable=True),
        sa.Column("problems_summary", sa.Text(), nullable=False),
        sa.Column("recommended_service_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("recommended_service_summary", sa.Text(), nullable=True),
        sa.Column("message_history_markdown", sa.Text(), nullable=False),
        sa.Column("call_script", sa.Text(), nullable=False),
        sa.Column("generated_by_agent", sa.Text(), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["triggering_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recommended_service_id"], ["services.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_call_center_cards_lead_generated", "call_center_cards", ["lead_id", "created_at"]
    )


def downgrade() -> None:
    """Drop pipeline tables and the pipeline_stage column."""
    op.drop_table("call_center_cards")
    op.drop_table("pipeline_events")
    op.drop_table("inbound_messages")
    op.drop_column("leads", "pipeline_stage")

    for name in (
        "reply_intent",
        "inbound_channel",
        "pipeline_transition_reason",
        "pipeline_stage",
    ):
        _drop_enum_type(name)
