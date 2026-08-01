"""Places discovery staging tables.

Adds place_searches and place_candidates, and extends lead_source with
'google_places'.

Only place_id (indefinite) and latitude/longitude (30-day TTL) are stored from
the Places API. See app/db/models/place.py for the compliance rationale.

Revision ID: 0002_places
Revises: 0001_initial
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_places"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None

#: create_type=False -- explicitly created via _create_enum_type() below.
#: Uses postgresql.ENUM, not the generic sa.Enum -- see 0001_initial's
#: comment for why (the generic type's create_type flag was not reliably
#: suppressing the automatic CREATE TYPE emitted for an enum column).
candidate_status = postgresql.ENUM(
    "new", "reviewed", "promoted", "rejected", name="candidate_status", create_type=False
)


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
    """Create the discovery tables and extend the lead source enum."""
    op.execute("ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'google_places'")

    _create_enum_type("candidate_status", ["new", "reviewed", "promoted", "rejected"])

    op.create_table(
        "place_searches",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("industry", sa.String(200), nullable=False),
        sa.Column("postal_code", sa.String(20), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("radius_meters", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="google_places"),
        sa.Column("executed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["executed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("radius_meters > 0", name="ck_place_searches_radius_positive"),
        sa.CheckConstraint("result_count >= 0", name="ck_place_searches_result_count_nonneg"),
    )
    op.create_index("ix_place_searches_postal_code", "place_searches", ["postal_code"])
    op.create_index("ix_place_searches_fingerprint", "place_searches", ["fingerprint"])
    op.create_index(
        "ix_place_searches_fingerprint_executed", "place_searches", ["fingerprint", "executed_at"]
    )

    op.create_table(
        "place_candidates",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        # Storable indefinitely - exempt from Places caching restrictions.
        sa.Column("place_id", sa.String(255), nullable=False),
        # Storable for at most 30 days - nulled by the expiry sweeper.
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("coordinates_expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="google_places"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", candidate_status, nullable=False, server_default="new"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("lead_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["search_id"], ["place_searches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("place_id", name="uq_place_candidates_place_id"),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_place_candidates_latitude_range",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_place_candidates_longitude_range",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_place_candidates_confidence_range",
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_place_candidates_coordinates_paired",
        ),
    )
    op.create_index("ix_place_candidates_place_id", "place_candidates", ["place_id"])
    op.create_index(
        "ix_place_candidates_status_discovered", "place_candidates", ["status", "discovered_at"]
    )
    op.create_index(
        "ix_place_candidates_coordinates_expire", "place_candidates", ["coordinates_expire_at"]
    )


def downgrade() -> None:
    """Drop the discovery tables.

    Note:
        PostgreSQL cannot remove a value from an enum type, so the
        'google_places' lead_source value is intentionally left in place.
    """
    op.drop_table("place_candidates")
    op.drop_table("place_searches")
    _drop_enum_type("candidate_status")
