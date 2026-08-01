"""Lead ORM model.

A lead is a prospective client discovered by a research agent or entered
manually. ``confidence_score`` expresses how confident the discovering agent is
that the lead is a genuine, well-qualified prospect.

See AGENTS.md section 9.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EncryptedString
from app.services.email_enrichment import EmailSource
from app.services.outreach_policy import CampaignType
from app.services.pipeline import PipelineStage
from app.services.pii import email_blind_index


class LeadSource(str, enum.Enum):
    """Where a lead originated."""

    MANUAL = "manual"
    WEB_RESEARCH = "web_research"
    LINKEDIN = "linkedin"
    REFERRAL = "referral"
    INBOUND_FORM = "inbound_form"
    CSV_IMPORT = "csv_import"
    PARTNER = "partner"
    #: Discovered via Google Places. The lead's contact fields must come from a
    #: source that permits storage -- Places content may not be copied here.
    GOOGLE_PLACES = "google_places"


class LeadStatus(str, enum.Enum):
    """Position of a lead in the BD pipeline."""

    NEW = "new"
    ENRICHING = "enriching"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    RESPONDED = "responded"
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A prospective client company or contact."""

    __tablename__ = "leads"
    __table_args__ = (
        # The unique constraint lives on the blind-index hash, not on the
        # (now encrypted, non-deterministic) contact_email column itself --
        # see app/db/types.py::EncryptedString and app/services/pii.py.
        UniqueConstraint("contact_email_hash", name="uq_leads_contact_email_hash"),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_leads_confidence_score_range",
        ),
        Index("ix_leads_status_created_at", "status", "created_at"),
        Index("ix_leads_category_status", "category", "status"),
        #: Backs app.services.campaign_followup_scanner's scan query, which
        #: filters candidate leads by pipeline_stage and groups by
        #: campaign_type to look up the right cadence.
        Index("ix_leads_campaign_type_pipeline_stage", "campaign_type", "pipeline_stage"),
    )

    # Identity
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Contact fields
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    #: Encrypted at rest (Fernet, via EncryptedString). NEVER query this
    #: column with an equality filter -- ciphertext is non-deterministic, so
    #: use contact_email_hash instead (see set_contact_email() below).
    contact_email: Mapped[str | None] = mapped_column(EncryptedString(320), nullable=True)
    #: Deterministic HMAC-SHA256 of the normalized email, kept in sync with
    #: contact_email by set_contact_email(). Backs the unique constraint and
    #: every equality lookup (duplicate-lead detection, unsubscribe, bounce
    #: webhooks) that used to run directly against the plaintext column.
    contact_email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Encrypted at rest. No blind index -- nothing in this codebase looks a
    #: lead up by phone number today; add one the same way if that changes.
    contact_phone: Mapped[str | None] = mapped_column(EncryptedString(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: BCP-47 language code detected from the lead's website or country, e.g.
    #: 'es', 'fr', 'zh'. Set asynchronously by the Celery
    #: detect_and_store_language task; None means unknown (draft generator
    #: falls back to English). Use :attr:`effective_language` to read the
    #: resolved value, which prefers language_override when set.
    detected_language: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )
    #: Manual override for detected_language. Wins over detection when set.
    #: Allows a rep to correct a mis-detected language without re-running
    #: detection, e.g. for a bilingual business where the website lang attr
    #: does not match the contact's preferred language.
    language_override: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Provenance and scoring
    source: Mapped[LeadSource] = mapped_column(
        SAEnum(LeadSource, name="lead_source"), nullable=False, default=LeadSource.MANUAL
    )
    source_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Pipeline
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="lead_status"), nullable=False, default=LeadStatus.NEW
    )
    #: Conversation-stage pipeline from app/services/pipeline.py -- distinct
    #: from `status` above. `status` tracks BD-process state (enriching,
    #: qualified, disqualified, ...); `pipeline_stage` tracks reply-driven
    #: conversation progress (New/Contacted/Interested/Hot/Converted/Lost) and
    #: is what drives call-center card generation on entering Hot. The two are
    #: kept in sync only at the terminal edges (Converted -> WON, pipeline
    #: Lost -> status LOST) by app/services/pipeline_transitions.py; they are
    #: not merged into one field because they answer different questions and
    #: a rep should never have to guess which "hot" or "lost" a report means.
    pipeline_stage: Mapped[PipelineStage] = mapped_column(
        SAEnum(PipelineStage, name="pipeline_stage"),
        nullable=False,
        default=PipelineStage.NEW,
    )
    #: Which kind of relationship this lead is being pursued under -- see
    #: app/services/outreach_policy.py::CampaignType. Drives draft tone and
    #: follow-up cadence (app/services/campaign_cadence.py,
    #: app/services/campaign_followup_scanner.py). Defaults to COLD, matching
    #: this codebase's behavior for every lead created before this field
    #: existed: every draft was written as a first cold open.
    campaign_type: Mapped[CampaignType] = mapped_column(
        SAEnum(CampaignType, name="campaign_type"),
        nullable=False,
        default=CampaignType.COLD,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Compliance -- feeds the ComplianceRisk gate in app/services/lead_scoring.py.
    #: Explicit suppression flag. True means this lead must never be
    #: contacted -- set on an opt-out, a legal objection, or a suppression
    #: list match. This is the hard gate input; it is deliberately a separate
    #: field from `status` so "not a good fit" (DISQUALIFIED) can never be
    #: confused with "may not be contacted" (do_not_contact).
    do_not_contact: Mapped[bool] = mapped_column(nullable=False, default=False)
    do_not_contact_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: Lawful basis recorded for holding/contacting this lead's data, e.g.
    #: "consent", "legitimate_interest", "public_task". Null means undocumented.
    consent_basis: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: Whether this lead gave opt-in permission for business-initiated
    #: WhatsApp messages. Meta's WhatsApp Business Messaging Policy requires
    #: this before any such message, so it hard-gates WhatsApp draft
    #: generation -- see app/services/outreach_policy.py.
    whatsapp_opt_in: Mapped[bool] = mapped_column(nullable=False, default=False)
    whatsapp_opt_in_source: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # GDPR
    #: Simple boolean consent flag, distinct from `consent_basis` above.
    #: `consent_basis` records WHICH of GDPR Article 6's six lawful bases
    #: applies (consent is only one of them -- legitimate interest, a legal
    #: obligation, etc. are others); `gdpr_consent` is the specific yes/no of
    #: "did this person affirmatively opt in", requested separately because a
    #: lead can be lawfully processed under a different basis without ever
    #: having given consent specifically.
    gdpr_consent: Mapped[bool] = mapped_column(nullable=False, default=False)
    gdpr_consent_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gdpr_consent_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Set once an Article 17 erasure request has been fulfilled (see
    #: app/api/v1/privacy.py). Non-null is the durable record that PII on this
    #: row was scrubbed and why the fields below may be empty even though the
    #: row itself is retained (the row ID is kept so foreign keys -- audits,
    #: drafts, pipeline events -- don't dangle or silently disappear).
    pii_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Email enrichment (app/services/email_enrichment.py, email_discovery.py,
    # email_enrichment_scanner.py) -- provenance and trust level of
    # contact_email. Defaults preserve today's behavior for every existing
    # write path (manual create/update, Places candidate promotion): a
    # human-supplied email is MANUAL/confidence 1.0/verified True, exactly as
    # if this feature didn't exist. Only the new enrichment chain sets
    # WEBSITE_CONTACT_PAGE/PATTERN_GUESS with verified=False.
    contact_email_source: Mapped[EmailSource] = mapped_column(
        SAEnum(EmailSource, name="email_source"), nullable=False, default=EmailSource.MANUAL
    )
    contact_email_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: True means this address is trusted enough to draft/send to. False --
    #: only possible for an enrichment-sourced email -- hard-blocks email
    #: draft generation the same way a missing address does (see
    #: app/services/outreach_policy.py::assess_email) until a human confirms
    #: it via POST /leads/{id}/email/verify.
    contact_email_verified: Mapped[bool] = mapped_column(nullable=False, default=True)

    @property
    def effective_language(self) -> str | None:
        """Resolved language for this lead, used by the draft generator.

        Returns ``language_override`` when it is set (a human correction wins
        over automated detection), otherwise ``detected_language``. ``None``
        means no language has been determined and the draft generator will
        write in English.

        Returns:
            A BCP-47 code (e.g. ``"es"``), or ``None``.
        """
        return self.language_override or self.detected_language

    def set_contact_email(
        self,
        email: str | None,
        *,
        source: EmailSource = EmailSource.MANUAL,
        confidence_score: float | None = None,
        verified: bool = True,
    ) -> None:
        """Set contact_email and keep its blind-index hash and provenance in sync.

        Every write path (create, update, candidate promotion, enrichment)
        must go through this rather than assigning ``contact_email``
        directly, or the hash backing the unique constraint and every lookup
        will drift out of sync with the encrypted value.

        Args:
            email: The new email address, or None to clear it.
            source: Where this email came from. Defaults to MANUAL so every
                existing call site (which knows nothing about enrichment)
                keeps behaving exactly as before.
            confidence_score: 0.0-1.0 trust score, only meaningful for an
                enrichment-sourced email. None for a manually-entered one.
            verified: Whether this address is trusted enough to draft/send
                to. Defaults to True -- a human typing in an email is
                implicitly asserting it's correct. The enrichment scanner is
                the only caller that passes False.
        """
        self.contact_email = email
        self.contact_email_hash = email_blind_index(email) if email else None
        self.contact_email_source = source if email else EmailSource.MANUAL
        self.contact_email_confidence = confidence_score if email else None
        self.contact_email_verified = verified if email else True

    def is_contactable(self) -> bool:
        """Whether the lead has at least one usable outreach channel.

        Note:
            A True result does NOT authorize sending anything. All outreach
            still requires an approved draft (AGENTS.md section 8).

        Returns:
            True if an email address or LinkedIn URL is present.
        """
        return bool(self.contact_email or self.linkedin_url)
