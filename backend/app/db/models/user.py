"""User ORM model.

Identity is owned by the external provider (Clerk or Auth.js). This table is a
local mirror keyed on the provider's stable subject claim, so that foreign keys
such as ``approved_by`` on outreach drafts point at a row we control.

No password hashes are stored here -- credentials never touch this service.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(str, enum.Enum):
    """Authorization role for a user.

    Three tiers: ``ADMIN`` (full access, including managing prompt versions
    and reading cost/budget status), ``SALES`` (everything BD work requires --
    discovery, audits, scoring, drafting, and approving/sending outreach),
    and ``VIEWER`` (read-only everywhere; see
    ``app.api.deps.require_write_access``).

    Note: this collapses what used to be two separate BD roles
    (``bd_manager``, who alone could approve outreach, and ``bd_rep``, who
    could only draft) into one ``SALES`` role that can do both. That
    manager-only approval gate no longer exists -- every SALES user can
    approve and send. If you need that distinction back, reintroduce it as a
    permission on the user row rather than a fourth role, since RBAC here is
    deliberately kept to three tiers.
    """

    ADMIN = "admin"
    SALES = "sales"
    VIEWER = "viewer"


#: Roles permitted to approve/send outreach drafts (AGENTS.md section 8).
APPROVER_ROLES: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.SALES})

#: Roles permitted to create/modify anything (as opposed to VIEWER's
#: read-only access). See app.api.deps.require_write_access.
WRITE_ROLES: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.SALES})


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A local mirror of an identity-provider user."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider_subject", name="uq_users_provider_subject"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="clerk")
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False, default=UserRole.SALES
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def can_write(self) -> bool:
        """Whether this user may create or modify anything (vs. read-only).

        Returns:
            True for active users holding a write-capable role. VIEWER always
            returns False, deactivated users always return False regardless
            of role.
        """
        return self.is_active and self.role in WRITE_ROLES

    def can_approve_outreach(self) -> bool:
        """Whether this user may approve outreach drafts for sending.

        Returns:
            True for active users holding an approver role.
        """
        return self.is_active and self.role in APPROVER_ROLES
