"""Add detected_language + language_override to leads; draft_language to outreach_drafts.

Revision: 0019
Depends on: 0018 (alert_log + paused DraftStatus)

Adds:
  1. ``leads.detected_language VARCHAR(10)`` — BCP-47 code auto-detected from
     the lead's website or country, e.g. 'es', 'fr'. Nullable; null = unknown,
     draft generator falls back to English.
  2. ``leads.language_override VARCHAR(10)`` — manual rep override. Wins over
     ``detected_language`` when set.
  3. ``outreach_drafts.draft_language VARCHAR(10)`` — BCP-47 code the draft
     body was actually written in, snapshotted at generation time for analytics
     grouping (``GET /api/v1/analytics/languages``).

No enum changes. Pure column additions — safe to run without a transaction
isolation concern, so a single upgrade() block is fine here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add language columns to leads and outreach_drafts."""
    # --- leads.detected_language ------------------------------------------
    op.add_column(
        "leads",
        sa.Column("detected_language", sa.String(10), nullable=True),
    )
    op.create_index(
        "ix_leads_detected_language",
        "leads",
        ["detected_language"],
    )

    # --- leads.language_override ------------------------------------------
    op.add_column(
        "leads",
        sa.Column("language_override", sa.String(10), nullable=True),
    )

    # --- outreach_drafts.draft_language -----------------------------------
    op.add_column(
        "outreach_drafts",
        sa.Column("draft_language", sa.String(10), nullable=True),
    )
    op.create_index(
        "ix_outreach_drafts_draft_language",
        "outreach_drafts",
        ["draft_language"],
    )


def downgrade() -> None:
    """Remove language columns from leads and outreach_drafts."""
    op.drop_index("ix_outreach_drafts_draft_language", table_name="outreach_drafts")
    op.drop_column("outreach_drafts", "draft_language")

    op.drop_column("leads", "language_override")

    op.drop_index("ix_leads_detected_language", table_name="leads")
    op.drop_column("leads", "detected_language")
