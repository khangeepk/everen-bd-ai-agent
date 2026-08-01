"""Outreach draft generation agent.

Produces email, WhatsApp, and call-script drafts grounded in a lead's website
audit findings and its best-matching Everen Techno service.

THIS AGENT NEVER SENDS ANYTHING. Every method returns or persists a draft with
``status = PENDING_REVIEW``. There is no code path here that dispatches a
message, sets ``sent_at``, or moves a draft to ``APPROVED``. Sending happens
only through ``POST /api/v1/outreach/{draft_id}/send`` after a human approves.
See AGENTS.md section 8.

Grounding rules given to the LLM mirror the report agent's: only the supplied
findings and the supplied service may be referenced, and prices are quoted
only as stored. A deterministic fallback draft is produced on any LLM failure,
so an outage degrades wording rather than inventing claims about the
prospect's website.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.analytics import PromptVersion
from app.db.models.audit import AuditFinding, WebsiteAudit
from app.db.models.knowledge_base import Service
from app.db.models.lead import Lead
from app.services.ab_testing import PromptVariant, choose_variant
from app.services.audit_scoring import Severity
from app.services.canspam import (
    SenderIdentity,
    assemble_email_body,
    build_erasure_url,
    build_unsubscribe_url,
    make_erasure_token,
    make_unsubscribe_token,
    validate_subject,
)
from app.services.cost_guard import BudgetExceededError, CostProvider, estimate_openai_cost
from app.services.cost_tracking import enforce_budget_before_call, record_spend
from app.services.knowledge_base import KnowledgeBaseService
from app.services.outreach_policy import (
    CampaignType,
    ChannelDecision,
    LeadOutreachContext,
    OutreachChannel,
    assess_channel,
)
from app.services.reply_classification import ObjectionType

logger = logging.getLogger(__name__)

AGENT_NAME = "outreach-draft-agent-v1"

#: Findings at or above this severity are worth leading with in outreach.
_LEAD_WITH_SEVERITIES: frozenset[Severity] = frozenset({Severity.CRITICAL, Severity.HIGH})

#: How many findings to reference. More than this reads as a teardown rather
#: than an opener, and lands badly.
_MAX_FINDINGS_IN_DRAFT = 3

_EMAIL_SYSTEM_PROMPT = """You are writing a first cold outreach email from Everen Techno \
to a small business owner.

You will receive specific findings from an automated review of their website, \
and one Everen Techno service that addresses them.

Rules:
- Reference ONLY the findings supplied. Never invent a problem or a statistic.
- Mention ONLY the supplied service. Never invent a service or alter a price.
- Under 150 words. A business owner skims on a phone.
- Lead with one concrete, specific observation about THEIR site. No generic openers.
- Plain language, no jargon: "your site takes 6 seconds to load on a phone", \
not "your LCP is 6.1s".
- Do not be alarmist and do not imply the business is failing or incompetent.
- One clear, low-friction ask. Suggest a short call; do not push for a sale.
- Do NOT write a subject line, greeting boilerplate, signature, footer, \
unsubscribe text, or postal address. Those are added separately.
- Do not fabricate urgency or claim you have been trying to reach them.
"""

_WHATSAPP_SYSTEM_PROMPT = """You are drafting a WhatsApp message template for Everen Techno.

This will be submitted to Meta for template approval, so it must read as a \
legitimate business message.

Rules:
- Under 60 words. WhatsApp is a short-form channel.
- Reference ONLY the supplied finding and service.
- Conversational but professional. No marketing hype, no emoji spam.
- One clear question at the end.
- Do not fabricate a prior relationship or claim they contacted you first.
"""

_CALL_SYSTEM_PROMPT = """You are writing a phone call script for an Everen Techno BD rep.

Rules:
- Reference ONLY the supplied findings and service.
- Structure: opening (identify yourself and why you are calling), the specific \
observation, a question that invites them to talk, and a graceful exit if they \
are not interested.
- Write what the rep says. Mark pauses for the prospect's reply as [PAUSE].
- Under 200 words. Natural spoken language, not written prose.
- Include an explicit, respectful exit line. If the prospect is not interested, \
the rep thanks them and ends the call -- no rebuttal scripting.
"""

#: LinkedIn hard-caps a connection request's accompanying note at 300
#: characters (enforced by LinkedIn's own UI, not something this system can
#: change). Truncating server-side, defensively, is cheaper than trusting an
#: LLM instruction alone -- see generate_linkedin_content's docstring for
#: the same "don't just trust the prompt for something with a hard,
#: checkable constraint" posture already used for URL-stripping in
#: generate_booking_reply.
_LINKEDIN_CONNECTION_NOTE_MAX_CHARS = 300

_LINKEDIN_CONNECTION_SYSTEM_PROMPT = """You are writing a LinkedIn connection-request note \
from a rep at Everen Techno to a prospect.

You will receive specific findings from an automated review of their website, and one \
Everen Techno service that addresses them.

Rules:
- STRICT HARD LIMIT: 300 characters total, including spaces and punctuation. LinkedIn \
itself rejects anything longer -- this is not a style preference, it is a platform limit. \
Aim for comfortably under 300 so there is margin.
- Reference ONLY the findings supplied. Never invent a problem or a statistic.
- Do not mention price or pitch the service by name -- a connection note is for starting \
a relationship, not selling. A brief, vague hint at "how we help" is fine; a sales pitch \
is not.
- Plain, human, first-person voice -- like a real person reaching out, not a marketing \
message.
- No greeting boilerplate ("Dear", "Hi there"), no signature, no links. Just the note text.
- Do not fabricate a prior relationship, a mutual connection, or claim you've met before.
"""

_LINKEDIN_FOLLOWUP_SYSTEM_PROMPT = """You are writing the follow-up message a rep at \
Everen Techno will send on LinkedIn after a prospect accepts their connection request.

You will receive the same findings from an automated website review and the one Everen \
Techno service that addresses them.

Rules:
- Under 100 words. LinkedIn messages are read on mobile as often as desktop.
- Reference ONLY the findings and service supplied. Never invent a problem, a discount, \
or alter the price.
- Assume no reply has happened yet -- this is the first real message after connecting, \
not a reply to something the prospect said.
- Thank them for connecting, briefly, then get to one concrete observation and one \
low-friction ask (a short call). Do not open with a hard sales pitch.
- No greeting boilerplate, no signature block, no links -- LinkedIn messages read as \
direct chat, not email.
"""

_PRICE_OBJECTION_SYSTEM_PROMPT = """You are writing a reply from Everen Techno to a \
prospect who responded to earlier outreach with a question or concern about price.

You will receive the prospect's own reply, the findings from an automated review of \
their website, and the one Everen Techno service that addresses them (with its price \
range).

