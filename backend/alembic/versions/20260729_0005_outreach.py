"""Outreach: drafts, audit log, suppression, bounces, daily send counters.

Adds the human-approval workflow required by AGENTS.md section 8, plus
whatsapp_opt_in on leads to gate WhatsApp draft generation.

Revision ID: 0005_outreach
Revises: 0004_scoring
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_outreach"
down_revision: str | None = "0004_scoring"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False on every enum here -- each is explicitly created via
#: _create_enum_type() below. Uses postgresql.ENUM, not the generic
#: sa.Enum -- see 0001_initial's comment for why.
outreach_channel = postgresql.ENUM(
    "email", "whatsapp", "call_script", name="outreach_channel", create_type=False
)
draft_status = postgresql.ENUM(
    "pending_review",
    "approved",
    "rejected",
    "sent",
    "failed",
    name="draft_status",
    create_type=False,
)
suppression_reason = postgresql.ENUM(
    "unsubscribed",
    "hard_bounce",
    "spam_complaint",
    "manual",
    "legal_request",
    name="suppression_reason",
    create_type=False,
)
bounce_type = postgresql.ENUM(
    "hard", "soft", "complaint", "blocked", "unknown", name="bounce_type", create_type=False
)

_ENUMS = (outreach_channel, draft_status, suppression_reason, bounce_type)


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
    """Create the outreach tables and add WhatsApp opt-in to leads."""
    _create_enum_type("outreach_channel", ["email", "whatsapp", "call_script"])
    _create_enum_type(
        "draft_status", ["pending_review", "approved", "rejected", "sent", "failed"]
    )
    _create_enum_type(
        "suppression_reason",
        ["unsubscribed", "hard_bounce", "spam_complaint", "manual", "legal_request"],
    )
    _create_enum_type("bounce_type", ["hard", "soft", "complaint", "blocked", "unknown"])

    op.add_column(
        "leads",
        sa.Column("whatsapp_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("leads", sa.Column("whatsapp_opt_in_source", sa.String(200), nullable=True))

    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", outreach_channel, nullable=False),
        sa.Column("status", draft_status, nullable=False, server_default="pending_review"),
        sa.Column("subject", sa.String(300), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("recipient_phone", sa.String(50), nullable=True),
        sa.Column("sender_name", sa.String(200), nullable=True),
        sa.Column("sender_email", sa.String(320), nullable=True),
        sa.Column("sender_company", sa.String(200), nullable=True),
        sa.Column("sender_physical_address", sa.String(500), nullable=True),
        sa.Column("unsubscribe_url", sa.String(600), nullable=True),
        sa.Column("whatsapp_template_name", sa.String(200), nullable=True),
        sa.Column("review_warnings", sa.Text(), nullable=True),
        sa.Column("source_audit_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("source_service_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_agent", sa.String(100), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_audit_id"], ["website_audits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_service_id"], ["services.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        # Enforce the approval workflow at the database level, not just in
        # application code (AGENTS.md section 8).
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)",
            name="ck_outreach_drafts_sent_at_matches_status",
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR approved_by_id IS NOT NULL",
            name="ck_outreach_drafts_approval_is_attributed",
        ),
        sa.CheckConstraint(
            "status <> 'sent' OR approved_by_id IS NOT NULL",
            name="ck_outreach_drafts_sent_requires_approver",
        ),
    )
    op.create_index("ix_outreach_drafts_status_created", "outreach_drafts", ["status", "created_at"])
    op.create_index("ix_outreach_drafts_lead", "outreach_drafts", ["lead_id"])
    op.create_index(
        "ix_outreach_drafts_provider_message_id", "outreach_drafts", ["provider_message_id"]
    )

    op.create_table(
        "outreach_audit_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("old_status", draft_status, nullable=True),
        sa.Column("new_status", draft_status, nullable=False),
        sa.Column("changed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["draft_id"], ["outreach_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_outreach_audit_log_draft_changed", "outreach_audit_log", ["draft_id", "changed_at"]
    )

    op.create_table(
        "suppression_entries",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("identifier", sa.String(320), nullable=False),
        sa.Column("channel", outreach_channel, nullable=False),
        sa.Column("reason", suppression_reason, nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("source_draft_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_draft_id"], ["outreach_drafts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("identifier", name="uq_suppression_entries_identifier"),
    )
    op.create_index("ix_suppression_entries_identifier", "suppression_entries", ["identifier"])
    op.create_index("ix_suppression_entries_reason", "suppression_entries", ["reason"])

    op.create_table(
        "bounce_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("identifier", sa.String(320), nullable=False),
        sa.Column("bounce_type", bounce_type, nullable=False),
        sa.Column("provider_event", sa.String(100), nullable=True),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["draft_id"], ["outreach_drafts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_bounce_events_identifier", "bounce_events", ["identifier"])
    op.create_index(
        "ix_bounce_events_identifier_occurred", "bounce_events", ["identifier", "occurred_at"]
    )

    op.create_table(
        "daily_send_counters",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("channel", outreach_channel, nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("quota_date", "channel", name="uq_daily_send_counters_date_channel"),
        sa.CheckConstraint("sent_count >= 0", name="ck_daily_send_counters_count_nonneg"),
    )
    op.create_index("ix_daily_send_counters_quota_date", "daily_send_counters", ["quota_date"])


def downgrade() -> None:
    """Drop the outreach tables and the WhatsApp opt-in columns.

    Note:
        Dropping suppression_entries destroys opt-out records that CAN-SPAM
        requires be honoured indefinitely. Export them before running this
        against any environment that has sent real email.
    """
    op.drop_table("daily_send_counters")
    op.drop_table("bounce_events")
    op.drop_table("suppression_entries")
    op.drop_table("outreach_audit_log")
    op.drop_table("outreach_drafts")

    op.drop_column("leads", "whatsapp_opt_in_source")
    op.drop_column("leads", "whatsapp_opt_in")

    for name in ("bounce_type", "suppression_reason", "draft_status", "outreach_channel"):
        _drop_enum_type(name)
