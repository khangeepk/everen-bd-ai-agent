"""Tests for :mod:`app.services.canspam`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services.canspam import (
    CanSpamViolationError,
    SenderIdentity,
    assemble_email_body,
    build_footer,
    build_unsubscribe_url,
    is_deceptive_subject,
    make_unsubscribe_token,
    validate_sendable_email,
    validate_subject,
    verify_unsubscribe_token,
)

SECRET = "test-signing-secret"
DRAFT_ID = "11111111-1111-1111-1111-111111111111"
RECIPIENT = "owner@prospect.example"


def _sender(**overrides: object) -> SenderIdentity:
    """Build a valid SenderIdentity with optional overrides.

    Args:
        **overrides: Fields to replace.

    Returns:
        A :class:`SenderIdentity`.
    """
    defaults: dict[str, object] = {
        "from_name": "Sami Khan",
        "from_email": "bd@everentechno.example",
        "physical_address": "12 Example Street, Suite 400, Austin, TX 78701, USA",
        "company_name": "Everen Techno",
    }
    defaults.update(overrides)
    return SenderIdentity(**defaults)


def test_valid_sender_passes_validation() -> None:
    """A complete sender identity validates."""
    _sender().validate()


def test_missing_physical_address_is_rejected() -> None:
    """CAN-SPAM requires a postal address in every commercial email."""
    with pytest.raises(CanSpamViolationError, match="physical_address"):
        _sender(physical_address="").validate()


def test_placeholder_physical_address_is_rejected() -> None:
    """The .env placeholder must not reach production as a real address."""
    with pytest.raises(CanSpamViolationError, match="placeholder"):
        _sender(physical_address="REPLACE_ME_WITH_REAL_ADDRESS").validate()


@pytest.mark.parametrize("address", ["N/A", "None", "TBD", "short"])
def test_junk_physical_addresses_are_rejected(address: str) -> None:
    """Obvious non-addresses are refused."""
    with pytest.raises(CanSpamViolationError):
        _sender(physical_address=address).validate()


def test_malformed_from_email_is_rejected() -> None:
    """The From address must be a valid address."""
    with pytest.raises(CanSpamViolationError, match="from_email"):
        _sender(from_email="not-an-email").validate()


def test_malformed_reply_to_is_rejected() -> None:
    """Reply-To, when set, must also be valid."""
    with pytest.raises(CanSpamViolationError, match="reply_to"):
        _sender(reply_to="broken@").validate()


def test_missing_company_name_is_rejected() -> None:
    """The sending business must be identified."""
    with pytest.raises(CanSpamViolationError, match="company_name"):
        _sender(company_name="   ").validate()


@pytest.mark.parametrize(
    "subject",
    [
        "Re: our conversation",
        "RE: your enquiry",
        "Fwd: pricing",
        "Fw: proposal",
        "Your invoice is ready",
        "Your payment failed",
        "URGENT: action required",
        "Final notice regarding your account",
    ],
)
def test_deceptive_subjects_are_detected(subject: str) -> None:
    """Fake reply prefixes and false transaction framing are flagged."""
    assert is_deceptive_subject(subject) is True


@pytest.mark.parametrize(
    "subject",
    [
        "Noticed something on your website",
        "A quick question about Acme Dental's site",
        "Website review for Acme Dental",
    ],
)
def test_honest_subjects_pass(subject: str) -> None:
    """Straightforward subjects are not flagged."""
    assert is_deceptive_subject(subject) is False


def test_deceptive_subject_raises_on_validation() -> None:
    """A deceptive subject blocks the draft."""
    with pytest.raises(CanSpamViolationError, match="misrepresents"):
        validate_subject("Re: your invoice")


def test_blank_subject_is_rejected() -> None:
    """A subject line is required."""
    with pytest.raises(CanSpamViolationError, match="required"):
        validate_subject("   ")


def test_unsubscribe_token_round_trips() -> None:
    """A generated token verifies for the same draft and recipient."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    assert verify_unsubscribe_token(token, DRAFT_ID, RECIPIENT, SECRET) is True


def test_unsubscribe_token_is_case_insensitive_on_email() -> None:
    """Email casing does not break the token, since addresses are normalized."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    assert verify_unsubscribe_token(token, DRAFT_ID, RECIPIENT.upper(), SECRET) is True


def test_token_does_not_verify_for_a_different_recipient() -> None:
    """A token cannot be reused to unsubscribe someone else."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    assert verify_unsubscribe_token(token, DRAFT_ID, "someone@else.example", SECRET) is False


