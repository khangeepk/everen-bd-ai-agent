"""CAN-SPAM compliance: required fields, footer construction, unsubscribe tokens.

Standard library only, so the rules that decide whether an email is legally
sendable are testable without a database, an LLM, or a mail provider.

Requirements encoded here (15 U.S.C. 7704, as enforced by the FTC):

* Accurate, non-deceptive header information and subject line.
* A working physical postal address for the sender -- a street address, a
  USPS-registered PO Box, or a registered private mailbox.
* A clear and conspicuous opt-out mechanism. The recipient must not be
  required to pay a fee, supply anything beyond their email address and
  opt-out preference, or take any step beyond a reply or visiting one web
  page.
* Opt-out requests honoured within 10 business days, and honoured
  indefinitely -- an unsubscribe never expires unless the address later
  opts back in explicitly.

This module enforces the parts that are checkable at draft time (fields
present, unsubscribe reachable in one step). The 10-business-day and
never-expires obligations are enforced by the suppression list in
:mod:`app.services.suppression`, which is permanent by design.

This is not legal advice, and CAN-SPAM is US federal law only. Recipients in
the EEA/UK are additionally subject to GDPR and PECR, which require a lawful
basis this module does not assess -- see ``consent_basis`` on
:class:`app.db.models.lead.Lead` and the compliance gate in
:mod:`app.services.lead_scoring`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

logger = logging.getLogger(__name__)

#: Opt-out requests must be honoured within this many business days.
OPT_OUT_DEADLINE_BUSINESS_DAYS = 10

#: Minimum viable postal address length. A guard against placeholder values
#: like "N/A" reaching production, not a validation of deliverability.
_MIN_ADDRESS_LENGTH = 12

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Subject-line patterns that misrepresent the message. Non-exhaustive -- a
#: deceptive-subject check cannot be complete, so this catches the obvious
#: cases and the rest is a human review responsibility.
_DECEPTIVE_SUBJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bre\s*:", re.IGNORECASE),
    re.compile(r"\bfwd?\s*:", re.IGNORECASE),
    re.compile(r"\byour\s+(?:invoice|payment|order|account)\b", re.IGNORECASE),
    re.compile(r"\b(?:urgent|action required|final notice)\b", re.IGNORECASE),
)


class CanSpamViolationError(ValueError):
    """Raised when an email draft would not satisfy CAN-SPAM.

    Deliberately an error rather than a warning: a draft missing its postal
    address or unsubscribe link must not reach the approval queue looking
    sendable.
    """


@dataclass(frozen=True)
class SenderIdentity:
    """The identified sender of a commercial email.

    Attributes:
        from_name: Display name in the From header.
        from_email: Sending address. Must be a working address.
        reply_to: Reply-To address, if different from ``from_email``.
        physical_address: Full postal address, required by CAN-SPAM.
        company_name: Legal or trading name of the sending business.
    """

    from_name: str
    from_email: str
    physical_address: str
    company_name: str
    reply_to: str | None = None

    def validate(self) -> None:
        """Check the sender identity is complete and plausible.

        Raises:
            CanSpamViolationError: If any required field is missing,
                malformed, or looks like a placeholder.
        """
        if not self.from_name.strip():
            raise CanSpamViolationError("from_name is required")
        if not _EMAIL_PATTERN.match(self.from_email.strip()):
            raise CanSpamViolationError(f"from_email {self.from_email!r} is not a valid address")
        if self.reply_to and not _EMAIL_PATTERN.match(self.reply_to.strip()):
            raise CanSpamViolationError(f"reply_to {self.reply_to!r} is not a valid address")
        if not self.company_name.strip():
            raise CanSpamViolationError("company_name is required")

        address = self.physical_address.strip()
        if len(address) < _MIN_ADDRESS_LENGTH:
            raise CanSpamViolationError(
                "physical_address is required by CAN-SPAM and must be a real postal "
                "address (street address, registered PO Box, or registered private "
                f"mailbox). Got {self.physical_address!r}."
            )
        if "REPLACE_ME" in address.upper() or address.upper() in {"N/A", "NONE", "TBD"}:
            raise CanSpamViolationError(
                f"physical_address {self.physical_address!r} looks like a placeholder"
            )


def is_deceptive_subject(subject: str) -> bool:
    """Whether a subject line looks deceptive under CAN-SPAM.

    Flags fake reply/forward prefixes and false-urgency or false-transaction
    framing on what is a cold commercial message. This is a heuristic
    backstop, not a complete test -- final judgement is the human reviewer's.

    Args:
        subject: The proposed subject line.

    Returns:
        True if the subject matches a known deceptive pattern.
    """
    return any(pattern.search(subject) for pattern in _DECEPTIVE_SUBJECT_PATTERNS)


def validate_subject(subject: str) -> None:
    """Check a subject line is present and not obviously deceptive.

    Args:
        subject: The proposed subject line.

    Raises:
        CanSpamViolationError: If the subject is blank or matches a deceptive
            pattern.
    """
    trimmed = subject.strip()
    if not trimmed:
        raise CanSpamViolationError("subject line is required and must not be blank")
    if is_deceptive_subject(trimmed):
        raise CanSpamViolationError(
            f"subject {subject!r} misrepresents the message (fake reply prefix, false "
            "urgency, or implied existing transaction), which CAN-SPAM prohibits"
        )


def make_unsubscribe_token(draft_id: str, recipient_email: str, secret: str) -> str:
    """Build a tamper-proof unsubscribe token.

    HMAC-SHA256 over the draft and recipient, so the unsubscribe endpoint can
    verify a request without a database lookup and without accepting a token
    someone constructed by hand for a different address.

    Args:
        draft_id: Identifier of the outreach draft.
        recipient_email: The recipient's address, lowercased internally.
        secret: Application signing secret.

    Returns:
        A hex digest token.

    Raises:
        ValueError: If ``secret`` is blank.
    """
    if not secret.strip():
        raise ValueError("a non-empty signing secret is required for unsubscribe tokens")

    message = f"{draft_id}:{recipient_email.strip().lower()}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_unsubscribe_token(
    token: str, draft_id: str, recipient_email: str, secret: str
) -> bool:
    """Verify an unsubscribe token in constant time.

    Args:
        token: The token from the unsubscribe link.
        draft_id: Identifier of the outreach draft.
        recipient_email: The recipient's address.
        secret: Application signing secret.

    Returns:
        True if the token is valid for this draft and recipient.
    """
    try:
        expected = make_unsubscribe_token(draft_id, recipient_email, secret)
    except ValueError:
        return False
    return hmac.compare_digest(token, expected)


def build_unsubscribe_url(base_url: str, draft_id: str, recipient_email: str, token: str) -> str:
    """Build the one-click unsubscribe URL.

    CAN-SPAM permits requiring at most a single web page visit, so every
    parameter needed to process the opt-out is carried in the link -- the
    recipient never has to log in, reply, or fill a form.

    Args:
        base_url: Public base URL of the API, without a trailing slash.
        draft_id: Identifier of the outreach draft.
        recipient_email: The recipient's address.
        token: The verification token.

    Returns:
        An absolute unsubscribe URL.
    """
    return (
        f"{base_url.rstrip('/')}/api/v1/outreach/unsubscribe"
        f"?draft={quote(draft_id)}&email={quote(recipient_email)}&token={quote(token)}"
    )


def make_erasure_token(lead_id: str, recipient_email: str, secret: str) -> str:
    """Build a tamper-proof GDPR/CCPA erasure-request token.

    Same HMAC construction as :func:`make_unsubscribe_token`, but keyed on
    ``lead_id`` rather than ``draft_id`` (an erasure request is about the
    person's whole record, not any one message) and namespaced with a
    distinct prefix so an unsubscribe token can never be replayed as an
    erasure token or vice versa.

    Args:
        lead_id: Identifier of the lead record this request would erase.
        recipient_email: The recipient's address, lowercased internally.
        secret: Application signing secret.

    Returns:
        A hex digest token.

    Raises:
        ValueError: If ``secret`` is blank.
    """
    if not secret.strip():
        raise ValueError("a non-empty signing secret is required for erasure tokens")

    message = f"erasure:{lead_id}:{recipient_email.strip().lower()}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_erasure_token(token: str, lead_id: str, recipient_email: str, secret: str) -> bool:
    """Verify an erasure-request token in constant time.

    Args:
        token: The token from the erasure-request link.
        lead_id: Identifier of the lead record.
        recipient_email: The recipient's address.
        secret: Application signing secret.

    Returns:
        True if the token is valid for this lead and recipient.
    """
    try:
        expected = make_erasure_token(lead_id, recipient_email, secret)
    except ValueError:
        return False
    return hmac.compare_digest(token, expected)


def build_erasure_url(base_url: str, lead_id: str, recipient_email: str, token: str) -> str:
    """Build the one-click "delete my data" URL for the email footer.

    Args:
        base_url: Public base URL of the API, without a trailing slash.
        lead_id: Identifier of the lead record.
        recipient_email: The recipient's address.
        token: The verification token.

    Returns:
        An absolute erasure-request URL.
    """
    return (
        f"{base_url.rstrip('/')}/api/v1/privacy/delete-request"
        f"?lead={quote(lead_id)}&email={quote(recipient_email)}&token={quote(token)}"
    )


def build_footer(
    sender: SenderIdentity, unsubscribe_url: str, erasure_url: str | None = None
) -> str:
    """Build the CAN-SPAM compliance footer appended to every commercial email.

    Args:
        sender: The validated sender identity.
        unsubscribe_url: The one-click unsubscribe URL.
        erasure_url: Optional one-click GDPR/CCPA "delete my data" URL. Not a
            CAN-SPAM requirement -- this is a good-practice addition for
            recipients who have a separate right to erasure, added alongside
            the mandatory opt-out link rather than in place of it.

    Returns:
        A plain-text footer carrying the sender identification, postal
        address, and opt-out mechanism.

    Raises:
        CanSpamViolationError: If the sender identity is incomplete.
        ValueError: If ``unsubscribe_url`` is blank.
    """
    sender.validate()
    if not unsubscribe_url.strip():
        raise ValueError("unsubscribe_url is required")

    footer = (
        "\n\n---\n"
        f"This message was sent by {sender.company_name}.\n"
        f"{sender.physical_address}\n\n"
        f"Not interested? Unsubscribe here and we will not contact you again:\n"
        f"{unsubscribe_url}\n"
    )
    if erasure_url:
        footer += (
            "\nUnder GDPR/CCPA you may request we delete your data entirely:\n"
            f"{erasure_url}\n"
        )
    return footer


def assemble_email_body(
    body: str, sender: SenderIdentity, unsubscribe_url: str, erasure_url: str | None = None
) -> str:
    """Attach the compliance footer to a drafted email body.

    Args:
        body: The LLM-drafted or human-written message body.
        sender: The validated sender identity.
        unsubscribe_url: The one-click unsubscribe URL.
        erasure_url: Optional one-click GDPR/CCPA erasure-request URL.

    Returns:
        The complete sendable body.

    Raises:
        CanSpamViolationError: If the body is blank or the sender is invalid.
    """
    if not body.strip():
        raise CanSpamViolationError("email body must not be blank")
    return body.rstrip() + build_footer(sender, unsubscribe_url, erasure_url)


def validate_sendable_email(
    subject: str, body: str, sender: SenderIdentity, unsubscribe_url: str
) -> None:
    """Run every draft-time CAN-SPAM check on an email.

    Called immediately before dispatch, so a draft that was compliant when
    approved cannot be sent after an edit stripped its footer.

    Args:
        subject: The subject line.
        body: The full assembled body, footer included.
        sender: The sender identity.
        unsubscribe_url: The unsubscribe URL expected in the body.

    Raises:
        CanSpamViolationError: If any requirement is unmet.
    """
    validate_subject(subject)
    sender.validate()

    if not body.strip():
        raise CanSpamViolationError("email body must not be blank")
    if unsubscribe_url not in body:
        raise CanSpamViolationError(
            "assembled body is missing its unsubscribe link; CAN-SPAM requires a "
            "clear and conspicuous opt-out mechanism in every commercial message"
        )
    if sender.physical_address.strip() not in body:
        raise CanSpamViolationError(
            "assembled body is missing the sender's physical postal address, which "
            "CAN-SPAM requires in every commercial message"
        )

    logger.info("Email passed CAN-SPAM validation")
