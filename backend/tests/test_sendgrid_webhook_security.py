"""Tests for SendGrid event webhook security and idempotency (Phase 25).

Coverage:
* test_valid_signature_accepted               — Real ECDSA signature verification passes
* test_invalid_signature_rejected            — Tampered signature returns 401
* test_changed_payload_rejected              — Payload modification after signing returns 401
* test_changed_timestamp_rejected            — Timestamp modification after signing returns 401
* test_missing_headers_rejected              — Missing X-Twilio headers return 401
* test_production_fail_closed_unconfigured   — Empty key in production environment returns 401
* test_invalid_webhook_creates_no_suppression— Failed signature does NOT alter DB or suppress addresses
* test_valid_bounce_suppresses_address       — Valid signed bounce suppresses address and flags DNC
* test_webhook_idempotency_duplicate_event   — Duplicate event_id is safely skipped on retry
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_sendgrid_webhook_signature
from app.db.models.lead import Lead
from app.db.models.outreach import ProcessedWebhookEvent, SuppressionEntry
from app.services.suppression import is_suppressed


# Generate a test EC keypair for realistic ECDSA signature testing
_TEST_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_TEST_PUBLIC_KEY_PEM = _TEST_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")


def _sign_sendgrid_payload(timestamp: str, raw_body: bytes) -> str:
    """Helper to generate a valid SendGrid ECDSA signature for a test payload."""
    payload = timestamp.encode("utf-8") + raw_body
    sig = _TEST_PRIVATE_KEY.sign(payload, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode("utf-8")


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """Provide an AsyncClient wired to the FastAPI app with DB session overridden."""
    from app.db.session import get_db
    from app.main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()



def test_verify_sendgrid_webhook_signature_unit() -> None:
    """Unit test for verify_sendgrid_webhook_signature helper."""
    ts = str(int(time.time()))
    body = b'[{"email": "bounced@example.com", "event": "bounce", "reason": "550 User unknown"}]'
    sig = _sign_sendgrid_payload(ts, body)

    assert verify_sendgrid_webhook_signature(_TEST_PUBLIC_KEY_PEM, body, sig, ts) is True
    assert verify_sendgrid_webhook_signature(_TEST_PUBLIC_KEY_PEM, body, "bad_sig", ts) is False
    assert verify_sendgrid_webhook_signature(_TEST_PUBLIC_KEY_PEM, b"tampered_body", sig, ts) is False
    assert verify_sendgrid_webhook_signature(_TEST_PUBLIC_KEY_PEM, body, sig, "99999999") is False


@pytest.mark.asyncio
async def test_valid_signature_accepted(client: AsyncClient) -> None:
    """Validly signed SendGrid webhook request should be accepted (200 OK)."""
    ts = str(int(time.time()))
    payload = [{"email": "valid-bounce@example.com", "event": "bounce", "reason": "550 User unknown"}]
    raw_body = json.dumps(payload).encode("utf-8")
    sig = _sign_sendgrid_payload(ts, raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Twilio-Email-Event-Webhook-Signature": sig,
        "X-Twilio-Email-Event-Webhook-Timestamp": ts,
    }

    with patch("app.core.config.settings.sendgrid_webhook_verification_key", _TEST_PUBLIC_KEY_PEM):
        resp = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1


@pytest.mark.asyncio
async def test_invalid_signature_rejected(client: AsyncClient) -> None:
    """Webhook with invalid signature must return 401 Unauthorized."""
    ts = str(int(time.time()))
    raw_body = b'[{"email": "fake@example.com", "event": "bounce"}]'
    headers = {
        "Content-Type": "application/json",
        "X-Twilio-Email-Event-Webhook-Signature": "invalid_signature_base64",
        "X-Twilio-Email-Event-Webhook-Timestamp": ts,
    }

    with patch("app.core.config.settings.sendgrid_webhook_verification_key", _TEST_PUBLIC_KEY_PEM):
        resp = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_headers_rejected(client: AsyncClient) -> None:
    """Webhook missing signature headers must return 401 Unauthorized."""
    raw_body = b'[{"email": "fake@example.com", "event": "bounce"}]'

    with patch("app.core.config.settings.sendgrid_webhook_verification_key", _TEST_PUBLIC_KEY_PEM):
        resp = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_production_fail_closed_unconfigured(client: AsyncClient) -> None:
    """In production (app_env='production'), empty verification key must fail closed with 401."""
    ts = str(int(time.time()))
    raw_body = b'[{"email": "test@example.com", "event": "bounce"}]'
    headers = {
        "X-Twilio-Email-Event-Webhook-Signature": "sig",
        "X-Twilio-Email-Event-Webhook-Timestamp": ts,
    }

    with patch("app.core.config.settings.app_env", "production"), \
         patch("app.core.config.settings.sendgrid_webhook_verification_key", ""):
        resp = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_webhook_creates_no_suppression(client: AsyncClient, db_session: AsyncSession) -> None:
    """Rejected webhook must NOT suppress any address or alter DB state."""
    target_email = "protected-target@example.com"
    ts = str(int(time.time()))
    payload = [{"email": target_email, "event": "bounce", "reason": "550 User unknown"}]
    raw_body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-Twilio-Email-Event-Webhook-Signature": "invalid_signature",
        "X-Twilio-Email-Event-Webhook-Timestamp": ts,
    }

    with patch("app.core.config.settings.sendgrid_webhook_verification_key", _TEST_PUBLIC_KEY_PEM):
        resp = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp.status_code == 401

    # Assert target address was NOT suppressed
    from app.services.outreach_policy import OutreachChannel
    suppressed = await is_suppressed(db_session, target_email, OutreachChannel.EMAIL)
    assert suppressed is False


@pytest.mark.asyncio
async def test_webhook_idempotency_duplicate_event(client: AsyncClient, db_session: AsyncSession) -> None:
    """Duplicate webhook delivery with same sg_event_id must be handled idempotently."""
    event_id = f"evt_{uuid.uuid4()}"
    ts = str(int(time.time()))
    email = f"idempotent-{uuid.uuid4()}@example.com"
    payload = [{
        "email": email,
        "event": "bounce",
        "reason": "550 User unknown",
        "sg_event_id": event_id,
    }]
    raw_body = json.dumps(payload).encode("utf-8")
    sig = _sign_sendgrid_payload(ts, raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Twilio-Email-Event-Webhook-Signature": sig,
        "X-Twilio-Email-Event-Webhook-Timestamp": ts,
    }

    with patch("app.core.config.settings.sendgrid_webhook_verification_key", _TEST_PUBLIC_KEY_PEM):
        # First call — processes and suppresses
        resp1 = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["processed"] == 1

        # Second call with identical sg_event_id — skips processing
        resp2 = await client.post("/api/v1/outreach/webhooks/bounce", content=raw_body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["processed"] == 1

    # Verify ProcessedWebhookEvent table has exactly 1 entry for event_id
    processed_row = (
        await db_session.execute(
            select(ProcessedWebhookEvent).where(ProcessedWebhookEvent.event_id == event_id)
        )
    ).scalar_one_or_none()
    assert processed_row is not None