def test_token_does_not_verify_for_a_different_draft() -> None:
    """A token is bound to the draft it was issued for."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    other_draft_id = "22222222-2222-2222-2222-222222222222"
    assert (
        verify_unsubscribe_token(token, other_draft_id, RECIPIENT, SECRET) is False
    )


def test_token_does_not_verify_with_a_different_secret() -> None:
    """A forged token without the signing secret is rejected."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    assert verify_unsubscribe_token(token, DRAFT_ID, RECIPIENT, "wrong-secret") is False


def test_garbage_token_is_rejected() -> None:
    """A hand-constructed token does not verify."""
    assert verify_unsubscribe_token("deadbeef", DRAFT_ID, RECIPIENT, SECRET) is False


def test_blank_secret_is_rejected() -> None:
    """Signing requires a real secret."""
    with pytest.raises(ValueError, match="signing secret"):
        make_unsubscribe_token(DRAFT_ID, RECIPIENT, "  ")


def test_unsubscribe_url_carries_everything_needed() -> None:
    """One page visit must complete the opt-out, so all params are in the link."""
    token = make_unsubscribe_token(DRAFT_ID, RECIPIENT, SECRET)
    url = build_unsubscribe_url("https://api.everentechno.example", DRAFT_ID, RECIPIENT, token)

    assert url.startswith("https://api.everentechno.example/api/v1/outreach/unsubscribe")
    assert f"draft={DRAFT_ID}" in url
    assert token in url
    assert "%40" in url or "@" in url


def test_footer_contains_address_and_unsubscribe() -> None:
    """The footer carries both CAN-SPAM required elements."""
    footer = build_footer(_sender(), "https://x.example/unsub")

    assert "12 Example Street" in footer
    assert "https://x.example/unsub" in footer
    assert "Everen Techno" in footer


def test_footer_rejects_blank_unsubscribe_url() -> None:
    """A footer without an opt-out link is not a compliant footer."""
    with pytest.raises(ValueError, match="unsubscribe_url"):
        build_footer(_sender(), "  ")


def test_assembled_body_appends_footer() -> None:
    """The drafted body is preserved and the footer appended."""
    assembled = assemble_email_body("Hello there.", _sender(), "https://x.example/unsub")

    assert assembled.startswith("Hello there.")
    assert "https://x.example/unsub" in assembled
    assert "12 Example Street" in assembled


def test_assembled_body_rejects_blank_body() -> None:
    """An empty message is not sendable."""
    with pytest.raises(CanSpamViolationError, match="body"):
        assemble_email_body("   ", _sender(), "https://x.example/unsub")


def test_sendable_validation_passes_for_a_complete_email() -> None:
    """A properly assembled email passes the pre-send check."""
    url = "https://x.example/unsub"
    body = assemble_email_body("Hello there.", _sender(), url)

    validate_sendable_email(
        subject="Noticed something on your website",
        body=body,
        sender=_sender(),
        unsubscribe_url=url,
    )


def test_sendable_validation_catches_stripped_unsubscribe() -> None:
    """An edit that removed the unsubscribe link blocks the send.

    This is the regression that matters: a draft compliant at approval time
    must not become sendable after an edit strips its footer.
    """
    with pytest.raises(CanSpamViolationError, match="unsubscribe"):
        validate_sendable_email(
            subject="Noticed something on your website",
            body="Hello there. Someone deleted the footer.",
            sender=_sender(),
            unsubscribe_url="https://x.example/unsub",
        )


def test_sendable_validation_catches_stripped_address() -> None:
    """An edit that removed the postal address blocks the send."""
    url = "https://x.example/unsub"
    with pytest.raises(CanSpamViolationError, match="postal address"):
        validate_sendable_email(
            subject="Noticed something on your website",
            body=f"Hello there.\n\nUnsubscribe: {url}",
            sender=_sender(),
            unsubscribe_url=url,
        )


def test_sendable_validation_catches_deceptive_subject_at_send_time() -> None:
    """Subject deception is re-checked immediately before dispatch."""
    url = "https://x.example/unsub"
    body = assemble_email_body("Hello there.", _sender(), url)

    with pytest.raises(CanSpamViolationError, match="misrepresents"):
        validate_sendable_email(
            subject="Re: your invoice", body=body, sender=_sender(), unsubscribe_url=url
        )


def test_sender_identity_is_immutable() -> None:
    """Sender identity is frozen so a validated identity cannot be mutated."""
    sender = _sender()
    with pytest.raises(FrozenInstanceError):
        sender.physical_address = "somewhere else"  # type: ignore[misc]
