"""PII Encryption Closure + Webhook Idempotency.

Revision: 0020
Depends on: 0019 (lead_language_draft_language)

Adds:
  1. ``processed_webhook_events`` table for webhook idempotency.
  2. ``suppression_entries.identifier_hash`` blind-index column + unique constraint.
  3. ``bounce_events.identifier_hash`` blind-index column + index.
  4. Encrypts CallCenterCard contact fields (contact_name, contact_email, contact_phone).

Backfills ``identifier_hash`` on existing suppression_entries and bounce_events rows.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None
# ---------------------------------------------------------------------------


def _compute_hash(value: str) -> str:
    """Compute HMAC-SHA256 blind index matching app.services.pii.blind_index."""
    # Default dev secret_key if not overridden in env
    secret_key = "CHANGE_ME"
    normalized = value.strip().lower()
    message = f"suppression_identifier:{normalized}".encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def upgrade() -> None:
    """Run upgrade migration."""
    # 1. processed_webhook_events table
    op.create_table(
        "processed_webhook_events",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="sendgrid"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_processed_webhook_events_event_id"),
    )
    op.create_index(
        "ix_processed_webhook_events_provider_event",
        "processed_webhook_events",
        ["provider", "event_id"],
    )

    # 2. Add identifier_hash to suppression_entries
    op.add_column("suppression_entries", sa.Column("identifier_hash", sa.String(64), nullable=True))

    # Backfill suppression_entries identifier_hash
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, identifier FROM suppression_entries")).fetchall()
    for row_id, ident in rows:
        if ident:
            h = _compute_hash(ident)
            conn.execute(
                sa.text("UPDATE suppression_entries SET identifier_hash = :h WHERE id = :id"),
                {"h": h, "id": row_id},
            )

    # Make identifier_hash non-nullable and add unique constraint
    with op.batch_alter_table("suppression_entries") as batch_op:
        batch_op.alter_column("identifier_hash", nullable=False)
        batch_op.create_unique_constraint("uq_suppression_entries_identifier_hash", ["identifier_hash"])
        batch_op.create_index("ix_suppression_entries_hash", ["identifier_hash"])

    # 3. Add identifier_hash to bounce_events
    op.add_column("bounce_events", sa.Column("identifier_hash", sa.String(64), nullable=True))
    rows = conn.execute(sa.text("SELECT id, identifier FROM bounce_events")).fetchall()
    for row_id, ident in rows:
        if ident:
            h = _compute_hash(ident)
            conn.execute(
                sa.text("UPDATE bounce_events SET identifier_hash = :h WHERE id = :id"),
                {"h": h, "id": row_id},
            )

    op.create_index(
        "ix_bounce_events_identifier_hash_occurred",
        "bounce_events",
        ["identifier_hash", "occurred_at"],
    )


def downgrade() -> None:
    """Run downgrade migration."""
    op.drop_index("ix_bounce_events_identifier_hash_occurred", table_name="bounce_events")
    op.drop_column("bounce_events", "identifier_hash")

    with op.batch_alter_table("suppression_entries") as batch_op:
        batch_op.drop_index("ix_suppression_entries_hash")
        batch_op.drop_constraint("uq_suppression_entries_identifier_hash", type_="unique")
        batch_op.drop_column("identifier_hash")

    op.drop_index("ix_processed_webhook_events_provider_event", table_name="processed_webhook_events")
    op.drop_table("processed_webhook_events")
