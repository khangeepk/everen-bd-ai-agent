"""Per-channel outreach eligibility rules.

Standard library only, so the rules deciding whether a lead may be contacted
on a given channel are testable in isolation. This module answers "may we
draft this?" -- it never sends anything and never decides "is this a good
idea?", which is the scoring engine's job.

Channel regimes differ substantially, which is why this is not one shared
rule:

**Email** -- permitted for cold B2B outreach in the US under CAN-SPAM,
provided the message carries sender identification, a physical postal
address, and a working opt-out. Recipients in the EEA/UK additionally need a
lawful basis under GDPR/PECR.

**WhatsApp** -- NOT permitted for cold outreach. Meta's WhatsApp Business
Messaging Policy requires opt-in permission before any business-initiated
message, and business-initiated conversations must use a Meta-approved
Message Template. Since March 2026 Meta also applies preemptive enforcement
against accounts showing rapid contact-list growth paired with high template
send velocity and low engagement -- exactly the pattern bulk-discovered leads
would produce. So :func:`assess_channel` refuses to draft WhatsApp for a lead
without recorded opt-in, rather than producing a draft a reviewer might
approve without realising the account risk.

**Call script** -- a script is a document a human reads aloud; nothing is
transmitted by this system, so there is no send gate. Cold B2B calling is
generally permissible in the US, but note the UK's Corporate Telephone
Preference Service does cover business numbers, so a UK lead should be
screened against CTPS before dialling. That screening is out of scope here
and is surfaced as a warning on the draft.

**LinkedIn** -- like a call script, this system never transmits a LinkedIn
message itself: it drafts a connection-request note and a follow-up message
as plain text for a rep to copy and send manually from their own LinkedIn
account. This is deliberate, not a missing integration -- LinkedIn's User
Agreement prohibits automating actions on the platform (connection requests,
messages, profile scraping) without LinkedIn's separate written permission,
and using their consumer/Sales Navigator surfaces through anything but a
human at the keyboard risks account restriction. So there is no LinkedIn API
client anywhere in this codebase and there never should be one added here;
:func:`assess_linkedin` and the generated content exist purely to save a rep
typing, not to reach around that restriction. See app/agents/outreach.py's
``generate_linkedin_content`` docstring for the two-piece (connection note +
follow-up) content shape this produces.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Jurisdictions where a recorded lawful basis is required before cold email,
#: and where business phone numbers may be registered on a preference service.
#: Matched case-insensitively against free-text country values.
STRICT_JURISDICTIONS: frozenset[str] = frozenset(
    {
        "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
        "czech republic", "denmark", "estonia", "finland", "france", "germany",
        "greece", "hungary", "iceland", "ireland", "italy", "latvia",
        "liechtenstein", "lithuania", "luxembourg", "malta", "netherlands",
        "norway", "poland", "portugal", "romania", "slovakia", "slovenia",
        "spain", "sweden", "united kingdom", "uk", "gb",
    }
)

#: Jurisdictions whose telephone preference service covers business numbers.
CTPS_JURISDICTIONS: frozenset[str] = frozenset({"united kingdom", "uk", "gb"})


class OutreachChannel(str, enum.Enum):
    """A channel an outreach draft can target."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"
    CALL_SCRIPT = "call_script"
    #: Connection-request note + follow-up message, drafted as plain text
    #: for a rep to copy and send manually from their own LinkedIn account.
    #: See the module docstring's "LinkedIn" section for why this system
    #: never transmits one itself.
    LINKEDIN = "linkedin"


class CampaignType(str, enum.Enum):
    """Which kind of relationship this outreach is addressing.

    Drives two things elsewhere in this codebase: the tone/framing of
    generated drafts (app.agents.outreach) and the follow-up cadence a
    non-responding lead is put on (app.services.campaign_cadence). Lives
    here rather than in app/db/models/lead.py because -- like
    OutreachChannel above -- it is fundamentally an outreach-policy concept
    that both the Lead and OutreachDraft models need to reference, not a
    lead-only classification.
    """

    #: A prospect with no prior relationship to Everen Techno. The default
    #: for every lead unless set otherwise -- matches this codebase's
    #: behavior before this field existed, where every draft was written as
    #: a first cold open.
    COLD = "cold"
    #: A prospect with some existing warmth: a referral, an inbound
    #: enquiry, or a lead who has already engaged (e.g. replied, booked a
    #: call) on a separate thread. Drafts assume familiarity rather than
    #: opening cold.
    WARM = "warm"
    #: A previously contacted, previously lost, or gone-quiet lead being
    #: revisited after a gap. Drafts acknowledge the gap explicitly rather
    #: than pretending this is the first contact.
    RE_ENGAGEMENT = "re_engagement"


