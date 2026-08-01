"""PII encryption at rest, GDPR consent flag, and erasure bookkeeping.

Widens ``leads.contact_email``/``contact_phone`` and
``outreach_drafts.recipient_email``/``recipient_phone`` to ``TEXT`` (Fernet
ciphertext is longer than the plaintext it replaces and does not fit a fixed
``VARCHAR`` bound predictably), adds ``leads.contact_email_hash`` (the
deterministic blind-index HMAC that now backs the unique constraint and every
equality lookup that used to run against the plaintext column -- see
app/db/types.py::EncryptedString and app/services/pii.py), and adds the GDPR
columns: ``gdpr_consent``, ``gdpr_consent_recorded_at``,
``gdpr_consent_source``, ``pii_erased_at``.

IMPORTANT -- this migration does NOT re-encrypt or back-hash any pre-existing
plaintext rows. There is no production data behind this migration at the time
it was written (dev/local databases only per the project history), so that
gap is acceptable here; before running this against any database that already
holds real contact data, write and run a one-off data migration that, for
every existing row, re-saves ``contact_email``/``contact_phone`` through the
ORM (so ``EncryptedString`` encrypts them) and calls
``Lead.set_contact_email()`` (so ``contact_email_hash`` gets populated) before
this migration's ``NOT NULL``-adjacent constraints would matter. As written,
existing plaintext rows are left as plain unencrypted text with a NULL
``contact_email_hash`` (unique constraint permits multiple NULLs, so this does
not fail on migrate) until the application next writes to them.

Revision ID: 0008_pii_gdpr
Revises: 0007_analytics
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_pii_gdpr"
down_revision: str | None = "0007_analytics"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Widen encrypted columns, add the blind-index hash, add GDPR columns."""
    # --- leads: encrypted contact fields + blind-index hash ---
    op.alter_column("leads", "contact_email", type_=sa.Text(), existing_nullable=True)
    op.alter_column("leads", "contact_phone", type_=sa.Text(), existing_nullable=True)

    op.add_column("leads", sa.Column("contact_email_hash", sa.String(64), nullable=True))
    op.create_index("ix_leads_contact_email_hash", "leads", ["contact_email_hash"])

    op.drop_constraint("uq_leads_contact_email", "leads", type_="unique")
    op.create_unique_constraint(
        "uq_leads_contact_email_hash", "leads", ["contact_email_hash"]
    )

    # --- leads: GDPR consent flag + erasure bookkeeping ---
    op.add_column(
        "leads",
        sa.Column("gdpr_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "leads", sa.Column("gdpr_consent_recorded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leads", sa.Column("gdpr_consent_source", sa.String(200), nullable=True))
    op.add_column(
        "leads", sa.Column("pii_erased_at", sa.DateTime(timezone=True), nullable=True)
    )

    # --- outreach_drafts: encrypted recipient fields (no blind index -- not
    # queried by equality anywhere in this codebase) ---
    op.alter_column("outreach_drafts", "recipient_email", type_=sa.Text(), existing_nullable=True)
    op.alter_column("outreach_drafts", "recipient_phone", type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    """Revert to plaintext-width columns and drop the new GDPR/hash columns."""
    op.alter_column(
        "outreach_drafts", "recipient_phone", type_=sa.String(50), existing_nullable=True
    )
    op.alter_column(
        "outreach_drafts", "recipient_email", type_=sa.String(320), existing_nullable=True
    )

    op.drop_column("leads", "pii_erased_at")
    op.drop_column("leads", "gdpr_consent_source")
    op.drop_column("leads", "gdpr_consent_recorded_at")
    op.drop_column("leads", "gdpr_consent")

    op.drop_constraint("uq_leads_contact_email_hash", "leads", type_="unique")
    op.create_unique_constraint("uq_leads_contact_email", "leads", ["contact_email"])
    op.drop_index("ix_leads_contact_email_hash", table_name="leads")
    op.drop_column("leads", "contact_email_hash")

    op.alter_column("leads", "contact_phone", type_=sa.String(50), existing_nullable=True)
    op.alter_column("leads", "contact_email", type_=sa.String(320), existing_nullable=True)
