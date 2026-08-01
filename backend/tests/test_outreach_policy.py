"""Tests for :mod:`app.services.outreach_policy`.

The central behavior: WhatsApp drafts are hard-blocked without recorded
opt-in, because Meta's WhatsApp Business Messaging Policy requires opt-in
before any business-initiated message and enforces against high-velocity cold
senders.
"""

from __future__ import annotations

import pytest

from app.services.outreach_policy import (
    ChannelDecision,
    LeadOutreachContext,
    OutreachChannel,
    assess_call_script,
    assess_channel,
    assess_email,
    assess_linkedin,
    assess_whatsapp,
    eligible_channels,
)


def _context(**overrides: object) -> LeadOutreachContext:
    """Build a LeadOutreachContext with contactable defaults.

    Args:
        **overrides: Fields to replace.

    Returns:
        A :class:`LeadOutreachContext`.
    """
    defaults: dict[str, object] = {
        "contact_email": "owner@prospect.example",
        "contact_phone": "+15125550100",
        "country": "United States",
        "consent_basis": "legitimate_interest",
    }
    defaults.update(overrides)
    return LeadOutreachContext(**defaults)


# ---------------------------------------------------------------------------
# WhatsApp opt-in gate -- the headline rule.
# ---------------------------------------------------------------------------


def test_whatsapp_is_blocked_without_opt_in() -> None:
    """Cold WhatsApp is refused outright, not merely warned about."""
    decision = assess_whatsapp(_context(whatsapp_opt_in=False))

    assert decision.allowed is False
    assert any("opt-in" in blocker.lower() for blocker in decision.blockers)


def test_whatsapp_is_allowed_with_opt_in() -> None:
    """A lead with recorded opt-in may be drafted for WhatsApp."""
    decision = assess_whatsapp(_context(whatsapp_opt_in=True))
    assert decision.allowed is True


def test_whatsapp_with_opt_in_still_warns_about_templates() -> None:
    """Opt-in is necessary but not sufficient; templates must be approved."""
    decision = assess_whatsapp(_context(whatsapp_opt_in=True))

    assert any("template" in warning.lower() for warning in decision.warnings)


def test_whatsapp_blocked_without_a_phone_number() -> None:
    """No phone number means no WhatsApp draft."""
    decision = assess_whatsapp(_context(contact_phone=None, whatsapp_opt_in=True))

    assert decision.allowed is False
    assert any("phone" in blocker.lower() for blocker in decision.blockers)


def test_whatsapp_blocker_mentions_account_risk() -> None:
    """The blocker explains the consequence, not just the rule."""
    decision = assess_whatsapp(_context(whatsapp_opt_in=False))
    combined = " ".join(decision.blockers).lower()

    assert "suspension" in combined or "enforcement" in combined


# ---------------------------------------------------------------------------
# Universal suppression gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel",
    [
        OutreachChannel.EMAIL,
        OutreachChannel.WHATSAPP,
        OutreachChannel.CALL_SCRIPT,
        OutreachChannel.LINKEDIN,
    ],
)
def test_do_not_contact_blocks_every_channel(channel: OutreachChannel) -> None:
    """Suppression is absolute across all channels, including LinkedIn."""
    context = _context(
        do_not_contact=True,
        do_not_contact_reason="Unsubscribed",
        whatsapp_opt_in=True,
        linkedin_url="https://linkedin.com/in/jane-doe",
    )
    decision = assess_channel(channel, context)

    assert decision.allowed is False
    assert any("suppressed" in blocker.lower() for blocker in decision.blockers)


def test_do_not_contact_reason_is_surfaced() -> None:
    """The blocker carries the recorded reason, so reviewers see why."""
    decision = assess_email(
        _context(do_not_contact=True, do_not_contact_reason="Legal objection received")
    )
    assert any("Legal objection received" in blocker for blocker in decision.blockers)


# ---------------------------------------------------------------------------
# Email.
# ---------------------------------------------------------------------------


def test_email_allowed_for_a_clean_contactable_lead() -> None:
    """A normal US lead with an email address may be drafted."""
    assert assess_email(_context()).allowed is True


def test_email_blocked_without_an_address() -> None:
    """No email address means no email draft."""
    decision = assess_email(_context(contact_email=None))

    assert decision.allowed is False
    assert any("email address" in blocker.lower() for blocker in decision.blockers)


