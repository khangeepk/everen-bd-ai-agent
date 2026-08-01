"""Application settings loaded from environment variables.

All secrets are read from ``.env`` (or the process environment) via Pydantic
``BaseSettings``. No module in this codebase may read ``os.environ`` directly
for secret values -- import :data:`settings` from here instead.

See AGENTS.md section 5.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed application configuration sourced from the environment.

    Attributes:
        app_env: Deployment environment name.
        secret_key: Application-level signing secret.
        database_url: Async SQLAlchemy DSN for PostgreSQL.
        openai_api_key: API key used for embeddings and completions.
        embedding_model: Embedding model identifier.
        embedding_dimension: Vector width, must match ``embedding_model``.
        auth_jwks_url: JWKS endpoint of the identity provider (Clerk/Auth.js).
        auth_issuer: Expected ``iss`` claim on incoming tokens.
        auth_audience: Expected ``aud`` claim on incoming tokens.
        cors_origins: Explicit allowlist of browser origins.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "development"
    secret_key: str = "CHANGE_ME"
    log_level: str = "INFO"

    # PII encryption at rest (app.db.types.EncryptedString)
    #: Fernet key protecting contact_email/contact_phone and similar PII
    #: columns. The default below is a fixed, publicly-known dev-only
    #: placeholder (deterministically derived, not secret) so the app and
    #: test suite work out of the box -- it MUST be replaced with a real,
    #: privately generated key before storing any real person's data. Rotate
    #: by re-encrypting existing rows with the old key before switching.
    encryption_key: str = "E25xTJVA9vjWAdMy7inb-JtYfT_0u3pkfK15juhxPt8="

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/everen_db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # AI / LLM
    openai_api_key: str = "sk-REPLACE_ME"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    recommendation_model: str = "gpt-4o-mini"

    # Website audit
    pagespeed_api_key: str = "REPLACE_ME"
    #: Crawl bounds. Kept low deliberately -- the crawler fetches pages from
    #: third-party sites and politeness is what keeps that defensible.
    audit_crawl_max_pages: int = 25
    audit_crawl_max_depth: int = 2
    audit_crawl_delay_seconds: float = 1.0

    # Lead scoring
    #: Deal-size range used to normalize the Revenue component. Defaults match
    #: the seed service catalogue's price span (managed retainer floor to
    #: AI agent integration ceiling). Tune to actual pricing before relying on
    #: the Revenue score for prioritization.
    lead_score_revenue_scale_min: float = 5000.0
    lead_score_revenue_scale_max: float = 180000.0

    # Google Places (lead discovery)
    google_places_api_key: str = "REPLACE_ME"
    #: Retention for cached Places coordinates. Google permits at most 30 days
    #: (Service Specific Terms 10.3); values above that are rejected by
    #: app.services.places_policy.coordinate_expiry.
    places_coordinate_ttl_days: int = 30
    #: Soft-launch / dev safety rail, independent of the dollar-based cost
    #: guard below: when True, app.services.places.PlaceDiscoveryService
    #: refuses to make more than places_test_mode_max_requests Places API
    #: calls for the lifetime of the process, regardless of budget headroom.
    #: The point is to keep a test batch trivially inside Google Maps
    #: Platform's monthly free credit without having to trust a dollar
    #: estimate to be exactly right. Off by default so production behavior is
    #: unchanged unless explicitly opted into.
    places_test_mode: bool = False
    #: Requests allowed per process while places_test_mode is True. At up to
    #: 20 results per searchText call, 10 requests caps a test run at ~200
    #: raw candidates -- enough for a real soft-launch batch, comfortably
    #: inside the free monthly credit even before the dollar cost guard below
    #: is considered.
    places_test_mode_max_requests: int = 10

    # API cost guard (app.services.cost_guard / cost_tracking) -- daily
    # budget caps and the fraction at which a WARNING alert fires. Places and
    # OpenAI chat-completion calls are guarded; PageSpeed (free) and OpenAI
    # embeddings (comparatively negligible cost) are not -- see the module
    # docstring on app.services.cost_guard for why.
    cost_guard_daily_budget_places_usd: float = 20.0
    cost_guard_daily_budget_openai_usd: float = 20.0
    cost_guard_alert_threshold: float = 0.8

    # Auth
    auth_jwks_url: str = "https://REPLACE_ME/.well-known/jwks.json"
    auth_issuer: str = "https://REPLACE_ME"
    auth_audience: str = "everen-bd-agent"
    auth_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    auth_jwks_cache_seconds: int = 3600

    # Email / Outreach (human-gated -- see AGENTS.md section 8)
    sendgrid_api_key: str = "SG.REPLACE_ME"
    #: When True, every send sets SendGrid's mail_settings.sandbox_mode.enable,
    #: which makes SendGrid fully validate and accept the request (so the
    #: whole send-gate/quota/suppression pipeline is genuinely exercised) but
    #: never actually deliver the message or count against sending limits/
    #: reputation. See app.services.email_sender.SendGridEmailSender. Off by
    #: default -- this must be a deliberate opt-in for a test/soft-launch
    #: window, never a silent default that could mask real sends not going out.
    sendgrid_sandbox_mode: bool = False
    outreach_from_email: str = "bd@yourdomain.com"
    outreach_from_name: str = "Everen Techno"
    outreach_reply_to: str | None = None
    outreach_company_name: str = "Everen Techno"
    #: REQUIRED by CAN-SPAM in every commercial email: a real street address,
    #: a USPS-registered PO Box, or a registered private mailbox. The
    #: placeholder below is rejected at draft time by app/services/canspam.py,
    #: so no email can be drafted until this is set to a real address.
    outreach_physical_address: str = "REPLACE_ME"
    #: Public base URL used to build one-click unsubscribe links.
    outreach_public_base_url: str = "https://REPLACE_ME"
    #: Daily send cap per channel. Primarily a deliverability control -- cold
    #: domains that ramp volume quickly get filtered -- and secondarily a
    #: blast-radius limit if draft generation ever produces bad copy.
    outreach_daily_send_limit: int = 50

    # Deliverability checklist (SPF/DKIM/DMARC + warmup + readiness report)
    #: Domain the deliverability checker inspects. Defaults to the domain
    #: half of outreach_from_email if left unset (see
    #: app.services.deliverability_checker), so a fresh install with no
    #: extra configuration still checks the right domain.
    deliverability_check_domain: str | None = None
    #: DKIM selectors to try when looking for a domain-authentication TXT
    #: (or CNAME-delegated, e.g. SendGrid's default automated security
    #: setup) record at "<selector>._domainkey.<domain>". There is no way to
    #: discover the real selector via DNS alone -- unlike SPF/DMARC, which
    #: live at well-known record names -- so this is a best-effort guess
    #: list, not a guarantee. "s1"/"s2" are SendGrid's own default pair.
    #: Override with the selector(s) shown in your SendGrid domain
    #: authentication setup if they differ.
    sendgrid_dkim_selectors: list[str] = Field(default_factory=lambda: ["s1", "s2"])

    # Calendar booking (app.services.google_calendar, booking_slots,
    # booking_token; app.api.v1.booking) -- a single shared "sales calendar"
    # model: one Google account's OAuth refresh token, obtained once by an
    # admin outside this application (e.g. via Google's OAuth playground with
    # the https://www.googleapis.com/auth/calendar scope), not a per-rep
    # "connect your calendar" flow. Every booking link checks and books
    # against this one calendar.
    google_calendar_client_id: str = "REPLACE_ME"
    google_calendar_client_secret: str = "REPLACE_ME"
    google_calendar_refresh_token: str = "REPLACE_ME"
    #: Which calendar on that account to check/book. "primary" is that
    #: account's default calendar; set to a specific calendar's id (its
    #: email-address-shaped identifier) to use a dedicated shared calendar
    #: instead of a real person's primary one.
    google_calendar_id: str = "primary"
    #: IANA zone the working-hours settings below are defined in. Booking
    #: links always compute slots in this zone regardless of the prospect's
    #: own timezone -- there is no per-prospect timezone detection here.
    booking_timezone: str = "America/Chicago"
    booking_slot_duration_minutes: int = 30
    #: Working-hours window slots are offered within, local to
    #: booking_timezone, weekdays only (Sat/Sun are never offered).
    booking_working_hour_start: int = 9
    booking_working_hour_end: int = 17
    #: How many calendar days ahead a booking link offers slots for.
    booking_lookahead_days: int = 10
    #: Don't offer a slot starting sooner than this from "now" -- gives the
    #: rep some notice rather than a prospect booking a call 5 minutes out.
    booking_min_lead_time_minutes: int = 60
    #: How long a generated booking link stays valid before
    #: app.services.booking_token.verify_booking_token rejects it. A link
    #: left open indefinitely would be a standing, unauthenticated way to put
    #: events on the shared calendar.
    booking_link_expiry_days: int = 21

    # Error alerting (Phase E) -- app.main wires sentry_sdk.init() only when
    # this is non-blank, so leaving it unset is a true no-op, not a
    # misconfiguration. Get a DSN by creating a free Sentry project (see
    # DEPLOYMENT.md).
    sentry_dsn: str = ""
    #: Fraction of requests traced for performance monitoring (0.0-1.0). Kept
    #: low by default -- this is an internal low-traffic tool, so full
    #: tracing costs little, but there is no reason to default to 1.0 and
    #: burn through Sentry's free-tier transaction quota on health checks.
    sentry_traces_sample_rate: float = 0.1

    # n8n health-monitor webhook authentication
    #: Shared secret validated by app.api.deps.verify_webhook_secret on the
    #: POST /api/v1/outreach/pause endpoint. When blank (the default),
    #: webhook auth is disabled -- acceptable in development / CI but MUST
    #: be set to a strong random value in staging and production.
    #: Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    n8n_webhook_secret: str = ""

    # SendGrid Signed Event Webhook signature verification (Phase 25)
    #: ECDSA public key from SendGrid Event Webhook settings.
    #: In production (app_env="production"), missing/invalid key causes fail-closed 401.
    sendgrid_webhook_verification_key: str = ""

    # Language detection (app.services.language_detection)
    #: BCP-47 codes the outreach agent may draft in. Detected languages not in
    #: this set fall back to English with a reviewer warning on the draft.
    #: Comma-separated in the environment, parsed by _split_csv.
    #: Default covers the major LLM-supported languages; trim to the markets
    #: you actually operate in.
    outreach_supported_languages: list[str] = Field(
        default_factory=lambda: [
            "en", "es", "fr", "de", "pt", "ar", "zh", "zh-TW", "zh-HK",
            "ja", "ko", "it", "nl", "ru", "hi", "tr", "pl", "sv",
            "nb", "da", "fi", "he", "th", "vi", "id", "ms", "el",
            "cs", "ro", "hu", "uk",
        ]
    )

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    @field_validator(
        "cors_origins", "auth_algorithms", "sendgrid_dkim_selectors",
        "outreach_supported_languages", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated strings for list-valued settings.

        Args:
            value: Raw value from the environment.

        Returns:
            A list of trimmed strings when given a CSV string, else the value
            unchanged.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        """Whether the app is running in the production environment.

        Returns:
            True when ``app_env`` is ``"production"``.
        """
        return self.app_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    Returns:
        The process-wide :class:`Settings` instance.
    """
    loaded = Settings()
    logger.info("Settings loaded", extra={"app_env": loaded.app_env})
    default_key = Settings.model_fields["encryption_key"].default
    if loaded.is_production and loaded.encryption_key == default_key:
        logger.error(
            "ENCRYPTION_KEY is still the default dev placeholder in a production "
            "environment. All PII encrypted with it is protected only by obscurity, "
            "not secrecy. Generate a real key and set ENCRYPTION_KEY before storing "
            "real contact data."
        )
    return loaded


settings = get_settings()
