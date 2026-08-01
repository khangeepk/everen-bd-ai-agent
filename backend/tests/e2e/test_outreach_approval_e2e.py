"""E2E tests: the outreach draft -> approval -> gated send pipeline.

This is the flow AGENTS.md section 8 treats as non-negotiable: every draft is
created ``pending_review``, only a human approval moves it to ``approved``,
and only ``POST /{id}/send`` can dispatch -- and it re-verifies status,
suppression, and quota immediately before doing so. These tests drive that
whole lifecycle through the real routes, and cover the edge cases called out
for this phase: no email on file, a duplicate approval attempt, sending
without approval, a quota (rate-limit) hit, and a simulated provider failure.

Draft *content* generation always takes its deterministic fallback here: the
OpenAI SDK is not installed in this sandbox, so
``OutreachDraftAgent._generate_with_llm`` catches the resulting ImportError
and returns None, and the agent's own fallback body is used -- this is the
codebase's documented graceful-degradation behavior, not a workaround.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.models.user import UserRole
from app.services.email_sender import EmailSendError, SendResult
from tests.e2e.conftest import set_caller_role

pytestmark = pytest.mark.asyncio


async def _create_lead(client: AsyncClient, **overrides: object) -> dict:
    """Create a lead via the API and return its JSON body.

    Args:
        client: The e2e API client.
        **overrides: Fields to override on the default payload.

    Returns:
        The created lead's response body.
    """
    payload: dict[str, object] = {
        "name": "Outreach Test Co",
        "category": "dental clinics",
        "contact_email": overrides.pop("contact_email", "prospect@outreach-test.example"),
        "contact_phone": "+15125550199",
        "confidence_score": 0.8,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/leads", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _generate_email_draft(client: AsyncClient, lead_id: str) -> dict:
    """Generate an email draft for a lead and return it.

    Args:
        client: The e2e API client.
        lead_id: The lead to draft for.

    Returns:
        The first generated draft.
    """
    response = await client.post(
        f"/api/v1/outreach/leads/{lead_id}/drafts", json={"channels": ["email"]}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["drafts"], "expected at least one draft"
    return body["drafts"][0]


async def test_full_lifecycle_generate_approve_send(e2e_client: AsyncClient) -> None:
    """The happy path: pending_review -> approved -> sent, quota decremented."""
    lead = await _create_lead(e2e_client)
    draft = await _generate_email_draft(e2e_client, lead["id"])
    assert draft["status"] == "pending_review"
    assert draft["subject"]
    assert draft["unsubscribe_url"]

    quota_before = (await e2e_client.get("/api/v1/outreach/quota")).json()

    approved = await e2e_client.post(
        f"/api/v1/outreach/drafts/{draft['id']}/approve", json={"note": "looks good"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    async def _fake_send(self, **kwargs) -> SendResult:
        return SendResult(provider_message_id="fake-provider-id-001", accepted=True)

    from app.services.email_sender import SendGridEmailSender

    original_send = SendGridEmailSender.send
    SendGridEmailSender.send = _fake_send
    try:
        sent = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/send")
    finally:
        SendGridEmailSender.send = original_send

    assert sent.status_code == 200
    sent_body = sent.json()
    assert sent_body["status"] == "sent"
    assert sent_body["provider_message_id"] == "fake-provider-id-001"

    quota_after = (await e2e_client.get("/api/v1/outreach/quota")).json()
    assert quota_after["used"] == quota_before["used"] + 1

    audit_log = await e2e_client.get(f"/api/v1/outreach/drafts/{draft['id']}/audit-log")
    transitions = [row["new_status"] for row in audit_log.json()]
    assert transitions == ["pending_review", "approved", "sent"]

    # The lead's pipeline stage advances New -> Contacted on a real send.
    # (pipeline_stage is not on LeadResponse -- see app/api/v1/pipeline.py.)
    pipeline_state = await e2e_client.get(f"/api/v1/leads/{lead['id']}/pipeline")
    assert pipeline_state.status_code == 200
    assert pipeline_state.json()["pipeline_stage"] == "contacted"


async def test_no_email_on_file_skips_the_email_channel(e2e_client: AsyncClient) -> None:
    """A lead with no contact_email produces no email draft, with a reason.

    Covers the "no email found" edge case: this is not an error, it is a
    documented blocker surfaced in the response's ``skipped`` list (see
    app.services.outreach_policy.assess_email).
    """
    lead = await _create_lead(e2e_client, contact_email=None)

    response = await e2e_client.post(
        f"/api/v1/outreach/leads/{lead['id']}/drafts", json={"channels": ["email"]}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["drafts"] == []
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["channel"] == "email"
    assert "No email address on file." in body["skipped"][0]["blockers"]


async def test_approving_an_already_approved_draft_is_rejected(e2e_client: AsyncClient) -> None:
    """A draft can only leave pending_review once (duplicate-approval edge case)."""
    lead = await _create_lead(e2e_client, contact_email="dup-approve@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])

    first = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/approve", json={})
    assert first.status_code == 200

    second = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/approve", json={})
    assert second.status_code == 409
    assert "pending_review" in second.json()["detail"]


async def test_send_without_approval_is_refused(
    e2e_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """THE GATE: a pending_review draft can never be sent (AGENTS.md section 8).

    Also verifies the gate logs a WARNING on refusal (app.api.v1.outreach.send_draft)
    -- a blocked send attempt is a security-relevant event worth a distinct log
    level from routine INFO request handling.
    """
    lead = await _create_lead(e2e_client, contact_email="no-approval@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])

    import logging

    with caplog.at_level(logging.WARNING, logger="app.api.v1.outreach"):
        response = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/send")

    assert response.status_code == 403
    assert any(
        record.levelname == "WARNING" and "Send refused" in record.message
        for record in caplog.records
    )
    assert "human must approve" in response.json()["detail"].lower()


async def test_only_an_approver_role_may_approve(
    e2e_client: AsyncClient, e2e_session_factory: async_sessionmaker
) -> None:
    """A VIEWER (non-approver) caller is refused at the approval gate."""
    lead = await _create_lead(e2e_client, contact_email="rbac@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])

    set_caller_role(UserRole.VIEWER, e2e_session_factory)
    try:
        response = await e2e_client.post(
            f"/api/v1/outreach/drafts/{draft['id']}/approve", json={}
        )
    finally:
        set_caller_role(UserRole.SALES, e2e_session_factory)

    assert response.status_code == 403


async def test_daily_quota_exhausted_blocks_send(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A send attempt that would exceed the daily cap is refused, not throttled silently.

    Covers the "rate-limit hit" edge case for this phase: the outreach daily
    send quota (app.services.send_limits.check_can_send) is this system's
    rate limit, guarding sender reputation.
    """
    monkeypatch.setattr(settings, "outreach_daily_send_limit", 0)

    lead = await _create_lead(e2e_client, contact_email="quota@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])
    await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/approve", json={})

    response = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/send")

    assert response.status_code == 409
    assert "daily send limit" in response.json()["detail"].lower()


async def test_provider_failure_marks_draft_failed_and_returns_502(
    e2e_client: AsyncClient,
) -> None:
    """A SendGrid rejection is surfaced as 502 and the draft is marked FAILED, not lost."""
    lead = await _create_lead(e2e_client, contact_email="provider-fail@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])
    await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/approve", json={})

    async def _failing_send(self, **kwargs):
        raise EmailSendError("SendGrid returned 500")

    from app.services.email_sender import SendGridEmailSender

    original_send = SendGridEmailSender.send
    SendGridEmailSender.send = _failing_send
    try:
        response = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/send")
    finally:
        SendGridEmailSender.send = original_send

    assert response.status_code == 502

    draft_after = await e2e_client.get(f"/api/v1/outreach/drafts/{draft['id']}")
    assert draft_after.json()["status"] == "failed"


async def test_suppressed_recipient_cannot_be_sent_to(e2e_client: AsyncClient) -> None:
    """Once a recipient unsubscribes, sending to them is refused even if pre-approved."""
    lead = await _create_lead(e2e_client, contact_email="suppressed@outreach-test.example")
    draft = await _generate_email_draft(e2e_client, lead["id"])
    approved = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/approve", json={})
    assert approved.status_code == 200

    unsubscribe_path = urlparse(draft["unsubscribe_url"])
    unsub_response = await e2e_client.get(
        f"{unsubscribe_path.path}?{unsubscribe_path.query}"
    )
    assert unsub_response.status_code == 200

    send_response = await e2e_client.post(f"/api/v1/outreach/drafts/{draft['id']}/send")

    assert send_response.status_code == 409
    assert "suppression list" in send_response.json()["detail"].lower()


async def test_email_draft_blocked_by_missing_canspam_config(
    e2e_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reverting the CAN-SPAM physical-address setting blocks email drafting.

    Verifies the placeholder-rejection path the ``_valid_outreach_sender``
    autouse fixture normally bypasses for every other test in this file.
    """
    monkeypatch.setattr(settings, "outreach_physical_address", "REPLACE_ME")
    lead = await _create_lead(e2e_client, contact_email="canspam-misconfig@outreach-test.example")

    response = await e2e_client.post(
        f"/api/v1/outreach/leads/{lead['id']}/drafts", json={"channels": ["email"]}
    )

    assert response.status_code == 422
    assert "OUTREACH_PHYSICAL_ADDRESS" in response.json()["detail"]


async def test_open_tracking_pixel_always_returns_the_gif(e2e_client: AsyncClient) -> None:
    """The tracking pixel returns an image even for an unknown draft_id.

    An email client must never see a broken-image icon because of internal
    tracking failures (see the route's docstring).
    """
    response = await e2e_client.get(
        "/api/v1/outreach/track/open/00000000-0000-0000-0000-000000000000.gif"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
