"""Collapse bd_manager/bd_rep into a single 'sales' role (RBAC: admin/sales/viewer).

The four-role model (admin, bd_manager, bd_rep, viewer) becomes three
(admin, sales, viewer). ``bd_manager`` and ``bd_rep`` both map to ``sales``;
the manager-only outreach-approval distinction is gone -- every ``sales``
user can now approve and send (see APPROVER_ROLES in
app/db/models/user.py). ``viewer`` is unchanged and is now actually enforced
as read-only by app.api.deps.require_write_access, which did not exist
before this change.

PostgreSQL cannot drop or rename enum labels out of a live type in one step
in a way that is both safe and portable across server versions, so this
follows the standard workaround: build a new enum type, migrate the column
over with an explicit value mapping, then drop the old type and rename the
new one into its place.

Revision ID: 0009_rbac_roles
Revises: 0008_pii_gdpr
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_rbac_roles"
down_revision: str | None = "0008_pii_gdpr"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Swap the 4-value user_role enum for the 3-value admin/sales/viewer one."""
    op.execute("CREATE TYPE user_role_new AS ENUM ('admin', 'sales', 'viewer')")

    op.execute("ALTER TABLE users ADD COLUMN role_new user_role_new")
    op.execute(
        """
        UPDATE users SET role_new = CASE
            WHEN role::text = 'admin' THEN 'admin'::user_role_new
            WHEN role::text IN ('bd_manager', 'bd_rep') THEN 'sales'::user_role_new
            ELSE 'viewer'::user_role_new
        END
        """
    )

    op.execute("ALTER TABLE users DROP COLUMN role")
    op.execute("ALTER TABLE users RENAME COLUMN role_new TO role")
    op.execute("ALTER TABLE users ALTER COLUMN role SET NOT NULL")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'sales'")

    op.execute("DROP TYPE user_role")
    op.execute("ALTER TYPE user_role_new RENAME TO user_role")


def downgrade() -> None:
    """Revert to the 4-value enum.

    Lossy: there is no record of whether a 'sales' user used to be
    bd_manager or bd_rep, so every 'sales' row downgrades to 'bd_rep' (the
    more restrictive of the two) rather than guessing upward into
    approval-capable bd_manager.
    """
    op.execute("CREATE TYPE user_role_old AS ENUM ('admin', 'bd_manager', 'bd_rep', 'viewer')")

    op.execute("ALTER TABLE users ADD COLUMN role_old user_role_old")
    op.execute(
        """
        UPDATE users SET role_old = CASE
            WHEN role::text = 'admin' THEN 'admin'::user_role_old
            WHEN role::text = 'sales' THEN 'bd_rep'::user_role_old
            ELSE 'viewer'::user_role_old
        END
        """
    )

    op.execute("ALTER TABLE users DROP COLUMN role")
    op.execute("ALTER TABLE users RENAME COLUMN role_old TO role")
    op.execute("ALTER TABLE users ALTER COLUMN role SET NOT NULL")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'bd_rep'")

    op.execute("DROP TYPE user_role")
    op.execute("ALTER TYPE user_role_old RENAME TO user_role")