Rules:
- Reference ONLY the findings and service supplied. Never invent a problem, a \
discount, a payment plan, or alter the stated price range in any way.
- Address the price concern by reinforcing the value of what is included, using only \
the supplied service's summary -- do not simply repeat the price.
- Do not get defensive and do not apologize for the price.
- Offer one low-friction next step: a short call to scope exactly what they need, so \
the conversation can continue without you guessing at a number to lower to.
- Warm, direct, respectful of their time.
- Do NOT write a subject line, greeting boilerplate, signature, footer, unsubscribe \
text, or postal address. Those are added separately.
"""

_TIMING_OBJECTION_SYSTEM_PROMPT = """You are writing a reply from Everen Techno to a \
prospect who responded to earlier outreach saying now is not a good time.

You will receive the prospect's own reply, the findings from an automated review of \
their website, and the one Everen Techno service that addresses them.

Rules:
- Reference ONLY the findings and service supplied. Never invent a problem or alter \
the price.
- Explicitly respect their stated timeline. Do not push for an immediate call or sale.
- Offer a low-pressure way to stay in touch (e.g. checking back at a sensible later \
point) rather than asking them to commit to anything now.
- Keep it brief and genuinely no-pressure -- this should read as considerate, not as \
a disguised second pitch.
- Do NOT write a subject line, greeting boilerplate, signature, footer, unsubscribe \
text, or postal address. Those are added separately.
"""

_NOT_INTERESTED_YET_OBJECTION_SYSTEM_PROMPT = """You are writing a reply from Everen \
Techno to a prospect who responded to earlier outreach declining, without asking to \
stop being contacted or citing price or timing specifically.

You will receive the prospect's own reply, the findings from an automated review of \
their website, and the one Everen Techno service that addresses them.

Rules:
- Reference ONLY the findings and service supplied. Never invent a problem or alter \
the price.
- Acknowledge their decision respectfully -- do not argue with it or imply they are \
wrong.
- At most one more concrete, specific reason the finding might matter to them, only \
if it adds real value. Do not pile on additional problems.
- Leave the door open without being pushy: one clear, easy way to say yes if they \
change their mind, and make clear no reply is needed.
- This is a single considerate follow-up, not the start of a persistence campaign.
- Do NOT write a subject line, greeting boilerplate, signature, footer, unsubscribe \
text, or postal address. Those are added separately.
"""

_OBJECTION_SYSTEM_PROMPTS: dict[ObjectionType, str] = {
    ObjectionType.PRICE: _PRICE_OBJECTION_SYSTEM_PROMPT,
    ObjectionType.TIMING: _TIMING_OBJECTION_SYSTEM_PROMPT,
    ObjectionType.NOT_INTERESTED_YET: _NOT_INTERESTED_YET_OBJECTION_SYSTEM_PROMPT,
}

#: Appended to whichever base system prompt is in use (the hardcoded
#: default or an active PromptVersion override), so campaign_type shapes
#: tone without needing a separate prompt per (channel, campaign_type)
#: pairing. See app/services/outreach_policy.py::CampaignType.
_CAMPAIGN_TONE_NOTES: dict[CampaignType, str] = {
    CampaignType.COLD: (
        "Tone: this is the first message this business is receiving from Everen "
        "Techno. Introduce yourself and the observation with no assumption of "
        "familiarity."
    ),
    CampaignType.WARM: (
        "Tone: this lead came through a referral, an inbound enquiry, or has "
        "otherwise already engaged with Everen Techno. Write with a warmer, more "
        "familiar tone -- acknowledge the existing connection or interest rather "
        "than opening cold."
    ),
    CampaignType.RE_ENGAGEMENT: (
        "Tone: this lead was previously contacted and went quiet, or was previously "
        "lost/disqualified, and is being revisited now. Acknowledge the gap plainly "
        "(e.g. \"it's been a while\" or \"wanted to check back\"), keep it "
        "low-pressure, and do not write as if this is the first contact."
    ),
}

_BOOKING_REPLY_SYSTEM_PROMPT = """You are writing a reply from Everen Techno to a \
prospect who responded to earlier outreach asking to book a call or clearly expressing \
interest in talking further.

You will receive the prospect's own reply, the findings from an automated review of \
their website, and the one Everen Techno service that addresses them.

Rules:
- Reference ONLY the findings and service supplied. Never invent a problem or alter \
the price.
- This prospect has already said yes -- confirm briefly and move straight to \
scheduling. Do not re-pitch, re-sell, or re-explain the value proposition at length.
- Say that a scheduling link with available times follows this message, so they can \
pick whatever works for them. Do NOT write out a URL, a placeholder link, or any \
booking instructions yourself -- the real link is appended separately after you \
generate this text, exactly like the CAN-SPAM footer is appended to every email. \
Inventing or guessing at a link here would give the prospect a broken one.
- Warm, brief, and direct. This should read as "great, here's how to grab time," not \
another pitch.
- Do NOT write a subject line, greeting boilerplate, signature, footer, unsubscribe \
text, or postal address. Those are added separately.
"""

#: Defensive backstop for _BOOKING_REPLY_SYSTEM_PROMPT's "do not write a URL"
#: instruction. An LLM occasionally ignores such instructions and invents a
#: placeholder link anyway; since the real booking URL is always appended
#: after generation (see generate_booking_reply), any URL the model wrote
#: on its own would be a second, fake link sitting next to the real one --
#: confusing at best. This is stripped rather than trusted, the same
#: "don't trust the LLM with anything that must be correct" posture as
#: finalize_email_body's footer handling.
_URL_PATTERN = re.compile(r"https?://\S+")

_FOLLOW_UP_SYSTEM_PROMPT = """You are writing a follow-up message from Everen Techno to a \
prospect who received an earlier outreach message and has not yet replied.

You will receive the original findings from an automated review of their website, the \
one Everen Techno service that addresses them, and which follow-up number in the \
sequence this is.