def test_email_blocked_when_suppressed() -> None:
    """A suppressed address may never be re-contacted."""
    decision = assess_email(_context(email_suppressed=True))

    assert decision.allowed is False
    assert any("suppression" in blocker.lower() for blocker in decision.blockers)


def test_email_suppression_blocker_notes_opt_outs_never_expire() -> None:
    """The blocker states the CAN-SPAM rule rather than being opaque."""
    decision = assess_email(_context(email_suppressed=True))
    assert any("never" in blocker.lower() for blocker in decision.blockers)


def test_email_blocked_after_a_hard_bounce() -> None:
    """Continuing to send to a hard-bounced address damages reputation."""
    decision = assess_email(_context(hard_bounced=True))

    assert decision.allowed is False
    assert any("bounce" in blocker.lower() for blocker in decision.blockers)


def test_email_allowed_when_verified_flag_defaults_true() -> None:
    """A manually-entered email (the pre-enrichment-feature norm) is unaffected."""
    decision = assess_email(_context())
    assert decision.allowed is True


def test_email_blocked_when_unverified() -> None:
    """An enrichment-sourced email that hasn't been confirmed by a human is blocked.

    This is the core of the email-enrichment feature's "never auto-send to
    unverified emails" requirement -- drafting itself is refused, not just
    sending, so a guessed/scraped address never reaches the approval queue
    looking send-ready.
    """
    decision = assess_email(_context(contact_email_verified=False))

    assert decision.allowed is False
    assert any("verified" in blocker.lower() for blocker in decision.blockers)


def test_email_unverified_blocker_points_to_the_verify_endpoint() -> None:
    """The blocker tells a reviewer exactly how to unblock the lead."""
    decision = assess_email(_context(contact_email_verified=False))
    assert any("/email/verify" in blocker for blocker in decision.blockers)


def test_email_missing_address_takes_precedence_over_verified_flag() -> None:
    """With no address at all, the 'no email' blocker fires, not the verify one."""
    decision = assess_email(_context(contact_email=None, contact_email_verified=False))

    assert decision.allowed is False
    assert any("no email address" in blocker.lower() for blocker in decision.blockers)


def test_eea_lead_without_consent_warns_but_does_not_block() -> None:
    """GDPR exposure is a reviewer judgement call, not an automatic block."""
    decision = assess_email(_context(country="Germany", consent_basis=None))

    assert decision.allowed is True
    assert any("GDPR" in warning or "lawful basis" in warning for warning in decision.warnings)


def test_eea_lead_with_consent_has_no_warning() -> None:
    """A recorded lawful basis clears the GDPR warning."""
    decision = assess_email(_context(country="France", consent_basis="consent"))

    assert decision.allowed is True
    assert decision.warnings == ()


def test_us_lead_without_consent_has_no_gdpr_warning() -> None:
    """The GDPR warning is jurisdiction-scoped, not universal."""
    decision = assess_email(_context(country="United States", consent_basis=None))
    assert decision.warnings == ()


@pytest.mark.parametrize("country", ["Germany", "germany", "GERMANY", "United Kingdom", "uk"])
def test_strict_jurisdiction_matching_is_case_insensitive(country: str) -> None:
    """Jurisdiction detection tolerates whatever casing a rep typed."""
    decision = assess_email(_context(country=country, consent_basis=None))
    assert decision.warnings


# ---------------------------------------------------------------------------
# Call scripts.
# ---------------------------------------------------------------------------


def test_call_script_allowed_with_a_phone_number() -> None:
    """A script is a document; the bar is lower than for transmitted channels."""
    assert assess_call_script(_context()).allowed is True


def test_call_script_blocked_without_a_phone_number() -> None:
    """No number, no script."""
    decision = assess_call_script(_context(contact_phone=None))
    assert decision.allowed is False


def test_uk_lead_warns_about_ctps() -> None:
    """The UK's CTPS covers business numbers, unlike the US DNC registry."""
    decision = assess_call_script(_context(country="United Kingdom"))

    assert decision.allowed is True
    assert any("CTPS" in warning for warning in decision.warnings)


def test_us_lead_has_no_ctps_warning() -> None:
    """CTPS is UK-specific."""
    decision = assess_call_script(_context(country="United States"))
    assert not any("CTPS" in warning for warning in decision.warnings)


def test_call_script_does_not_require_whatsapp_opt_in() -> None:
    """Call scripts are unaffected by the WhatsApp gate."""
    assert assess_call_script(_context(whatsapp_opt_in=False)).allowed is True


