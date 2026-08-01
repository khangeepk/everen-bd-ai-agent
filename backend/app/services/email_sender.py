"""Email transport.

Deliberately thin and behind a Protocol: this is the only module in the
codebase that can actually put a message in front of a prospect, so it stays
small enough to audit at a glance.

It performs no authorization of its own. The caller
(``POST /api/v1/outreach/{draft_id}/send``) is responsible for verifying
``status == APPROVED``, checking suppression, and checking the daily quota
before invoking this. That separation is intentional -- the send gate lives in
one place, not spread across the transport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"


class EmailSendError(RuntimeError):
    """Raised when the provider rejects or cannot accept a message."""


@dataclass(frozen=True)
class SendResult:
    """Provider acceptance of a message.

    Attributes:
        provider_message_id: Identifier for correlating later bounce events.
        accepted: Whether the provider accepted the message for delivery.
            Acceptance is not delivery -- bounces arrive asynchronously.
    """

    provider_message_id: str | None
    accepted: bool


@runtime_checkable
class EmailSender(Protocol):
    """Interface for anything that can dispatch an email."""

    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        from_email: str,
        from_name: str,
        reply_to: str | None = None,
        unsubscribe_url: str | None = None,
    ) -> SendResult:
        """Dispatch one email.

        Args:
            to_email: Recipient address.
            subject: Subject line.
            body: Plain-text body, footer already attached.
            from_email: Sender address.
            from_name: Sender display name.
            reply_to: Reply-To address, if different.
            unsubscribe_url: URL for List-Unsubscribe headers.

        Returns:
            The provider's acceptance result.
        """
        ...


class SendGridEmailSender:
    """SendGrid v3 Mail Send client."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        sandbox_mode: bool | None = None,
    ) -> None:
        """Initialize the sender.

        Args:
            api_key: SendGrid API key. Defaults to settings.
            timeout_seconds: Per-request timeout.
            sandbox_mode: Force SendGrid sandbox mode on/off for this
                instance. Defaults to ``settings.sendgrid_sandbox_mode`` when
                not given, so a soft-launch/test window can enable it via
                config without every call site needing to know about it.
        """
        self._api_key = api_key or settings.sendgrid_api_key
        self._timeout = timeout_seconds
        self._sandbox_mode = (
            settings.sendgrid_sandbox_mode if sandbox_mode is None else sandbox_mode
        )

    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        from_email: str,
        from_name: str,
        reply_to: str | None = None,
        unsubscribe_url: str | None = None,
    ) -> SendResult:
        """Dispatch one email via SendGrid.

        Sets ``List-Unsubscribe`` and ``List-Unsubscribe-Post`` headers in
        addition to the in-body link, so mailbox providers can surface a
        native one-click unsubscribe. This reduces spam complaints, which is
        both a deliverability win and a compliance one.

        Args:
            to_email: Recipient address.
            subject: Subject line.
            body: Plain-text body, footer already attached.
            from_email: Sender address.
            from_name: Sender display name.
            reply_to: Reply-To address, if different.
            unsubscribe_url: URL for the List-Unsubscribe headers.

        Returns:
            The provider's acceptance result.

        Raises:
            EmailSendError: On transport failure or a non-2xx response.
        """
        payload: dict = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email, "name": from_name},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        if reply_to:
            payload["reply_to"] = {"email": reply_to}
        if unsubscribe_url:
            payload["headers"] = {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        if self._sandbox_mode:
            # SendGrid fully validates the request (auth, payload shape,
            # sender verification) and returns the same 202 it would for a
            # real send, but never enqueues the message for delivery and
            # never counts it against sending limits or reputation. This is
            # the whole point of a soft-launch test batch: every other gate
            # (approval, suppression, quota, CAN-SPAM validation) still runs
            # for real, only the actual mail delivery is skipped.
            payload["mail_settings"] = {"sandbox_mode": {"enable": True}}
            logger.info(
                "SendGrid sandbox mode active -- message will be validated but not delivered",
                extra={"to_email": to_email},
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    SENDGRID_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "SendGrid rejected the message", extra={"status": exc.response.status_code}
            )
            raise EmailSendError(
                f"SendGrid returned {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("SendGrid unreachable")
            raise EmailSendError(f"SendGrid request failed: {exc}") from exc

        # Sandbox-mode responses typically omit X-Message-Id since no message
        # is actually queued -- provider_message_id staying None in that case
        # is expected, not a parsing failure.
        message_id = response.headers.get("X-Message-Id")
        logger.info(
            "Email accepted by provider",
            extra={"provider_message_id": message_id, "sandbox_mode": self._sandbox_mode},
        )
        return SendResult(provider_message_id=message_id, accepted=True)
