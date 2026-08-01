"""Website audit and social review ORM models.

An audit is always tied to a lead and to the user who triggered it, so there is
a record of who asked for a third-party site to be crawled and when.

Social reviews store a human reviewer's structured observations. No scraped
platform content is persisted -- see :mod:`app.services.social_review`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.services.audit_scoring import FindingCategory, Severity
from app.services.social_review import Platform, PostingCadence


class AuditStatus(str, enum.Enum):
    """Lifecycle of an audit run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROBOTS_BLOCKED = "robots_blocked"


class WebsiteAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One website audit run against a prospect's site."""

    __tablename__ = "website_audits"
    __table_args__ = (
        CheckConstraint(
            "health_score IS NULL OR (health_score >= 0.0 AND health_score <= 1.0)",
            name="ck_website_audits_health_score_range",
        ),
        CheckConstraint(
            "pages_crawled >= 0 AND links_checked >= 0 AND broken_link_count >= 0",
            name="ck_website_audits_counts_nonneg",
        ),
        Index("ix_website_audits_lead_created", "lead_id", "created_at"),
        Index("ix_website_audits_status", "status"),
    )

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[AuditStatus] = mapped_column(
        SAEnum(AuditStatus, name="audit_status"), nullable=False, default=AuditStatus.PENDING
    )

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lighthouse category scores, 0.0-1.0.
    performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    seo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    accessibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_practices_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mobile_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Direct checks.
    ssl_valid: Mapped[bool | None] = mapped_column(nullable=True)
    ssl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contact_form_found: Mapped[bool | None] = mapped_column(nullable=True)
    contact_form_reachable: Mapped[bool | None] = mapped_column(nullable=True)

    pages_crawled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    links_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broken_link_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    robots_blocked: Mapped[bool] = mapped_column(nullable=False, default=False)

    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list["AuditFinding"]] = relationship(
        back_populates="audit", cascade="all, delete-orphan"
    )
    report: Mapped["AuditReport | None"] = relationship(
        back_populates="audit", cascade="all, delete-orphan", uselist=False
    )


class AuditFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One issue discovered during an audit."""

    __tablename__ = "audit_findings"
    __table_args__ = (
        UniqueConstraint("audit_id", "code", name="uq_audit_findings_audit_code"),
        Index("ix_audit_findings_audit_severity", "audit_id", "severity"),
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("website_audits.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[FindingCategory] = mapped_column(
        SAEnum(FindingCategory, name="finding_category"), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, name="finding_severity"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Service this finding points at, resolved by the report agent.
    mapped_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    audit: Mapped["WebsiteAudit"] = relationship(back_populates="findings")


class SocialProfileReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A human reviewer's assessment of one public social profile.

    Contains only the reviewer's structured observations. No platform content
    is scraped or stored.
    """

    __tablename__ = "social_profile_reviews"
    __table_args__ = (
        UniqueConstraint("lead_id", "platform", name="uq_social_reviews_lead_platform"),
        CheckConstraint(
            "completeness_score >= 0.0 AND completeness_score <= 1.0",
            name="ck_social_reviews_score_range",
        ),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[Platform] = mapped_column(
        SAEnum(Platform, name="social_platform"), nullable=False
    )
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    profile_exists: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_profile_image: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_cover_image: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_description: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_website_link: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_contact_details: Mapped[bool] = mapped_column(nullable=False, default=False)
    cadence: Mapped[PostingCadence] = mapped_column(
        SAEnum(PostingCadence, name="posting_cadence"),
        nullable=False,
        default=PostingCadence.NONE,
    )
    follower_band: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    completeness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The LLM-generated business-friendly summary of an audit.

    A report is a document, not an outreach message. It is never sent by this
    system -- turning one into an email goes through the pending_review /
    approved gate in AGENTS.md section 8 like any other outreach.
    """

    __tablename__ = "audit_reports"
    __table_args__ = (
        UniqueConstraint("audit_id", name="uq_audit_reports_audit_id"),
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("website_audits.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )

    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)

    generated_by_agent: Mapped[str] = mapped_column(String(100), nullable=False)
    used_fallback: Mapped[bool] = mapped_column(nullable=False, default=False)
    social_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    audit: Mapped["WebsiteAudit"] = relationship(back_populates="report")