@dataclass(frozen=True)
class ChannelDecision:
    """Whether a draft may be generated for one channel.

    Attributes:
        channel: The channel assessed.
        allowed: Whether a draft may be generated.
        blockers: Reasons a draft may not be generated. Non-empty exactly
            when ``allowed`` is False.
        warnings: Concerns a human reviewer should weigh, which do not block
            drafting.
    """

    channel: OutreachChannel
    allowed: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate blockers and allowed status agree.

        Raises:
            ValueError: If the decision is internally inconsistent.
        """
        if self.allowed and self.blockers:
            raise ValueError("an allowed ChannelDecision must not carry blockers")
        if not self.allowed and not self.blockers:
            raise ValueError("a blocked ChannelDecision must explain why")


@dataclass(frozen=True)
class LeadOutreachContext:
    """The lead attributes that determine channel eligibility.

    A plain data carrier rather than the ORM model, so these rules can be
    tested without a database session.

    Attributes:
        do_not_contact: Hard suppression flag.
        do_not_contact_reason: Why suppression was applied.
        contact_email: Email address, if known.
        contact_phone: Phone number, if known.
        linkedin_url: The lead's LinkedIn profile URL, if known -- see
            app.db.models.lead.Lead.linkedin_url.
        consent_basis: Recorded lawful basis, if any.
        country: Free-text country.
        whatsapp_opt_in: Whether the lead gave opt-in permission for
            business-initiated WhatsApp messages.
        email_suppressed: Whether the email address is on the suppression list.
        hard_bounced: Whether the email address previously hard-bounced.
        contact_email_verified: Whether the email address is trusted enough
            to draft/send to. Defaults to True so every existing caller that
            predates the email-enrichment feature (app/services/
            email_enrichment.py) keeps behaving exactly as before -- only an
            email the enrichment chain found (website_contact_page or
            pattern_guess source, see app.db.models.lead.Lead) is ever False,
            and only until a human confirms it via
            POST /leads/{id}/email/verify.
    """

    do_not_contact: bool = False
    do_not_contact_reason: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    linkedin_url: str | None = None
    consent_basis: str | None = None
    country: str | None = None
    whatsapp_opt_in: bool = False
    email_suppressed: bool = False
    hard_bounced: bool = False
    contact_email_verified: bool = True

    def in_strict_jurisdiction(self) -> bool:
        """Whether the lead is in a GDPR/PECR jurisdiction.

        Returns:
            True if the country matches :data:`STRICT_JURISDICTIONS`.
        """
        return bool(self.country) and self.country.strip().lower() in STRICT_JURISDICTIONS

    def in_ctps_jurisdiction(self) -> bool:
        """Whether the lead's phone may be on a business preference service.

        Returns:
            True if the country matches :data:`CTPS_JURISDICTIONS`.
        """
        return bool(self.country) and self.country.strip().lower() in CTPS_JURISDICTIONS


def _universal_blockers(context: LeadOutreachContext) -> list[str]:
    """Blockers that apply to every channel regardless of medium.

    Args:
        context: The lead's outreach context.

    Returns:
        Blocking reasons, empty if none apply.
    """
    if context.do_not_contact:
        reason = context.do_not_contact_reason or "Lead is flagged do-not-contact."
        return [f"Lead is suppressed: {reason}"]
    return []


def assess_email(context: LeadOutreachContext) -> ChannelDecision:
    """Decide whether an email draft may be generated.

    Args:
        context: The lead's outreach context.

    Returns:
        The channel decision.
    """
    blockers = _universal_blockers(context)
    warnings: list[str] = []

    if not context.contact_email:
        blockers.append("No email address on file.")
    elif not context.contact_email_verified:
        # Only reachable for an enrichment-sourced email (website_contact_page
        # or pattern_guess) -- a manually-entered address defaults to
        # verified=True. Treated as a hard blocker, same as a missing
        # address entirely: a guessed or scraped email is not evidence that
        # outreach will actually reach anyone, and drafting against it would
        # put a message in the approval queue that looks ready to send but
        # is targeting an unconfirmed address. See
        # app/services/email_enrichment_scanner.py and
        # POST /leads/{id}/email/verify.
        blockers.append(
            "Contact email was auto-enriched (not manually entered) and has not been "
            "verified by a human yet. Confirm the address via "
            "POST /leads/{id}/email/verify, or replace it with a known-good one, "
            "before drafting outreach."
        )
    if context.email_suppressed:
        blockers.append(
            "Email address is on the suppression list. CAN-SPAM opt-outs never "
            "expire, so this address may not be re-contacted."
        )
    if context.hard_bounced:
        blockers.append(
            "Email address previously hard-bounced. Continuing to send damages "
            "sender reputation and suggests the address is invalid."
        )

    if context.in_strict_jurisdiction() and not context.consent_basis:
        warnings.append(
            f"Lead is in {context.country}, a GDPR/PECR jurisdiction, with no lawful "
            "basis recorded. CAN-SPAM compliance alone is not sufficient here."
        )

    return ChannelDecision(
        channel=OutreachChannel.EMAIL,
        allowed=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def assess_whatsapp(context: LeadOutreachContext) -> ChannelDecision:
    """Decide whether a WhatsApp draft may be generated.

    The opt-in requirement is a hard blocker, not a warning. Meta's policy
    requires opt-in permission before any business-initiated message, and
    generating a draft for a non-opted-in lead would put an unsendable
    message in the approval queue where a reviewer could approve it without
    realising it risks the WhatsApp Business account.

    Args:
        context: The lead's outreach context.

    Returns:
        The channel decision.
    """
    blockers = _universal_blockers(context)
    warnings: list[str] = []

    if not context.contact_phone:
        blockers.append("No phone number on file.")

    if not context.whatsapp_opt_in:
        blockers.append(
            "No WhatsApp opt-in on record. Meta's WhatsApp Business Messaging Policy "
            "requires opt-in permission before any business-initiated message. "
            "Cold WhatsApp outreach risks account suspension under Meta's "
            "preemptive enforcement for high-velocity, low-engagement senders."
        )
    else:
        warnings.append(
            "Business-initiated WhatsApp messages must use a Meta-approved Message "
            "Template. Verify the template is approved in the correct category "
            "(marketing/utility/authentication) before sending."
        )

    return ChannelDecision(
        channel=OutreachChannel.WHATSAPP,
        allowed=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def assess_call_script(context: LeadOutreachContext) -> ChannelDecision:
    """Decide whether a call script may be generated.

    A script is a document for a human to read; this system transmits
    nothing, so the bar is lower than for email or WhatsApp.

    Args:
        context: The lead's outreach context.

    Returns:
        The channel decision.
    """
    blockers = _universal_blockers(context)
    warnings: list[str] = []

    if not context.contact_phone:
        blockers.append("No phone number on file.")

    if context.in_ctps_jurisdiction():
        warnings.append(
            f"Lead is in {context.country}. The Corporate Telephone Preference "
            "Service covers business numbers there -- screen against CTPS before "
            "dialling."
        )

    return ChannelDecision(
        channel=OutreachChannel.CALL_SCRIPT,
        allowed=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def assess_linkedin(context: LeadOutreachContext) -> ChannelDecision:
    """Decide whether LinkedIn connection-request + follow-up text may be drafted.

    No opt-in gate the way WhatsApp has one -- LinkedIn imposes no analogous
    platform requirement on the *content* of a cold connection request, only
    on *how* it's sent (a human, manually, from their own account; see the
    module docstring). So the only hard blocker here is not having a profile
    to write to at all; the "no automation" constraint is a standing warning
    on every draft rather than a conditional blocker, since it applies
    unconditionally and a reviewer should see it every time, not just when
    something is wrong.

    Args:
        context: The lead's outreach context.

    Returns:
        The channel decision.
    """
    blockers = _universal_blockers(context)
    warnings = [
        "LinkedIn drafts are text only. Copy the connection-request note and follow-up "
        "message and send them manually from your own LinkedIn account -- this system "
        "has no LinkedIn integration and must not be made to send or scrape "
        "automatically (LinkedIn's User Agreement prohibits automating platform "
        "actions without their separate written permission)."
    ]

    if not context.linkedin_url:
        blockers.append("No LinkedIn profile URL on file.")

    return ChannelDecision(
        channel=OutreachChannel.LINKEDIN,
        allowed=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def assess_channel(channel: OutreachChannel, context: LeadOutreachContext) -> ChannelDecision:
    """Assess eligibility for one channel.

    Args:
        channel: The channel to assess.
        context: The lead's outreach context.

    Returns:
        The channel decision.

    Raises:
        ValueError: If the channel is not recognized.
    """
    assessors = {
        OutreachChannel.EMAIL: assess_email,
        OutreachChannel.WHATSAPP: assess_whatsapp,
        OutreachChannel.CALL_SCRIPT: assess_call_script,
        OutreachChannel.LINKEDIN: assess_linkedin,
    }
    assessor = assessors.get(channel)
    if assessor is None:
        raise ValueError(f"unknown outreach channel {channel!r}")

    decision = assessor(context)
    logger.info(
        "Channel eligibility assessed",
        extra={
            "channel": channel.value,
            "allowed": decision.allowed,
            "blocker_count": len(decision.blockers),
        },
    )
    return decision


def eligible_channels(context: LeadOutreachContext) -> list[OutreachChannel]:
    """List every channel a lead may currently be drafted for.

    Args:
        context: The lead's outreach context.

    Returns:
        The allowed channels, in a stable order.
    """
    return [
        channel
        for channel in (
            OutreachChannel.EMAIL,
            OutreachChannel.WHATSAPP,
            OutreachChannel.CALL_SCRIPT,
            OutreachChannel.LINKEDIN,
        )
        if assess_channel(channel, context).allowed
    ]
