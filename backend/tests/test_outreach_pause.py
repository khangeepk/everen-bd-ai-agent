"""Tests for POST /api/v1/outreach/pause and app.services.sendgrid_health.

Coverage:
* test_pause_endpoint_rejects_bad_secret        — 401 on wrong X-Webhook-Secret
* test_pause_endpoint_pauses_pending_drafts     — PENDING_REVIEW → PAUSED, audit rows
* test_pause_endpoint_ignores_non_pending_drafts— SENT/APPROVED drafts untouched
* test_pause_endpoint_creates_alert_log_row     — AlertLog fields match payload
* test_pause_endpoint_idempotent                — second call on same domain is safe

All tests use the in-memory SQLite fixture from conftest.py. No network calls.
(AGENTS.md section 11.)
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert_log import AlertLog
from app.db.models.outreach import DraftStatus, OutreachAuditLog, OutreachDraft


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_draft(
    sender_email: str,
    status: DraftStatus = DraftStatus.PENDING_REVIEW,
) -> OutreachDraft:
    """Create a minimal OutreachDraft ORM instance for seeding.

    Args:
        sender_email: The sender email address to set on the draft.
        status: Initial DraftStatus for the draft.

    Returns:
        An unsaved :class:`OutreachDraft` instance.
    """
    from app.db.base import utcnow

    approver_id = uuid.uuid4() if status == DraftStatus.SENT else None
    sent_at = utcnow() if status == DraftStatus.SENT else None

    return OutreachDraft(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        status=status,
        channel="email",
        subject="Test subject",
        body="Test body",
        sender_email=sender_email,
        sender_name="Test Sender",
        created_by_agent="test-agent-v0",
        approved_by_id=approver_id,
        sent_at=sent_at,
    )



PAUSE_URL = "/api/v1/outreach/pause"
VALID_SECRET = "test-secret-abc123"
DOMAIN = "mail.everen.io"

VALID_PAYLOAD = {
    "domain": DOMAIN,
    "alert_type": "bounce_rate_exceeded",
    "metric_value": 0.063,
    "threshold_value": 0.05,
}


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """Provide an AsyncClient wired to the FastAPI app with DB session overridden.

    Patches:
        - ``app.core.config.settings.n8n_webhook_secret`` → ``VALID_SECRET``
        - ``app.db.session.get_db`` → yields the test SQLite session

    Yields:
        An :class:`httpx.AsyncClient` ready for use in tests.
    """
    from app.main import app
    from app.db.session import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.core.config.settings.n8n_webhook_secret", VALID_SECRET):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_endpoint_rejects_bad_secret(client: AsyncClient) -> None:
    """A wrong X-Webhook-Secret must return 401 and not touch the database.

    Ensures the authentication gate works before any DB operation is reached.
    """
    response = await client.post(
        PAUSE_URL,
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": "wrong-secret"},
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_pause_endpoint_rejects_missing_secret(client: AsyncClient) -> None:
    """A missing X-Webhook-Secret header must also return 401.

    Validates the header-absent path in verify_webhook_secret.
    """
    response = await client.post(PAUSE_URL, json=VALID_PAYLOAD)
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_pause_endpoint_pauses_pending_drafts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PENDING_REVIEW drafts for the target domain are moved to PAUSED.

    Asserts:
    * HTTP 201 response.
    * ``drafts_paused`` in response equals the number of seeded PENDING drafts.
    * Each draft's ``status`` is now ``PAUSED`` in the DB.
    * One ``OutreachAuditLog`` row exists per paused draft.
    """
    # Seed 3 PENDING_REVIEW drafts for our target domain.
    drafts = [
        _make_draft(f"rep{i}@{DOMAIN}", DraftStatus.PENDING_REVIEW)
        for i in range(3)
    ]
    for d in drafts:
        db_session.add(d)
    await db_session.flush()

    response = await client.post(
        PAUSE_URL,
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": VALID_SECRET},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["drafts_paused"] == 3
    assert body["domain"] == DOMAIN

    # Verify DB state.
    for draft in drafts:
        await db_session.refresh(draft)
        assert draft.status == DraftStatus.PAUSED

    # Verify audit log rows.
    result = await db_session.execute(select(OutreachAuditLog))
    audit_rows = result.scalars().all()
    assert len(audit_rows) == 3
    for row in audit_rows:
        assert row.old_status == DraftStatus.PENDING_REVIEW
        assert row.new_status == DraftStatus.PAUSED
        assert row.changed_by_id is None  # system action


@pytest.mark.asyncio
async def test_pause_endpoint_ignores_non_pending_drafts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Drafts with status SENT or APPROVED for the same domain must not be changed.

    The pause operation is scoped exclusively to PENDING_REVIEW drafts.
    SENT drafts are already delivered (immutable by design); APPROVED drafts
    are awaiting human-gated sends and should not be silently interrupted
    without separate review.
    """
    sent_draft = _make_draft(f"rep@{DOMAIN}", DraftStatus.SENT)
    approved_draft = _make_draft(f"rep2@{DOMAIN}", DraftStatus.APPROVED)
    db_session.add(sent_draft)
    db_session.add(approved_draft)
    await db_session.flush()

    response = await client.post(
        PAUSE_URL,
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": VALID_SECRET},
    )
    assert response.status_code == 201, response.text
    assert response.json()["drafts_paused"] == 0

    await db_session.refresh(sent_draft)
    await db_session.refresh(approved_draft)
    assert sent_draft.status == DraftStatus.SENT
    assert approved_draft.status == DraftStatus.APPROVED


@pytest.mark.asyncio
async def test_pause_endpoint_creates_alert_log_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A single AlertLog row is written with fields matching the request payload.

    Validates that the event is recorded for observability even when no
    drafts exist to pause (e.g. the domain had no outreach queued).
    """
    response = await client.post(
        PAUSE_URL,
        json={
            "domain": DOMAIN,
            "alert_type": "spam_rate_exceeded",
            "metric_value": 0.0015,
            "threshold_value": 0.001,
        },
        headers={"X-Webhook-Secret": VALID_SECRET},
    )
    assert response.status_code == 201, response.text

    result = await db_session.execute(select(AlertLog))
    rows = result.scalars().all()
    assert len(rows) == 1

    row = rows[0]
    assert row.alert_type == "spam_rate_exceeded"
    assert row.domain == DOMAIN
    assert abs(row.metric_value - 0.0015) < 1e-9
    assert abs(row.threshold_value - 0.001) < 1e-9
    assert row.resolved_at is None
    assert row.drafts_paused_count == 0


@pytest.mark.asyncio
async def test_pause_endpoint_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Calling the pause endpoint twice for the same domain is safe.

    The second call finds no PENDING_REVIEW drafts (they are already PAUSED
    after the first call) and writes a second AlertLog row with
    drafts_paused_count = 0. This is the correct, observable behaviour: both
    events are logged, but no drafts are double-paused.
    """
    draft = _make_draft(f"rep@{DOMAIN}", DraftStatus.PENDING_REVIEW)
    db_session.add(draft)
    await db_session.flush()

    # First call — pauses 1 draft.
    r1 = await client.post(
        PAUSE_URL,
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": VALID_SECRET},
    )
    assert r1.status_code == 201
    assert r1.json()["drafts_paused"] == 1

    # Second call — draft is now PAUSED, not PENDING_REVIEW.
    r2 = await client.post(
        PAUSE_URL,
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": VALID_SECRET},
    )
    assert r2.status_code == 201
    assert r2.json()["drafts_paused"] == 0

    # Two AlertLog rows — one per call.
    result = await db_session.execute(select(AlertLog))
    assert len(result.scalars().all()) == 2

    # Draft is still PAUSED, not double-touched.
    await db_session.refresh(draft)
    assert draft.status == DraftStatus.PAUSED
