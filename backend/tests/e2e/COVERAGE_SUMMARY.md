# E2E Test Suite -- Coverage Summary

## Important caveat, up front

This suite was written and statically verified (`python -m py_compile` on
every file, plus a manual cross-check of every route path, dependency name,
and response-schema field used against the actual source) in an environment
with **no PyPI access** -- `pip install -r requirements.txt` fails with a
proxy 403 here, the same limitation noted for every prior phase of this
project. FastAPI, SQLAlchemy, httpx, and pytest are not installed, so **these
tests have not actually been executed**, and no real coverage percentage can
be generated from this environment.

Run the suite yourself with:

```bash
cd backend
pytest tests/ --cov=app --cov-report=term-missing --cov-report=html
```

(The repo's `pyproject.toml` scopes `[tool.coverage.run] source` to
`app/services`, `app/agents`, `app/core` only -- that was set for the earlier,
service-layer-only test phases. Since this new suite exercises `app/api` and
`app/db/models` end-to-end for the first time, add those to `source` if you
want the API layer's coverage reflected in the number:

```toml
[tool.coverage.run]
source = ["app/services", "app/agents", "app/core", "app/api", "app/db/models"]
```

)

## What was added

`tests/e2e/` -- 4 new test modules plus a shared `conftest.py`, none of which
touch the network (Places, PageSpeed, SSL/contact-form checks, SendGrid, and
OpenAI embeddings are always faked or monkeypatched, per AGENTS.md section
11). 31 test functions total.

| File | Tests | Flow |
|---|---|---|
| `test_lead_discovery_e2e.py` | 7 | Places search -> candidate staging -> promotion |
| `test_audit_engine_e2e.py` | 8 | Website audit run -> findings -> report |
| `test_lead_scoring_e2e.py` | 5 | Compute/fetch/history for the 5-component score |
| `test_outreach_approval_e2e.py` | 11 | Draft -> approve -> gated send, plus RBAC and the unsubscribe/tracking side-routes |

## Edge cases requested, and where each lives

| Edge case | Test(s) |
|---|---|
| No email found | `test_lead_discovery_e2e.py::test_no_email_found_promotes_lead_with_null_contact_email`, `test_outreach_approval_e2e.py::test_no_email_on_file_skips_the_email_channel` |
| Duplicate lead | `test_lead_discovery_e2e.py::test_duplicate_lead_contact_email_is_rejected`, `::test_discovery_duplicate_place_id_does_not_create_second_row`, `::test_promoting_same_candidate_twice_is_rejected` |
| API failure | `test_lead_discovery_e2e.py::test_places_api_failure_surfaces_as_502`, `test_audit_engine_e2e.py::test_pagespeed_api_failure_does_not_fail_the_whole_audit`, `::test_crawl_failure_is_recorded_but_audit_still_completes`, `test_outreach_approval_e2e.py::test_provider_failure_marks_draft_failed_and_returns_502` |
| Rate-limit hit | `test_lead_discovery_e2e.py::test_places_rate_limit_hit_surfaces_as_502`, `test_audit_engine_e2e.py::test_pagespeed_rate_limit_hit_is_recorded_as_a_non_fatal_error`, `test_outreach_approval_e2e.py::test_daily_quota_exhausted_blocks_send` |

Additional edge cases covered beyond the four requested, because they sit on
the same critical paths: non-approver RBAC on the send gate, an already-
approved draft rejected on a second approval, sending an unapproved draft,
sending to a suppressed/unsubscribed recipient, a reverted CAN-SPAM
configuration blocking draft generation, an invalid audit URL, and auditing/
scoring a nonexistent lead (404s throughout rather than silent defaults).

## Logical coverage by module (projected, pending an actual run)

These are the code paths each e2e module drives, reasoned from the source
read while writing the tests -- not measured.

* `app/services/places.py`, `app/api/v1/places.py` -- `discover()`,
  `_upsert_candidate()` (both branches), `purge_expired_coordinates` is
  **not** exercised (no e2e test calls the retention-sweep route); promotion
  success/409/null-email paths.
* `app/agents/auditor.py`, `app/api/v1/audits.py` -- `run_audit()` including
  every per-check `try/except` branch (PageSpeed success/failure x2
  strategies, SSL success, crawl success/failure, contact-form success),
  `build_fallback_report()` fully, `generate_report()`'s except-ImportError
  fallback branch (the success-with-LLM branch is **not** exercised, since
  the SDK is absent here by design), `map_findings_to_services()`.
* `app/services/lead_scoring.py`, `app/services/lead_signals.py`,
  `app/api/v1/lead_scores.py` -- `score_lead()`/`weighted_total()`/
  `label_for()` for both the gated and un-gated paths, `assess_compliance()`'s
  triggered branch, `assess_need`/`assess_fit`'s neutral-default branches for
  a signal-free lead. Not exercised: the EEA/consent-basis warning branch,
  the HOT/WARM/COLD banding boundaries with real audit-derived Need scores
  (would need a full audit fixture wired through -- out of scope for this
  pass), `assess_revenue`'s matched-service branch (knowledge base is empty
  in these tests).
* `app/agents/outreach.py`, `app/services/outreach_policy.py`,
  `app/services/canspam.py`, `app/services/send_limits.py`,
  `app/services/suppression.py`, `app/api/v1/outreach.py` -- the full
  generate -> approve -> send state machine, the send gate's every guard
  clause (not-approved, no-approver-attributed, already-sent is **not**
  separately tested but is structurally identical to the not-approved check),
  quota exhaustion, provider failure marking a draft FAILED, suppression via
  the real unsubscribe link, the CAN-SPAM placeholder rejection, and the
  tracking pixel's always-200 guarantee. Not exercised: the bounce webhook
  route, WhatsApp/call-script draft generation and their channel-eligibility
  branches (opt-in required, CTPS warning), A/B prompt-version bucketing
  through the API (covered at the unit level already in `test_ab_testing.py`
  and `test_analytics.py`).

## Structured logging changes made alongside these tests

Logging was already comprehensive across the codebase (JSON-structured via
`python-json-logger`, configured in `app/core/logging.py`; 48 of 77
`app/` modules already call `logger.info/warning/exception`). One gap was
found and closed:

* `app/services/lead_scoring.py::score_lead` now emits a `WARNING` (not just
  the existing `INFO`) when the compliance gate overrides a lead's label to
  `do_not_contact` -- previously this was only visible by inspecting the
  `gate_triggered` field on the routine `INFO` log line, which would not
  stand out in a log stream the way a suppressed-and-overridden lead
  arguably should.

Three of the new e2e tests assert directly on emitted log records (via
`caplog`), rather than only asserting HTTP outcomes, so the logging itself is
regression-tested, not just present:

* `test_lead_scoring_e2e.py::test_compliance_gate_forces_do_not_contact_regardless_of_other_scores` -- asserts the new WARNING fires.
* `test_outreach_approval_e2e.py::test_send_without_approval_is_refused` -- asserts the existing "Send refused" WARNING in `app/api/v1/outreach.py::send_draft` fires.
* `test_lead_discovery_e2e.py::test_places_rate_limit_hit_surfaces_as_502` -- asserts the existing "Places discovery failed" ERROR in `app/api/v1/places.py::search_places` fires.
