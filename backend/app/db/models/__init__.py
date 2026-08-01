"""ORM model registry.

Importing this package registers every model on :class:`app.db.base.Base`'s
metadata, which Alembic autogenerate depends on.
"""

from app.db.models.alert_log import AlertLog
from app.db.models.analytics import EmailOpenEvent, PromptVersion
from app.db.models.audit import (
    AuditFinding,
    AuditReport,
    AuditStatus,
    SocialProfileReview,
    WebsiteAudit,
)
from app.db.models.cost import ApiCostEvent, CostProvider
from app.db.models.deliverability import DeliverabilityCheck
from app.db.models.email_enrichment import EmailEnrichmentAttempt
from app.db.models.knowledge_base import (
    ChunkSourceType,
    KnowledgeChunk,
    PortfolioItem,
    PricingModel,
    Service,
)
from app.db.models.lead import Lead, LeadSource, LeadStatus
from app.db.models.lead_score import LeadScore
from app.db.models.meeting import Meeting, MeetingStatus
from app.db.models.outreach import (
    BounceEvent,
    DailySendCounter,
    DraftStatus,
    OutreachAuditLog,
    OutreachDraft,
    SuppressionEntry,
    SuppressionReason,
)
from app.db.models.pipeline import (
    CallCenterCard,
    InboundChannel,
    InboundMessage,
    PipelineEvent,
)
from app.db.models.place import CandidateStatus, PlaceCandidate, PlaceSearch
from app.db.models.signal import LeadSignal, SignalCheckpoint, SignalType
from app.db.models.user import APPROVER_ROLES, User, UserRole
from app.db.models.warmup import WarmupSchedule

__all__ = [
    "APPROVER_ROLES",
    "AlertLog",
    "ApiCostEvent",
    "AuditFinding",
    "AuditReport",
    "AuditStatus",
    "BounceEvent",
    "CallCenterCard",
    "CandidateStatus",
    "ChunkSourceType",
    "CostProvider",
    "DailySendCounter",
    "DeliverabilityCheck",
    "DraftStatus",
    "EmailEnrichmentAttempt",
    "EmailOpenEvent",
    "InboundChannel",
    "InboundMessage",
    "KnowledgeChunk",
    "Lead",
    "LeadScore",
    "LeadSignal",
    "LeadSource",
    "LeadStatus",
    "Meeting",
    "MeetingStatus",
    "OutreachAuditLog",
    "OutreachDraft",
    "PipelineEvent",
    "PlaceCandidate",
    "PlaceSearch",
    "PortfolioItem",
    "PricingModel",
    "PromptVersion",
    "Service",
    "SignalCheckpoint",
    "SignalType",
    "SocialProfileReview",
    "SuppressionEntry",
    "SuppressionReason",
    "User",
    "UserRole",
    "WarmupSchedule",
    "WebsiteAudit",
]
