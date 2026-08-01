"""E2E tests: lead scoring (POST /api/v1/leads/{id}/score).

The Fit component runs a knowledge-base similarity search that would
otherwise call the real OpenAI embeddings API; the ``_fake_embeddings``
autouse fixture in conftest.py neutralizes that so these tests only exercise
this codebase's own logic (formula weighting + the compliance gate), matching
AGENTS.md section 11's no-live-network-calls rule for tests.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

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
        "name": "Test Business",
        "category": "dental clinics",
        "contact_email": overrides.pop("contact_email", "owner@scoring-test.example"),
        "contact_phone": "+15125550100",
        "confidence_score": 0.8,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/leads", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_score_missing_lead_is_404(e2e_client: AsyncClient) -> None:
    """Scoring a lead that does not exist is rejected, not silently defaulted."""
    response = await e2e_client.post(
        "/api/v1/leads/00000000-0000-0000-0000-000000000000/score"
    )
    assert response.status_code == 404


async def test_score_a_lead_with_no_signals_returns_neutral_defaults(
    e2e_client: AsyncClient,
) -> None:
    """A freshly created lead with no audit and no notes scores near-neutral.

    Every component without real data (Need, Fit) falls back to its
    documented neutral 0.5 rather than 0.0 -- a lead with no data yet is
    "unknown", not "bad".
    """
    lead = await _create_lead(e2e_client, category=None, notes=None)

    response = await e2e_client.post(f"/api/v1/leads/{lead['id']}/score")

    assert response.status_code == 201
    body = response.json()
    assert body["need"]["value"] == pytest.approx(0.5)
    assert body["fit"]["value"] == pytest.approx(0.5)
    assert body["gate_triggered"] is False
    assert body["label"] in ("hot", "warm", "cold")  # never do_not_contact without a trigger

    # GET the latest score returns the same computation without recomputing.
    fetched = await e2e_client.get(f"/api/v1/leads/{lead['id']}/score")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


async def test_score_history_accumulates_across_recomputes(e2e_client: AsyncClient) -> None:
    """Every POST adds a new row rather than overwriting the last one."""
    lead = await _create_lead(e2e_client, contact_email="history@scoring-test.example")

    await e2e_client.post(f"/api/v1/leads/{lead['id']}/score")
    await e2e_client.post(f"/api/v1/leads/{lead['id']}/score")

    history = await e2e_client.get(f"/api/v1/leads/{lead['id']}/score/history")
    assert history.status_code == 200
    assert history.json()["total"] == 2


async def test_compliance_gate_forces_do_not_contact_regardless_of_other_scores(
    e2e_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A do_not_contact lead scores DO_NOT_CONTACT even with strong other signals.

    This is the scoring engine's most important edge case (see the module
    docstring on app.services.lead_scoring): a triggered ComplianceGate is an
    absolute override, not just a 10%-weighted input, so a suppressed or
    consent-withdrawn lead can never surface as Hot. Also verifies the
    dedicated WARNING this override logs (app.services.lead_scoring.score_lead)
    -- a gate override is significant enough to stand out from routine INFO
    scoring activity.
    """
    lead = await _create_lead(
        e2e_client,
        contact_email="donotcontact@scoring-test.example",
        confidence_score=0.95,
        do_not_contact=True,
        do_not_contact_reason="Recipient used the unsubscribe link.",
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.lead_scoring"):
        response = await e2e_client.post(f"/api/v1/leads/{lead['id']}/score")

    assert response.status_code == 201
    body = response.json()
    assert body["gate_triggered"] is True
    assert body["label"] == "do_not_contact"
    assert body["compliance"]["value"] == pytest.approx(0.0)
    assert "unsubscribe link" in " ".join(body["gate_reasons"]).lower()

    assert any(
        record.levelname == "WARNING" and "Compliance gate triggered" in record.message
        for record in caplog.records
    )