# ---------------------------------------------------------------------------
# LinkedIn.
# ---------------------------------------------------------------------------


def test_linkedin_blocked_without_a_profile_url() -> None:
    """No LinkedIn profile on file means no draft -- there's nothing to write to."""
    decision = assess_linkedin(_context(linkedin_url=None))

    assert decision.allowed is False
    assert any("linkedin profile" in blocker.lower() for blocker in decision.blockers)


def test_linkedin_allowed_with_a_profile_url() -> None:
    """A lead with a known profile may be drafted for LinkedIn."""
    decision = assess_linkedin(_context(linkedin_url="https://linkedin.com/in/jane-doe"))
    assert decision.allowed is True


def test_linkedin_always_warns_that_sending_is_manual() -> None:
    """Unlike WhatsApp's conditional template warning, this warning is unconditional.

    There is no scenario where a LinkedIn draft should look automatable --
    this system has no LinkedIn integration and must never gain one, so the
    warning fires whether or not the draft is otherwise allowed.
    """
    allowed = assess_linkedin(_context(linkedin_url="https://linkedin.com/in/jane-doe"))
    blocked = assess_linkedin(_context(linkedin_url=None))

    assert any("manually" in w.lower() for w in allowed.warnings)
    assert any("manually" in w.lower() for w in blocked.warnings)


def test_linkedin_warning_mentions_no_automation() -> None:
    """The warning explicitly rules out sending or scraping automation."""
    decision = assess_linkedin(_context(linkedin_url="https://linkedin.com/in/jane-doe"))
    combined = " ".join(decision.warnings).lower()
    assert "automat" in combined


def test_linkedin_does_not_require_whatsapp_opt_in() -> None:
    """LinkedIn eligibility is independent of the WhatsApp opt-in gate."""
    decision = assess_linkedin(
        _context(linkedin_url="https://linkedin.com/in/jane-doe", whatsapp_opt_in=False)
    )
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Aggregation and invariants.
# ---------------------------------------------------------------------------


def test_eligible_channels_excludes_whatsapp_for_a_cold_lead() -> None:
    """The realistic cold-prospecting case: email and calls, no WhatsApp."""
    channels = eligible_channels(_context(whatsapp_opt_in=False))

    assert OutreachChannel.EMAIL in channels
    assert OutreachChannel.CALL_SCRIPT in channels
    assert OutreachChannel.WHATSAPP not in channels


def test_eligible_channels_includes_whatsapp_after_opt_in() -> None:
    """Three of the four channels open up once opt-in is recorded (no LinkedIn URL on file)."""
    channels = eligible_channels(_context(whatsapp_opt_in=True))
    assert len(channels) == 3
    assert OutreachChannel.LINKEDIN not in channels


def test_eligible_channels_includes_linkedin_once_a_profile_is_on_file() -> None:
    """All four channels open up once every gate (opt-in, profile URL) is satisfied."""
    channels = eligible_channels(
        _context(whatsapp_opt_in=True, linkedin_url="https://linkedin.com/in/jane-doe")
    )
    assert len(channels) == 4
    assert OutreachChannel.LINKEDIN in channels


def test_eligible_channels_is_empty_for_a_suppressed_lead() -> None:
    """A suppressed lead has no eligible channel at all."""
    context = _context(
        do_not_contact=True,
        do_not_contact_reason="Opted out",
        whatsapp_opt_in=True,
        linkedin_url="https://linkedin.com/in/jane-doe",
    )
    assert eligible_channels(context) == []


def test_unknown_channel_raises() -> None:
    """An unrecognized channel is a programming error."""
    with pytest.raises(ValueError, match="unknown outreach channel"):
        assess_channel("carrier_pigeon", _context())  # type: ignore[arg-type]


def test_allowed_decision_cannot_carry_blockers() -> None:
    """An internally inconsistent decision is refused at construction."""
    with pytest.raises(ValueError, match="must not carry blockers"):
        ChannelDecision(
            channel=OutreachChannel.EMAIL, allowed=True, blockers=("contradiction",)
        )


def test_blocked_decision_must_explain_itself() -> None:
    """A block with no reason is not actionable and is refused."""
    with pytest.raises(ValueError, match="must explain why"):
        ChannelDecision(channel=OutreachChannel.EMAIL, allowed=False)