Rules:
- Reference ONLY the findings and service supplied. Never invent a new problem, a \
discount, or alter the price.
- Do not repeat the previous message in full. Add a small amount of new value or a \
different angle on the same finding/service, or a brief, low-key check-in.
- Explicitly acknowledge this is a follow-up (e.g. "following up on my note last \
week") -- do not write as if this is the first contact.
- Each successive follow-up should read shorter and lower-pressure than the last, \
never more insistent.
- One clear, low-friction ask, or none at all if this is the last follow-up in the \
sequence -- a graceful final touch is fine.
- Do NOT write a subject line, greeting boilerplate, signature, footer, unsubscribe \
text, or postal address. Those are added separately.
"""


@dataclass
class DraftContent:
    """Generated content for one channel before persistence.

    Attributes:
        channel: The channel this content targets.
        subject: Subject line. Email only; None for other channels.
        body: The message body or script.
        used_fallback: True when the LLM was unavailable and the deterministic
            draft was used.
        warnings: Reviewer-facing concerns carried from the channel decision.
        draft_language: BCP-47 code the draft was written in, e.g. ``'es'``.
            ``None`` means English (the model's natural default) or the
            detected language was unsupported and fell back to English.
    """

    channel: OutreachChannel
    subject: str | None
    body: str
    used_fallback: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    #: The PromptVersion used to generate this content, if any active version
    #: existed for this agent+channel. None means the code-default hardcoded
    #: prompt was used.
    prompt_version_id: uuid.UUID | None = None
    #: Which side of an A/B split this content was assigned to, if the
    #: active prompt versions for this agent+channel formed an experiment.
    ab_variant: str | None = None
    #: LINKEDIN only: the follow-up message to send after the prospect
    #: accepts the connection request in ``body``. None for every other
    #: channel -- a LinkedIn draft is genuinely two independent pieces of
    #: text sent at two different times, not one message with an appendix,
    #: so it doesn't fit in ``body`` alone. See
    #: OutreachDraftAgent.generate_linkedin_content.
    linkedin_followup_body: str | None = None
    #: BCP-47 language the draft was written in (e.g. 'es', 'fr'). None means
    #: English or language was unsupported / undetected. Stored on the draft
    #: row at persistence time for analytics grouping.
    draft_language: str | None = None


@dataclass
class DraftGenerationResult:
    """Outcome of a draft generation request for one lead.

    Attributes:
        lead_id: The lead drafts were generated for.
        drafts: Generated content, one entry per eligible channel.
        skipped: Channel decisions that blocked generation, so the caller can
            explain why a channel produced nothing.
    """

    lead_id: uuid.UUID
    drafts: list[DraftContent] = field(default_factory=list)
    skipped: list[ChannelDecision] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Language resolution helpers
# ---------------------------------------------------------------------------

#: English display names for LLM system-prompt language instructions.
_LANGUAGE_NAMES: dict[str, str] = {
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "ar": "Arabic", "zh": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)",
    "zh-HK": "Chinese (Traditional)", "ja": "Japanese", "ko": "Korean",
    "it": "Italian", "nl": "Dutch", "ru": "Russian", "hi": "Hindi",
    "tr": "Turkish", "pl": "Polish", "sv": "Swedish", "nb": "Norwegian",
    "da": "Danish", "fi": "Finnish", "he": "Hebrew", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "el": "Greek",
    "cs": "Czech", "ro": "Romanian", "hu": "Hungarian", "uk": "Ukrainian",
}


def _resolve_draft_language(lead: "Lead", supported_languages: list[str]) -> str | None:  # noqa: F821
    """Return the BCP-47 code to draft in for this lead, or None for English.

    Reads ``lead.effective_language`` (override wins over detected). If the
    result is ``None``, English will be used by default. If the code is set
    but not in ``supported_languages``, the draft is still generated in
    English with a reviewer warning, so no draft is silently produced in a
    low-quality language the operator has not explicitly enabled.

    Args:
        lead: The lead to determine language for.
        supported_languages: List of BCP-47 codes from
            ``settings.outreach_supported_languages``.

    Returns:
        A BCP-47 code the LLM should draft in, or ``None`` for English.
    """
    lang = lead.effective_language
    if not lang:
        return None
    if lang not in supported_languages:
        logger.warning(
            "Detected language not in supported list; falling back to English",
            extra={"lead_id": str(lead.id), "detected_language": lang},
        )
        return None
    if lang == "en":
        return None  # English is the model's natural default; no extra instruction needed
    return lang


def build_lead_context(
    lead: Lead, *, email_suppressed: bool = False, hard_bounced: bool = False
) -> LeadOutreachContext:
    """Build the channel-eligibility context for a lead.

    Args:
        lead: The lead to describe.
        email_suppressed: Whether the lead's email is on the suppression list.
        hard_bounced: Whether the lead's email previously hard-bounced.

    Returns:
        The outreach context.
    """
    return LeadOutreachContext(
        do_not_contact=lead.do_not_contact,
        do_not_contact_reason=lead.do_not_contact_reason,
        contact_email=lead.contact_email,
        contact_phone=lead.contact_phone,
        linkedin_url=lead.linkedin_url,
        consent_basis=lead.consent_basis,
        country=lead.country,
        whatsapp_opt_in=lead.whatsapp_opt_in,
        email_suppressed=email_suppressed,
        hard_bounced=hard_bounced,
        contact_email_verified=lead.contact_email_verified,
    )


def sender_identity() -> SenderIdentity:
    """Build the configured CAN-SPAM sender identity.

    Returns:
        The sender identity, unvalidated -- callers validate at the point
        they need it so the error surfaces with useful context.
    """
    return SenderIdentity(
        from_name=settings.outreach_from_name,
        from_email=settings.outreach_from_email,
        reply_to=settings.outreach_reply_to,
        physical_address=settings.outreach_physical_address,
        company_name=settings.outreach_company_name,
    )


class OutreachDraftAgent:
    """Generates channel-appropriate outreach drafts for a lead."""

    def __init__(self, db: AsyncSession, kb: KnowledgeBaseService) -> None:
        """Initialize the agent.

        Args:
            db: Active database session.
            kb: Knowledge base service, used to find the best-matching service.
        """
        self._db = db
        self._kb = kb

    async def _top_findings(self, lead_id: uuid.UUID) -> tuple[WebsiteAudit | None, list[AuditFinding]]:
        """Fetch the most severe findings from a lead's latest audit.

        Args:
            lead_id: The lead to look up.

        Returns:
            The latest audit and its highest-severity findings, most severe
            first. Empty list if no audit exists.
        """
        audit = (
            await self._db.execute(
                select(WebsiteAudit)
                .where(WebsiteAudit.lead_id == lead_id)
                .order_by(WebsiteAudit.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if audit is None:
            return None, []

        findings = (
            (
                await self._db.execute(
                    select(AuditFinding).where(AuditFinding.audit_id == audit.id)
                )
            )
            .scalars()
            .all()
        )

        urgent = [f for f in findings if f.severity in _LEAD_WITH_SEVERITIES]
        selected = (urgent or list(findings))[:_MAX_FINDINGS_IN_DRAFT]
        return audit, selected

    async def top_findings(
        self, lead_id: uuid.UUID
    ) -> tuple[WebsiteAudit | None, list[AuditFinding]]:
        """Public wrapper around :meth:`_top_findings`.

        Exposed so :mod:`app.agents.call_card` can reuse the same
        "what's wrong with this lead's site" selection logic instead of
        duplicating it, keeping the call-center card's problem list and the
        outreach draft's problem list always in agreement.

        Args:
            lead_id: The lead to look up.

        Returns:
            The latest audit and its highest-severity findings.
        """
        return await self._top_findings(lead_id)

    async def best_service(self, lead: Lead, findings: list[AuditFinding]) -> Service | None:
        """Public wrapper around :meth:`_best_service`.

        Args:
            lead: The lead.
            findings: The selected audit findings.

        Returns:
            The best-matching service, or None.
        """
        return await self._best_service(lead, findings)

    async def _best_service(self, lead: Lead, findings: list[AuditFinding]) -> Service | None:
        """Find the service best matching a lead's findings.

        Prefers a service already mapped to a finding by the audit agent,
        falling back to a knowledge-base search on the lead's own category.

        Args:
            lead: The lead.
            findings: The selected audit findings.

        Returns:
            The best-matching service, or None if nothing matched.
        """
        for finding in findings:
            if finding.mapped_service_id is not None:
                service = await self._db.get(Service, finding.mapped_service_id)
                if service is not None:
                    return service

        query = " ".join(filter(None, [lead.category, lead.notes])).strip()
        if not query:
            return None

        chunks = await self._kb.search(query, top_k=9)
        scored = KnowledgeBaseService.collapse_to_services(chunks)[:1]
        if not scored:
            return None
        return await self._db.get(Service, scored[0].item)

    @staticmethod
    def _findings_block(findings: list[AuditFinding]) -> str:
        """Format findings for an LLM prompt.

        Args:
            findings: The selected findings.

        Returns:
            A newline-delimited block, or a placeholder when empty.
        """
        if not findings:
            return "(no website audit findings available)"
        return "\n".join(f"- {f.title}: {f.detail}" for f in findings)

    @staticmethod
    def _service_block(service: Service | None) -> str:
        """Format a service for an LLM prompt.

        Args:
            service: The matched service, if any.

        Returns:
            A one-line description, or a placeholder when no service matched.
        """
        if service is None:
            return "(no specific service matched)"
        return f"{service.name} ({service.price_range_label()}): {service.summary}"

    def _fallback_email(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> tuple[str, str]:
        """Build a deterministic email without calling the LLM.

        Args:
            lead: The lead being written to.
            findings: The selected findings.
            service: The matched service, if any.

        Returns:
            A ``(subject, body)`` pair drawn only from stored facts.
        """
        if findings:
            headline = findings[0].title
            subject = f"Noticed something on {lead.name}'s website"
            opener = (
                f"I ran a quick technical review of your website and found one thing "
                f"worth flagging: {headline.lower()}. {findings[0].detail}"
            )
        else:
            subject = f"A quick question about {lead.name}'s website"
            opener = (
                "I work with businesses in your sector on their websites and online "
                "presence, and wanted to introduce what we do."
            )

        if service is not None:
            offer = (
                f"\n\nThis is the kind of thing our {service.name} work addresses "
                f"({service.price_range_label()}). {service.summary}"
            )
        else:
            offer = ""

        body = (
            f"Hi{' ' + lead.contact_name if lead.contact_name else ''},\n\n"
            f"{opener}{offer}\n\n"
            "If it would be useful, I am happy to walk you through what we found on a "
            "short call. If not, no problem at all -- just let me know.\n\n"
            f"Best regards,\n{settings.outreach_from_name}\n{settings.outreach_company_name}"
        )
        return subject, body

    def _fallback_whatsapp(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> str:
        """Build a deterministic WhatsApp message without calling the LLM.

        Args:
            lead: The lead being written to.
            findings: The selected findings.
            service: The matched service, if any.

        Returns:
            The message body.
        """
        finding_text = (
            f" We spotted that {findings[0].title.lower()} on your site."
            if findings
            else ""
        )
        service_text = f" We help with {service.name.lower()}." if service else ""
        return (
            f"Hi{' ' + lead.contact_name if lead.contact_name else ''}, this is "
            f"{settings.outreach_from_name} from {settings.outreach_company_name}."
            f"{finding_text}{service_text} "
            "Would you be open to a short call this week to talk it through?"
        )

    def _fallback_call_script(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> str:
        """Build a deterministic call script without calling the LLM.

        Args:
            lead: The lead being called.
            findings: The selected findings.
            service: The matched service, if any.

        Returns:
            The script.
        """
        lines = [
            f"OPENING",
            f"Hi, is that {lead.contact_name or 'the owner'}? This is "
            f"{settings.outreach_from_name} from {settings.outreach_company_name}. "
            "I'll keep this to about a minute -- is now an alright time?",
            "[PAUSE]",
            "",
            "REASON FOR THE CALL",
        ]

        if findings:
            lines.append(
                f"We ran a technical review of {lead.name}'s website and found "
                f"{len(findings)} thing{'s' if len(findings) != 1 else ''} worth "
                f"mentioning. The main one: {findings[0].title.lower()}."
            )
            lines.append(f"In plain terms, {findings[0].detail}")
        else:
            lines.append(
                f"We work with businesses like {lead.name} on their websites and "
                "online presence."
            )

        lines.extend(["[PAUSE]", "", "QUESTION"])
        lines.append("Is that something you've been aware of, or is it news to you?")
        lines.append("[PAUSE]")
        lines.append("")

        if service is not None:
            lines.append("IF INTERESTED")
            lines.append(
                f"That's exactly what our {service.name} work covers -- typically "
                f"{service.price_range_label()}. I can send over what we found in "
                "writing so you can look at it in your own time. Would that be useful?"
            )
            lines.append("")

        lines.append("IF NOT INTERESTED")
        lines.append(
            "No problem at all, I appreciate you taking the call. I'll take you off "
            "our list -- have a good day."
        )
        lines.append("")
        lines.append(
            "[If the prospect asks not to be contacted again, set do-not-contact on "
            "the lead record immediately.]"
        )
        return "\n".join(lines)

    async def generate_call_script(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> DraftContent:
        """Generate call-script content directly, bypassing channel eligibility.

        Used by :mod:`app.agents.call_card` to produce the "suggested call
        script" on a call-center card. This intentionally does not run
        through :func:`app.services.outreach_policy.assess_channel` -- a
        call-center card is an internal briefing for a rep who is about to
        call a lead that has already asked to talk (it is not itself an
        outbound send subject to the approval queue), so the eligibility
        gate that protects outbound channels does not apply here.

        Args:
            lead: The lead being called.
            findings: Selected audit findings.
            service: The matched service, if any.

        Returns:
            The generated call-script content.
        """
        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"Website review findings:\n{self._findings_block(findings)}\n\n"
            f"Everen Techno service that addresses this:\n{self._service_block(service)}"
        )
        fallback = self._fallback_call_script(lead, findings, service)
        generated = await self._generate_with_llm(
            _CALL_SYSTEM_PROMPT, f"{base_context}\n\nWrite the call script."
        )
        return DraftContent(
            channel=OutreachChannel.CALL_SCRIPT,
            subject=None,
            body=generated or fallback,
            used_fallback=generated is None,
        )

    def _fallback_linkedin_connection_note(
        self, lead: Lead, findings: list[AuditFinding]
    ) -> str:
        """Build a deterministic LinkedIn connection-request note without the LLM.

        Args:
            lead: The lead being connected with.
            findings: The selected findings.

        Returns:
            A note within LinkedIn's 300-character connection-note limit.
        """
        first_name = f" {lead.contact_name.split()[0]}" if lead.contact_name else ""
        if findings:
            note = (
                f"Hi{first_name}, I help businesses like {lead.name} with their "
                f"websites and noticed something worth a quick chat. Would love to "
                "connect."
            )
        else:
            note = (
                f"Hi{first_name}, I work with businesses in your space on their "
                "websites and online presence. Would love to connect."
            )
        return note[:_LINKEDIN_CONNECTION_NOTE_MAX_CHARS]

    def _fallback_linkedin_followup(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> str:
        """Build a deterministic LinkedIn follow-up message without the LLM.

        Args:
            lead: The lead being followed up with.
            findings: The selected findings.
            service: The matched service, if any.

        Returns:
            The follow-up message body.
        """
        first_name = f" {lead.contact_name.split()[0]}" if lead.contact_name else ""
        greeting = f"Hi{first_name},"
        if findings:
            observation = (
                f"thanks for connecting. I noticed {findings[0].title.lower()} on your site."
            )
        else:
            observation = "thanks for connecting -- always good to meet folks in your space."

        if service is not None:
            offer = (
                f" That's exactly the kind of thing our {service.name} work addresses "
                f"({service.price_range_label()}). Open to a short call?"
            )
        else:
            offer = " Happy to share what we found if it's useful -- open to a short call?"

        return f"{greeting} {observation}{offer}"

    async def generate_linkedin_content(
        self, lead: Lead, findings: list[AuditFinding], service: Service | None
    ) -> DraftContent:
        """Generate a LinkedIn connection-request note and follow-up message.

        Two pieces of plain text, both meant to be copied and sent manually
        by a rep from their own LinkedIn account -- this system has no
        LinkedIn integration and never transmits either piece itself (see
        app.services.outreach_policy's module docstring, "LinkedIn"
        section, for why: LinkedIn's User Agreement prohibits automating
        platform actions without their separate written permission).

        The connection note is returned as ``DraftContent.body`` (mirroring
        how every other channel's primary text lives in ``body``); the
        follow-up message is carried separately on
        ``DraftContent.linkedin_followup_body`` since a LinkedIn draft is
        genuinely two independent pieces of text sent at two different
        times -- the note now, the follow-up only after the prospect
        accepts -- not one message with an appendix.

        Args:
            lead: The lead to draft for.
            findings: Selected audit findings.
            service: The matched service, if any.

        Returns:
            The generated content. ``subject`` is always None -- LinkedIn
            has no subject line. This method never persists or sends
            anything itself.
        """
        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"Website review findings:\n{self._findings_block(findings)}\n\n"
            f"Everen Techno service that addresses this:\n{self._service_block(service)}"
        )

        fallback_note = self._fallback_linkedin_connection_note(lead, findings)
        generated_note = await self._generate_with_llm(
            _LINKEDIN_CONNECTION_SYSTEM_PROMPT, f"{base_context}\n\nWrite the connection note."
        )
        note = generated_note or fallback_note
        if len(note) > _LINKEDIN_CONNECTION_NOTE_MAX_CHARS:
            logger.warning(
                "LLM-generated LinkedIn connection note exceeded LinkedIn's "
                "300-character limit; truncating",
                extra={"lead_id": str(lead.id), "generated_length": len(note)},
            )
            note = note[:_LINKEDIN_CONNECTION_NOTE_MAX_CHARS]

        fallback_followup = self._fallback_linkedin_followup(lead, findings, service)
        generated_followup = await self._generate_with_llm(
            _LINKEDIN_FOLLOWUP_SYSTEM_PROMPT, f"{base_context}\n\nWrite the follow-up message."
        )

        return DraftContent(
            channel=OutreachChannel.LINKEDIN,
            subject=None,
            body=note,
            used_fallback=generated_note is None or generated_followup is None,
            linkedin_followup_body=generated_followup or fallback_followup,
        )

    def _fallback_objection_response(
        self,
        objection_type: ObjectionType,
        lead: Lead,
        service: Service | None,
    ) -> tuple[str, str]:
        """Build a deterministic objection-response email without the LLM.

        Args:
            objection_type: Which objection is being addressed.
            lead: The lead who raised the objection.
            service: The matched service, if any.

        Returns:
            A ``(subject, body)`` pair drawn only from stored facts.
        """
        greeting = f"Hi{' ' + lead.contact_name if lead.contact_name else ''},"
        signoff = f"Best regards,\n{settings.outreach_from_name}\n{settings.outreach_company_name}"

        if objection_type is ObjectionType.PRICE:
            subject = "Following up on your question about price"
            if service is not None:
                body_middle = (
                    f"Totally understand wanting to know the cost is worth it. Our "
                    f"{service.name} work ({service.price_range_label()}) includes "
                    f"{service.summary} -- happy to walk through exactly what's "
                    "included on a short call so you can judge for yourself, no "
                    "obligation."
                )
            else:
                body_middle = (
                    "Happy to go through exactly what's included and answer any "
                    "questions on a short call, no obligation."
                )
        elif objection_type is ObjectionType.TIMING:
            subject = "No rush -- happy to check back later"
            body_middle = (
                "Completely understand -- no need to do anything now. If it's useful, "
                "I can check back in a while, whenever suits you better. Just let me "
                "know a rough time that works and I'll follow up then."
            )
        else:
            subject = "Thanks for letting me know"
            if service is not None:
                body_middle = (
                    f"Appreciate you taking the time to reply. If it's ever useful, "
                    f"our {service.name} work ({service.price_range_label()}) is there "
                    "if things change on your end -- no need to reply unless you'd "
                    "like to revisit it."
                )
            else:
                body_middle = (
                    "Appreciate you taking the time to reply. If anything changes on "
                    "your end, feel free to reach out -- no need to reply unless "
                    "you'd like to revisit it."
                )

        body = f"{greeting}\n\n{body_middle}\n\n{signoff}"
        return subject, body

    async def generate_objection_response(
        self,
        lead: Lead,
        findings: list[AuditFinding],
        service: Service | None,
        objection_type: ObjectionType,
        reply_text: str,
        channel: OutreachChannel,
    ) -> DraftContent:
        """Generate a suggested response addressing a classified objection.

        Grounded in the same audit findings and best-matching Everen Techno
        service (the service knowledge base) already used for cold outreach,
        so the rebuttal never references anything not already vetted for
        this lead. Callers must have already confirmed
        ``objection_type is not None`` via
        :func:`app.services.reply_classification.classify_objection` --
        there is deliberately no code path here for a hard opt-out, since
        that function never returns an objection type for one.

        Args:
            lead: The lead who raised the objection.
            findings: Selected audit findings (for grounding context only).
            service: The matched service, if any.
            objection_type: Which objection is being addressed.
            reply_text: The prospect's own reply, given to the LLM as
                additional grounding so the response actually answers what
                they said.
            channel: Which channel to write for -- EMAIL gets a subject and
                a longer allowance; WHATSAPP is short-form with no subject.
                CALL_SCRIPT is not supported here; callers needing a call
                script use :meth:`generate_call_script` instead.

        Returns:
            The generated content, ready to be persisted as an
            ``OutreachDraft`` with ``status=pending_review`` -- this method
            never persists anything itself.
        """
        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"The prospect's reply:\n{reply_text.strip()}\n\n"
            f"Website review findings (for context only):\n{self._findings_block(findings)}\n\n"
            f"Everen Techno service that addresses this:\n{self._service_block(service)}"
        )
        length_note = (
            "Write this as a WhatsApp message, under 60 words."
            if channel is OutreachChannel.WHATSAPP
            else "Write this as an email body, under 130 words."
        )

        fallback_subject, fallback_body = self._fallback_objection_response(
            objection_type, lead, service
        )
        generated = await self._generate_with_llm(
            _OBJECTION_SYSTEM_PROMPTS[objection_type], f"{base_context}\n\n{length_note}"
        )
        return DraftContent(
            channel=channel,
            subject=fallback_subject if channel is OutreachChannel.EMAIL else None,
            body=generated or fallback_body,
            used_fallback=generated is None,
        )

    async def _resolve_prompt(
        self, channel: OutreachChannel, bucket_key: str
    ) -> tuple[str | None, uuid.UUID | None, str | None]:
        """Resolve which system prompt to use for a channel, honoring A/B tests.

        Looks up every active :class:`PromptVersion` for this agent+channel.
        With zero active rows, the code-default hardcoded prompt is used
        (returns all None). With one, that prompt is used unconditionally.
        With two or more sharing the same non-null ``experiment_group``, the
        lead is deterministically bucketed between them via
        :func:`app.services.ab_testing.choose_variant`, so the same lead
        always sees the same variant. Multiple active rows NOT sharing a
        group is a data-entry mistake -- rather than guessing, this logs a
        warning and uses the most recently created one.

        Args:
            channel: The channel being drafted for.
            bucket_key: Stable key to bucket an A/B split on -- the lead's ID
                as a string, so the same lead always lands on the same side.

        Returns:
            A ``(prompt_text, prompt_version_id, ab_variant_label)`` tuple.
            All ``None`` when no active :class:`PromptVersion` exists, in
            which case the caller falls back to its hardcoded prompt.
        """
        rows = (
            (
                await self._db.execute(
                    select(PromptVersion)
                    .where(
                        PromptVersion.agent_name == AGENT_NAME,
                        PromptVersion.channel == channel,
                        PromptVersion.is_active.is_(True),
                    )
                    .order_by(PromptVersion.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        if not rows:
            return None, None, None
        if len(rows) == 1:
            row = rows[0]
            return row.prompt_text, row.id, None

        groups = {row.experiment_group for row in rows}
        if len(groups) != 1 or None in groups:
            logger.warning(
                "Multiple active prompt versions without a shared experiment_group; "
                "using the most recent one rather than guessing at a split",
                extra={"agent_name": AGENT_NAME, "channel": channel.value, "count": len(rows)},
            )
            row = rows[0]
            return row.prompt_text, row.id, None

        variants = [
            PromptVariant(variant_id=str(row.id), label=row.label) for row in rows
        ]
        chosen = choose_variant(bucket_key, variants)
        chosen_row = next(row for row in rows if str(row.id) == chosen.variant_id)
        return chosen_row.prompt_text, chosen_row.id, chosen_row.label

    async def _generate_with_llm(
        self, system_prompt: str, user_prompt: str
    ) -> str | None:
        """Ask the LLM for draft copy.

        Args:
            system_prompt: The system instruction.
            user_prompt: The grounded user message.

        Returns:
            The generated text, or None on any failure -- including the
            daily OpenAI cost-budget being exhausted (see
            app.services.cost_guard) -- so the caller falls back
            deterministically rather than the request failing outright.
        """
        try:
            await enforce_budget_before_call(
                self._db, CostProvider.OPENAI, settings.cost_guard_daily_budget_openai_usd
            )

            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model=settings.recommendation_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
            )
            content = (response.choices[0].message.content or "").strip()

            usage = getattr(response, "usage", None)
            cost = estimate_openai_cost(
                settings.recommendation_model,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
            await record_spend(
                self._db,
                CostProvider.OPENAI,
                "outreach.draft",
                cost,
                daily_budget_usd=settings.cost_guard_daily_budget_openai_usd,
            )

            return content or None
        except BudgetExceededError:
            logger.warning("LLM draft generation skipped: daily OpenAI budget exhausted")
            return None
        except Exception:
            logger.exception("LLM draft generation failed; using deterministic fallback")
            return None

    async def generate(
        self,
        lead: Lead,
        channels: list[OutreachChannel],
        *,
        email_suppressed: bool = False,
        hard_bounced: bool = False,
    ) -> DraftGenerationResult:
        """Generate drafts for the requested channels.

        Channels the lead is not eligible for are skipped with a recorded
        reason rather than drafted -- an ineligible draft sitting in the
        approval queue invites a reviewer to approve something that must not
        be sent.

        Args:
            lead: The lead to draft for.
            channels: Channels to attempt.
            email_suppressed: Whether the lead's email is suppressed.
            hard_bounced: Whether the lead's email previously hard-bounced.

        Returns:
            The generation result, including skipped-channel explanations.
        """
        context = build_lead_context(
            lead, email_suppressed=email_suppressed, hard_bounced=hard_bounced
        )
        result = DraftGenerationResult(lead_id=lead.id)

        audit, findings = await self._top_findings(lead.id)
        service = await self._best_service(lead, findings)

        findings_block = self._findings_block(findings)
        service_block = self._service_block(service)
        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"Website review findings:\n{findings_block}\n\n"
            f"Everen Techno service that addresses this:\n{service_block}"
        )

        for channel in channels:
            decision = assess_channel(channel, context)
            if not decision.allowed:
                result.skipped.append(decision)
                logger.info(
                    "Channel skipped during draft generation",
                    extra={
                        "lead_id": str(lead.id),
                        "channel": channel.value,
                        "blockers": len(decision.blockers),
                    },
                )
                continue

            content = await self._generate_for_channel(
                channel, lead, findings, service, base_context, decision
            )
            result.drafts.append(content)

        logger.info(
            "Draft generation complete",
            extra={
                "lead_id": str(lead.id),
                "generated": len(result.drafts),
                "skipped": len(result.skipped),
                "audit_id": str(audit.id) if audit else None,
            },
        )
        return result

    async def _generate_for_channel(
        self,
        channel: OutreachChannel,
        lead: Lead,
        findings: list[AuditFinding],
        service: Service | None,
        base_context: str,
        decision: ChannelDecision,
    ) -> DraftContent:
        """Generate content for a single eligible channel.

        Args:
            channel: The channel to generate for.
            lead: The lead.
            findings: Selected audit findings.
            service: The matched service, if any.
            base_context: Shared grounded prompt context.
            decision: The channel decision, whose warnings are carried through.

        Returns:
            The generated content.
        """
        prompt_override, prompt_version_id, ab_variant = await self._resolve_prompt(
            channel, str(lead.id)
        )
        tone_note = _CAMPAIGN_TONE_NOTES[lead.campaign_type]

        # --- Resolve draft language ----------------------------------------
        resolved_language = _resolve_draft_language(lead, settings.outreach_supported_languages)
        if resolved_language and resolved_language in _LANGUAGE_NAMES:
            lang_display = _LANGUAGE_NAMES[resolved_language]
            language_note = (
                f"LANGUAGE REQUIREMENT: Write the entire message body in {lang_display}. "
                f"Every word must be in {lang_display}. Do not mix in English or any "
                "other language. Adapt idioms and phrasing to read naturally for a "
                f"native {lang_display} speaker."
            )
            logger.info(
                "Generating draft in non-English language",
                extra={"lead_id": str(lead.id), "language": resolved_language,
                       "channel": channel.value},
            )
        else:
            language_note = ""

        if channel is OutreachChannel.EMAIL:
            fallback_subject, fallback_body = self._fallback_email(lead, findings, service)
            generated = await self._generate_with_llm(
                f"{prompt_override or _EMAIL_SYSTEM_PROMPT}\n\n{tone_note}\n\n{language_note}".strip(),
                f"{base_context}\n\nWrite the email body.",
            )
            return DraftContent(
                channel=channel,
                subject=fallback_subject,
                body=generated or fallback_body,
                used_fallback=generated is None,
                warnings=decision.warnings,
                prompt_version_id=prompt_version_id,
                ab_variant=ab_variant,
                draft_language=resolved_language,
            )

        if channel is OutreachChannel.WHATSAPP:
            fallback = self._fallback_whatsapp(lead, findings, service)
            generated = await self._generate_with_llm(
                f"{prompt_override or _WHATSAPP_SYSTEM_PROMPT}\n\n{tone_note}\n\n{language_note}".strip(),
                f"{base_context}\n\nWrite the WhatsApp message.",
            )
            return DraftContent(
                channel=channel,
                subject=None,
                body=generated or fallback,
                used_fallback=generated is None,
                warnings=decision.warnings,
                prompt_version_id=prompt_version_id,
                ab_variant=ab_variant,
                draft_language=resolved_language,
            )

        if channel is OutreachChannel.LINKEDIN:
            # Deliberately bypasses _resolve_prompt/tone_note: LinkedIn's
            # content is two independent pieces (connection note +
            # follow-up), a different shape from every other channel's
            # single system-prompt-plus-tone-note generation, so it isn't
            # wired into the PromptVersion/A/B-testing machinery the way
            # EMAIL/WHATSAPP/CALL_SCRIPT are. See generate_linkedin_content.
            content = await self.generate_linkedin_content(lead, findings, service)
            content.warnings = decision.warnings
            content.draft_language = resolved_language
            return content

        # Everything else eligible for drafting is CALL_SCRIPT -- there is
        # no explicit "if channel is CALL_SCRIPT" guard here because it's
        # the last channel in OutreachChannel once EMAIL/WHATSAPP/LINKEDIN
        # are handled above.
        fallback = self._fallback_call_script(lead, findings, service)
        generated = await self._generate_with_llm(
            f"{prompt_override or _CALL_SYSTEM_PROMPT}\n\n{tone_note}\n\n{language_note}".strip(),
            f"{base_context}\n\nWrite the call script.",
        )
        return DraftContent(
            channel=channel,
            subject=None,
            body=generated or fallback,
            used_fallback=generated is None,
            warnings=decision.warnings,
            prompt_version_id=prompt_version_id,
            ab_variant=ab_variant,
            draft_language=resolved_language,
        )

    def _fallback_follow_up(
        self,
        lead: Lead,
        findings: list[AuditFinding],
        service: Service | None,
        channel: OutreachChannel,
        follow_up_sequence: int,
    ) -> tuple[str, str]:
        """Build a deterministic follow-up message without calling the LLM.

        Args:
            lead: The lead being followed up with.
            findings: The selected findings.
            service: The matched service, if any.
            channel: EMAIL or WHATSAPP.
            follow_up_sequence: Which follow-up number this is (1-indexed).

        Returns:
            A ``(subject, body)`` pair drawn only from stored facts. ``subject``
            is only meaningful for EMAIL.
        """
        greeting = f"Hi{' ' + lead.contact_name if lead.contact_name else ''},"
        reminder = (
            f"following up on my note about {findings[0].title.lower()}"
            if findings
            else "following up on my earlier note"
        )
        if service is not None:
            offer = (
                f"Still happy to walk through how our {service.name} work "
                f"({service.price_range_label()}) could help, on a short call -- no "
                "pressure either way."
            )
        else:
            offer = "Still happy to chat whenever's useful -- no pressure either way."

        if channel is OutreachChannel.WHATSAPP:
            body = f"{greeting} just {reminder}. {offer}"
            return "", body

        subject = f"Following up: {lead.name}'s website (#{follow_up_sequence})"
        signoff = f"Best regards,\n{settings.outreach_from_name}\n{settings.outreach_company_name}"
        body = f"{greeting}\n\nJust {reminder}. {offer}\n\n{signoff}"
        return subject, body

    async def generate_follow_up(
        self,
        lead: Lead,
        findings: list[AuditFinding],
        service: Service | None,
        channel: OutreachChannel,
        follow_up_sequence: int,
    ) -> DraftContent:
        """Generate a cadence-triggered follow-up message.

        Used by :mod:`app.services.campaign_followup_scanner` once a lead's
        follow-up cadence (:mod:`app.services.campaign_cadence`, keyed on
        ``lead.campaign_type``) says the next touch is due and the lead has
        not yet replied. Deliberately a separate system prompt from
        :meth:`generate`'s first-contact prompt -- a follow-up must read as
        a follow-up, not a repeated cold open.

        Args:
            lead: The lead being followed up with.
            findings: Selected audit findings (grounding only, same ones the
                original draft used).
            service: The matched service, if any.
            channel: EMAIL or WHATSAPP. CALL_SCRIPT is not supported here --
                this system has no record of whether/when a call happened,
                so there is nothing to cadence a follow-up off of.
            follow_up_sequence: Which follow-up number this is (1 = the
                first follow-up after the initial send, 2 = the second, and
                so on).

        Returns:
            The generated content, ready to be persisted as an
            ``OutreachDraft`` with ``status=pending_review`` -- this method
            never persists or sends anything itself.

        Raises:
            ValueError: If ``channel`` is CALL_SCRIPT.
        """
        if channel is OutreachChannel.CALL_SCRIPT:
            raise ValueError("generate_follow_up does not support CALL_SCRIPT")

        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"This is follow-up number {follow_up_sequence} since the original outreach.\n\n"
            f"Website review findings:\n{self._findings_block(findings)}\n\n"
            f"Everen Techno service that addresses this:\n{self._service_block(service)}"
        )
        length_note = (
            "Write this as a WhatsApp message, under 50 words."
            if channel is OutreachChannel.WHATSAPP
            else "Write this as an email body, under 100 words."
        )
        system_prompt = f"{_FOLLOW_UP_SYSTEM_PROMPT}\n{_CAMPAIGN_TONE_NOTES[lead.campaign_type]}"

        fallback_subject, fallback_body = self._fallback_follow_up(
            lead, findings, service, channel, follow_up_sequence
        )
        generated = await self._generate_with_llm(system_prompt, f"{base_context}\n\n{length_note}")
        return DraftContent(
            channel=channel,
            subject=fallback_subject if channel is OutreachChannel.EMAIL else None,
            body=generated or fallback_body,
            used_fallback=generated is None,
        )


    def _fallback_booking_reply(
        self, lead: Lead, service: Service | None, channel: OutreachChannel
    ) -> tuple[str, str]:
        """Build a deterministic booking-confirmation reply without the LLM.

        Args:
            lead: The lead who asked to book a call / expressed interest.
            service: The matched service, if any -- mentioned only in
                passing; this message's job is to get them to the
                scheduling link, not to re-pitch.
            channel: EMAIL or WHATSAPP.

        Returns:
            A ``(subject, body)`` pair drawn only from stored facts, with
            no link included -- the caller appends the real booking URL
            separately. ``subject`` is only meaningful for EMAIL.
        """
        greeting = f"Hi{' ' + lead.contact_name if lead.contact_name else ''},"
        service_note = f" about our {service.name} work" if service is not None else ""
        lede = (
            f"{greeting} great to hear from you{service_note}. Pick whatever time "
            "works best for you using the scheduling link below and I'll see you then."
        )

        if channel is OutreachChannel.WHATSAPP:
            return "", lede

        subject = "Let's find a time to talk"
        signoff = f"Best regards,\n{settings.outreach_from_name}\n{settings.outreach_company_name}"
        body = f"{lede}\n\n{signoff}"
        return subject, body

    async def generate_booking_reply(
        self,
        lead: Lead,
        findings: list[AuditFinding],
        service: Service | None,
        reply_text: str,
        channel: OutreachChannel,
    ) -> DraftContent:
        """Generate a reply confirming interest and pointing to the booking link.

        Triggered when a reply is classified BOOK_CALL or INTERESTED (see
        :mod:`app.services.booking_link_scanner`). The generated body
        deliberately never contains the actual booking URL -- mirroring
        :func:`finalize_email_body`'s philosophy for the CAN-SPAM footer,
        the one piece of this message that must be correct byte-for-byte
        (a working scheduling link) is never trusted to the LLM. The
        caller is responsible for appending the real, deterministically
        built booking URL (see
        :func:`app.services.booking_token.build_booking_url`) after this
        method returns, as a fixed final sentence -- never asking the LLM
        to reproduce it.

        Args:
            lead: The lead who asked to book a call / expressed interest.
            findings: Selected audit findings (grounding only).
            service: The matched service, if any.
            reply_text: The prospect's own reply, given to the LLM as
                additional grounding so the response actually answers what
                they said.
            channel: EMAIL or WHATSAPP. CALL_SCRIPT is not supported here
                -- this reply exists to put a link in front of the
                prospect, which only makes sense over a written channel.

        Returns:
            The generated content, with no booking URL in the body --
            ready for the caller to append one before persisting as an
            ``OutreachDraft`` with ``status=pending_review``. This method
            never persists or sends anything itself.

        Raises:
            ValueError: If ``channel`` is CALL_SCRIPT.
        """
        if channel is OutreachChannel.CALL_SCRIPT:
            raise ValueError("generate_booking_reply does not support CALL_SCRIPT")

        base_context = (
            f"Business: {lead.name}\n"
            f"Contact: {lead.contact_name or 'unknown'}\n"
            f"Sector: {lead.category or 'unknown'}\n\n"
            f"The prospect's reply:\n{reply_text.strip()}\n\n"
            f"Website review findings (for context only):\n{self._findings_block(findings)}\n\n"
            f"Everen Techno service that addresses this:\n{self._service_block(service)}"
        )
        length_note = (
            "Write this as a WhatsApp message, under 40 words."
            if channel is OutreachChannel.WHATSAPP
            else "Write this as an email body, under 80 words."
        )

        fallback_subject, fallback_body = self._fallback_booking_reply(lead, service, channel)
        generated = await self._generate_with_llm(
            _BOOKING_REPLY_SYSTEM_PROMPT, f"{base_context}\n\n{length_note}"
        )
        if generated is not None and _URL_PATTERN.search(generated):
            logger.warning(
                "LLM wrote a URL into a booking reply despite instructions not to; "
                "stripping it -- the real booking link is appended separately",
                extra={"lead_id": str(lead.id), "channel": channel.value},
            )
            generated = _URL_PATTERN.sub("", generated).strip()

        return DraftContent(
            channel=channel,
            subject=fallback_subject if channel is OutreachChannel.EMAIL else None,
            body=generated or fallback_body,
            used_fallback=generated is None,
        )


def finalize_email_body(
    draft_id: uuid.UUID, lead_id: uuid.UUID, recipient_email: str, body: str
) -> tuple[str, str]:
    """Attach the CAN-SPAM footer to a drafted email body.

    Args:
        draft_id: The draft's identifier, bound into the unsubscribe token.
        lead_id: The lead's identifier, bound into the GDPR/CCPA erasure
            token -- an erasure request is about the whole record, not one
            message, so it is keyed on the lead rather than the draft.
        recipient_email: The recipient's address.
        body: The drafted body, without a footer.

    Returns:
        A ``(assembled_body, unsubscribe_url)`` pair.

    Raises:
        CanSpamViolationError: If the sender identity is incomplete -- most
            commonly because ``OUTREACH_PHYSICAL_ADDRESS`` is still the
            placeholder.
    """
    sender = sender_identity()
    token = make_unsubscribe_token(str(draft_id), recipient_email, settings.secret_key)
    unsubscribe_url = build_unsubscribe_url(
        settings.outreach_public_base_url, str(draft_id), recipient_email, token
    )
    erasure_token = make_erasure_token(str(lead_id), recipient_email, settings.secret_key)
    erasure_url = build_erasure_url(
        settings.outreach_public_base_url, str(lead_id), recipient_email, erasure_token
    )
    assembled = assemble_email_body(body, sender, unsubscribe_url, erasure_url)
    return assembled, unsubscribe_url


def validate_draft_subject(subject: str) -> None:
    """Validate a subject line against CAN-SPAM's deception rules.

    Args:
        subject: The proposed subject line.

    Raises:
        CanSpamViolationError: If the subject is blank or deceptive.
    """
    validate_subject(subject)
