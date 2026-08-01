# Everen BD Agent — Master Progress Log

> Snapshot of everything built through Phase 6.
> Companion to `AGENTS.md` (rules) and `CLAUDE.md` (agent guidance) at the project root.

---

## Status: Phases 0–6 complete

| Phase | Module | Status |
|-------|--------|--------|
| 0 | Repo scaffold (Antigravity) | Done — inherited |
| 1 | Services Knowledge Base + RAG recommendation + Auth + Leads schema | Done |
| 2 | Location + industry lead search (Google Places integration) | Done |
| 3 | Website Audit Agent + Social Presence Reviewer + LLM report | Done |
| 6 | CRM pipeline + reply classifier + call-center card generator | Done (see below) |
| 7 | Analytics dashboard + A/B test tracking + prompt version log | Done (see below) |
| 4 | Lead scoring engine (Need/Fit/Contactability/Revenue/ComplianceRisk) | Done |
| 5 | Outreach draft generator + human-approval queue + CAN-SPAM + limits | Done |
| 6 | CRM pipeline + reply classifier + Hot-lead call-center card | **Next up** |

Backend lives at `backend/` (FastAPI, Python 3.11+, async, PostgreSQL + pgvector,
SQLAlchemy 2.x, Alembic). No frontend yet. All work follows `AGENTS.md`.

---

## Phase 1 — Knowledge Base, RAG, Auth, Leads

**Services Knowledge Base (RAG)**
- `app/db/models/knowledge_base.py`: `Service`, `PortfolioItem`, `KnowledgeChunk` (pgvector
  `Vector(1536)`, HNSW cosine index).
- `app/db/types.py`: `EmbeddingVector` — dialect-aware column, degrades to JSON text on
  SQLite so tests run without Postgres.
- `app/services/chunking.py`, `app/services/similarity.py`, `app/services/embeddings.py`
  (OpenAI `text-embedding-3-small`, 1536-dim), `app/services/knowledge_base.py`
  (ingest/reindex/search, chunk-to-service collapsing).
- `app/agents/recommender.py`: RAG service recommendation, grounded strictly in retrieved
  content, deterministic fallback if the LLM is down.
- Seed data: `app/db/seed.py` — **placeholder** Everen Techno services/pricing/portfolio.
  Replace before client-facing use.
- Routes: `app/api/v1/services.py` (`/api/v1/services*`).

**Auth**
- JWKS-based RS256 JWT verification (`app/core/security.py`, `app/core/claims.py`) — works
  with Clerk *or* Auth.js via env vars, no vendor SDK. Satisfies AGENTS.md §2's JWT
  requirement.
- `app/api/deps.py`: `get_current_user` (JIT user provisioning), `require_approver` (RBAC
  gate for outreach approval).
- `app/db/models/user.py`: `User`, `UserRole` (admin/bd_manager/bd_rep/viewer).

**Leads schema**
- `app/db/models/lead.py`: `Lead` — name, category, contact fields, source, status,
  confidence_score, plus (added later) `do_not_contact`, `consent_basis`,
  `whatsapp_opt_in`.
- Routes: `app/api/v1/leads.py` (`/api/v1/leads*`).

---

## Phase 2 — Google Places lead discovery

**Compliance-driven design.** Verified Google Maps Platform Service Specific Terms §10.3:
only `place_id` (indefinite) and lat/lng (30-day max) may be cached from the Places API.
Business name, address, phone, website are Google Maps Content and must **never** be
persisted.

- `app/services/places_policy.py`: enforced allowlist (`assert_persistable` raises on a
  restricted field), TTL math, dedup key = `place_id` (not name+address).
- `app/db/models/place.py`: `PlaceSearch`, `PlaceCandidate` (no name/address column by
  design).
- `app/services/places.py`: Places API (New) `searchText` client, transient
  `PlaceSearchResult` (display-only) vs. persistent `PlaceCandidate`.
- `app/tasks/places_retention.py`: scheduled sweeper to purge coordinates past 30 days —
  **must be scheduled in Celery beat**, not yet wired.
- Routes: `app/api/v1/places.py` — `/search`, `/candidates`,
  `/candidates/{id}/promote` (promotion requires human-supplied contact data + named
  `enrichment_source`), `/retention/sweep`.
- `LeadSource.GOOGLE_PLACES` added.

---

## Phase 3 — Website Audit + Social Presence Reviewer

**Website Audit Agent**
- `app/services/pagespeed_parsing.py` / `pagespeed.py`: PageSpeed Insights v5 (Lighthouse)
  client — performance/SEO/accessibility/best-practices, mobile + desktop.
- `app/services/site_checks.py`: direct SSL/TLS check, contact-form detection + HEAD-only
  endpoint probe (**never submits forms**), bounded robots-respecting link crawler
  (`USER_AGENT` in this file is a placeholder — needs a real contact URL before
  production).
- `app/services/web_parsing.py`: stdlib HTML parsing (forms, links, SEO meta).
- `app/services/audit_scoring.py`: `Finding`, `Severity`, grading, weighted `health_score`.
- `app/db/models/audit.py`: `WebsiteAudit`, `AuditFinding`, `AuditReport`.

**Social Presence Reviewer — no scraping**
- LinkedIn/Instagram/Facebook gate profile data behind owner OAuth; no compliant API for
  arbitrary businesses. Solution: `app/services/social_review.py` scores a structured
  `ProfileChecklist` filled in by a human reviewer. Swappable for consented OAuth later
  without touching scoring logic.
- `SocialProfileReview` model; routes: `PUT /api/v1/audits/leads/{id}/social`.

**Report agent**
- `app/agents/auditor.py`: `WebsiteAuditAgent` — runs all checks, maps findings to
  services via KB search, generates business-friendly LLM report (deterministic fallback
  on LLM failure). Audits are **rep-triggered per lead only** — no bulk/automatic path.
- Routes: `app/api/v1/audits.py` — `POST /api/v1/audits`, `GET /api/v1/audits/{id}`.

---

## Phase 4 — Lead scoring engine

`total = 0.30·Need + 0.25·Fit + 0.20·Contactability + 0.15·Revenue + 0.10·ComplianceRisk`
→ Hot (≥0.75) / Warm (≥0.50) / Cold / Do-Not-Contact.

**Key design decision:** ComplianceRisk is a **hard gate**, not just a 10% weight. As
originally specified, a lead scoring ~0.95 on the other four components would total ~0.86
(Hot) even with ComplianceRisk at 0.0. Now `Lead.do_not_contact=True` forces the
`do_not_contact` label regardless of the weighted total; the 10% weight still applies to
the stored total when the gate doesn't trigger. See
`test_end_to_end_gate_beats_a_near_perfect_lead` in `tests/test_lead_scoring.py`.

- `app/services/lead_scoring.py`: pure formula, gate, banding (stdlib, offline-tested).
- `app/services/lead_signals.py`: wires the five components to real data —
  - Need: inverse of website audit health (70%) + social score (30%)
  - Fit: KB similarity search on lead category/notes
  - Contactability: weighted channel presence (email 50/phone 25/LinkedIn 15/website 10)
    blended with discovery confidence
  - Revenue: matched service price midpoint normalized against
    `LEAD_SCORE_REVENUE_SCALE_MIN/MAX`
  - ComplianceRisk: 1.0 minus penalties for missing `consent_basis` (heavier in
    EEA/UK), gate on `do_not_contact`
- `app/db/models/lead_score.py`: `LeadScore` — every computation stored as a new row
  (audit trail), never updated in place.
- Routes: `app/api/v1/lead_scores.py` — `POST/GET /api/v1/leads/{id}/score`,
  `GET .../score/history`. Compute-on-demand (not automatic — Fit calls the embeddings
  API on every run).

---

## Phase 5 — Outreach draft generator + approval queue

**Regulatory findings that shaped the design (verified via web search):**
- CAN-SPAM requires: sender ID, real physical postal address, one-click unsubscribe,
  opt-outs honored within 10 business days and **indefinitely**.
- Meta's WhatsApp Business Messaging Policy requires opt-in **before** any
  business-initiated message, must use an approved Message Template, and (since March
  2026) applies **preemptive enforcement** against accounts with rapid contact-list
  growth + high send velocity + low engagement — exactly the Places-discovery →
  bulk-WhatsApp pattern. **Cold WhatsApp outreach is not available on this system.**

**The human-approval gate (AGENTS.md §8) — verified with grep, not just asserted:**
- `app/db/models/outreach.py`: `OutreachDraft` always created `pending_review`.
  DB-level CHECK constraints back the workflow: `status='sent'` requires both `sent_at`
  AND `approved_by_id`.
- `POST /api/v1/outreach/drafts/{id}/send` is the **only** dispatch path — one call site
  for the email sender, one place `sent_at` is set, one place status becomes `APPROVED`
  (behind `require_approver`). No Celery task touches any of it.
- `OutreachAuditLog`: every status transition recorded, insert-only.

**CAN-SPAM enforcement**
- `app/services/canspam.py`: `SenderIdentity.validate()` rejects placeholder/missing
  physical address; HMAC-signed unsubscribe tokens; deceptive-subject screening
  (fake `Re:`/`Fwd:`, false urgency); `validate_sendable_email` re-checked **immediately
  before dispatch** so an edit that strips the footer blocks the send.
- `OUTREACH_PHYSICAL_ADDRESS` / `OUTREACH_PUBLIC_BASE_URL` must be set to real values or
  drafting fails outright (fail loud, not silent).

**Channel eligibility**
- `app/services/outreach_policy.py`: per-channel gate.
  - Email: allowed (CAN-SPAM), blocked if suppressed/hard-bounced/no address; GDPR
    warning (not block) if EEA/UK + no `consent_basis`.
  - WhatsApp: **hard-blocked without `whatsapp_opt_in`** — no draft generated at all,
    reason surfaced in `skipped[]`.
  - Call script: lower bar (human reads it, nothing transmitted); UK leads get a CTPS
    warning (covers business numbers, unlike US DNC).

**Suppression & bounces — permanent, by design**
- `app/services/suppression.py`: `suppress()` exists; deliberately **no unsuppress /
  bulk-clear** function — CAN-SPAM opt-outs never expire.
- Hard bounce / spam complaint / unsubscribe → suppresses the address **and** sets
  `Lead.do_not_contact=True`, which flows straight into the Phase 4 scoring gate.
- `app/services/send_limits.py`: daily quota math (`OUTREACH_DAILY_SEND_LIMIT`, default
  50) — deliverability control first, blast-radius control second. Atomic upsert counter
  (`ON CONFLICT DO UPDATE`) avoids race conditions under concurrent sends.
- Bounce webhook (`POST /api/v1/outreach/webhooks/bounce`) is **unauthenticated** —
  needs SendGrid signature verification added before production.

**Draft generation agent**
- `app/agents/outreach.py`: `OutreachDraftAgent` — email/WhatsApp/call-script grounded in
  latest audit findings + best-matched service. LLM-generated with deterministic
  fallback. Never sends; only ever produces `pending_review` drafts.

---

## Test suite

Offline-runnable subset (stdlib-only modules, run via a minimal stdlib pytest shim since
this sandbox has no PyPI access): **322 passing, 0 failing** as of Phase 5, covering
chunking, similarity, JWT claims, Places policy, audit scoring, HTML parsing, social
review, PageSpeed parsing, lead scoring formula (incl. the compliance-gate regression),
CAN-SPAM, outreach channel policy, send limits/bounce classification.

DB-backed tests (models, knowledge base ingestion, recommender, lead signals, places
discovery) are written against the same `db_session` (in-memory SQLite) /
`fake_embedder` fixtures but need SQLAlchemy installed to execute — run
`pip install -r requirements.txt && pytest` locally to run the full suite.

---

## Known gaps / things to do before production

1. **Places retention sweeper not scheduled** — `app/tasks/places_retention.py` exists
   but nothing calls it on a cron; coordinates can silently exceed the 30-day limit.
2. **Crawler `USER_AGENT` is a placeholder** (`app/services/site_checks.py`) — needs a
   real contact URL before crawling any prospect's site.
3. **Bounce webhook has no signature verification** — anyone who learns the URL can
   currently suppress arbitrary addresses.
4. **Seed KB data is fabricated** — replace `app/db/seed.py` with real Everen Techno
   services/pricing/portfolio before any client-facing use.
5. **`.env` values are placeholders** — `OUTREACH_PHYSICAL_ADDRESS`,
   `OUTREACH_PUBLIC_BASE_URL`, `GOOGLE_PLACES_API_KEY`, `PAGESPEED_API_KEY`,
   `OPENAI_API_KEY`, auth JWKS settings all need real values.
6. **No frontend yet.** A dashboard mockup was shared once; build was deferred pending
   this backend work. Revisit if still wanted.
7. Full pytest run (DB-backed tests) has not been executed in this environment due to no
   PyPI access — run locally to confirm before merging.

---

## Phase 6 — CRM pipeline, reply classifier, call-center cards (complete)

**Pipeline stage machine** (`app/services/pipeline.py`, stdlib only): New → Contacted →
Interested → Hot → Converted, with Lost reachable from any open stage. Transitions are
validated against a directed graph (`InvalidTransitionError` on a rejected move); a
`force` escape hatch exists for approver-level correction. `next_stage_towards()` walks
a lead one valid step at a time when a classified reply implies a stage further along
than a direct transition permits (e.g. a New lead's first reply asking to book a call
advances to Contacted, not straight to Hot).

Deliberately **not merged** with the pre-existing `Lead.status` (`LeadStatus`): `status`
tracks BD-process state (enriching, qualified, disqualified...); `pipeline_stage` tracks
reply-driven conversation progress. Synced only at the terminal edges — pipeline
Converted → status WON, pipeline Lost → status LOST — via
`app/services/pipeline_transitions.py`. Same naming caution applies to
`PipelineStage.HOT` vs. `ScoreLabel.HOT` (Phase 4's lead-quality label): a lead can score
Cold while sitting in pipeline Hot (asked for a call, but a poor-fit prospect) and vice
versa. Both are shown separately on purpose.

**Reply classifier**: `app/services/reply_classification.py` (stdlib model + keyword
fallback) and `app/agents/reply_classifier.py` (LLM wrapper, `ReplyClassifierAgent`).
Categories: book_call, pricing, interested, not_interested, unclear. Opt-out language is
checked first regardless of what else is in the message (a decline plus a pricing
question still classifies not_interested). Unclear never auto-advances the pipeline —
it's a signal to route to a human, not a guess.

**Call-center card generator**: `app/agents/call_card.py` (`CallCenterCardAgent`),
triggered automatically the moment a lead enters pipeline stage Hot (`_maybe_generate_
call_card` in `app/api/v1/pipeline.py`), also available on demand via
`POST /leads/{id}/call-card/regenerate`. Reuses `OutreachDraftAgent`'s findings/service
selection and call-script generation (two new public methods added there:
`top_findings`, `best_service`, plus a new `generate_call_script` that bypasses channel
eligibility since a card is an internal briefing, not an outbound send) rather than
duplicating that logic. Card contents: contact snapshot, problems (audit findings),
recommended service, full chronological message history (merged inbound messages + sent
outreach drafts), suggested call script. A new row is written each time rather than
updated in place, so a rep can see what a card looked like at the point they acted on it.

**New DB models** (`app/db/models/pipeline.py`): `PipelineEvent` (insert-only transition
log, mirrors `OutreachAuditLog`'s pattern), `InboundMessage` (logged replies + LLM
classification), `CallCenterCard`. `Lead.pipeline_stage` column added
(migration `0006_pipeline`).

**New routes** (`app/api/v1/pipeline.py`, prefixed `/leads`): `GET /{id}/pipeline`,
`PATCH /{id}/pipeline/stage` (manual/forced move, force requires approver role),
`POST /{id}/messages` (log + auto-classify + auto-advance by default),
`POST /{id}/messages/{message_id}/classify` (re-classify), `GET /{id}/call-card`,
`POST /{id}/call-card/regenerate`. Sending an outreach draft to a New lead now also
auto-advances it to Contacted (hooked into the existing `POST /outreach/drafts/{id}/send`
in `app/api/v1/outreach.py`).

**Tests**: `test_pipeline.py` and `test_reply_classification.py` (stdlib-only, run
offline in this environment — all passing, 153/153 total across the full offline suite
including prior phases). `test_pipeline_transitions.py` covers stage-change side effects
(status sync, Hot detection), reply-driven advancement, outreach-sent advancement, and
full card generation against the in-memory SQLite fixture — written in full but not
executed here (no SQLAlchemy/FastAPI available in this sandbox, consistent with every
prior phase); run with `pytest` locally to confirm.

### Known gaps carried forward from Phase 6

- `PipelineEvent.triggered_by_id` / `InboundMessage.logged_by_id` reference `users.id`,
  but there's no admin UI yet for reviewing pipeline history — it's API-only.
- Call-card regeneration is unguarded by rate limiting; a rep hammering "regenerate"
  will re-run the LLM call script generation each time.
- No Celery task re-tries classification if the LLM call fails and the keyword fallback
  produces `unclear` — a low-confidence reply currently just sits until a human notices.

---

## Phase 7 — Analytics dashboard, A/B testing, prompt version log (complete)

**Analytics** (`app/services/analytics_math.py`, stdlib, + `app/services/analytics.py`,
DB-aware, + `app/api/v1/analytics.py`): emails sent, open rate, reply rate, meetings
booked, deals won, top industries/services — all optionally filtered to a date range.
Metric definitions are documented in the `analytics.py` module docstring since a
dashboard number only means something if its definition is known; the two worth
flagging directly:

- **Open rate will read 0 for now.** The tracking pixel and `EmailOpenEvent` model are
  built and working end-to-end, but `app/services/canspam.py` / `SendGridEmailSender`
  currently send **plain-text** email only — there's no `<img>` tag for a pixel to be
  in. Switching to HTML email (and giving the CAN-SPAM footer/unsubscribe link an HTML
  form) is a separate decision I didn't make unilaterally; the infrastructure is ready
  whenever that's wanted.
- **Reply rate is a lead-level approximation**, not per-draft attribution: distinct
  leads who sent any inbound message, divided by distinct leads who received any sent
  email, both within the period. Precise "replied to this specific draft" attribution
  would need reply-header/threading matching, which is out of scope here.
- **Top industries/services rank by won deals**, not raw lead volume — an industry you
  contact often but never close isn't "top" on a BD dashboard.
- An open-tracking pixel is a "tracking technology" under EU ePrivacy rules. Fine for
  this internal, aggregate, legitimate-interest use case (analytics on mail we sent),
  flagged here rather than silently wired in.

**A/B testing + prompt version log**: new `PromptVersion` table (`app/db/models/
analytics.py`) — every system prompt an agent has used for a channel is a row, with
`is_active` marking the live one(s). Two active rows sharing a non-null
`experiment_group` are an A/B test. `OutreachDraftAgent._resolve_prompt()` (in
`app/agents/outreach.py`) checks for active versions before falling back to its
existing hardcoded prompts; when two active versions share a group, the lead is
deterministically bucketed between them via `app/services/ab_testing.py` (stdlib,
hash-based on the lead's ID) so the same lead always sees the same variant across
multiple touches. The chosen `prompt_version_id` and `ab_variant` are stamped on the
resulting `OutreachDraft`, and `get_prompt_version_performance()` rolls up sent/opened/
replied/meetings/won per version — drafts generated before any `PromptVersion` existed
bucket under `"(code-default prompt)"`, which is exactly the old-vs-new comparison
requested.

**New routes** (`app/api/v1/analytics.py`, prefixed `/analytics`): `GET /overview`,
`GET /top-industries`, `GET /top-services`, `GET /prompt-performance`,
`GET /prompt-versions`, `POST /prompt-versions` (approver-only — creating one changes
what live outreach generation uses). New tracking endpoint in
`app/api/v1/outreach.py`: `GET /outreach/track/open/{draft_id}.gif` (public, always
returns a 1x1 GIF even for an unknown draft, records an `EmailOpenEvent`).

**New DB models/columns**: `PromptVersion`, `EmailOpenEvent`
(`app/db/models/analytics.py`); `OutreachDraft.prompt_version_id` and
`OutreachDraft.ab_variant` (migration `0007_analytics_ab_testing`).

**Tests**: `test_ab_testing.py` and `test_analytics_math.py` (stdlib-only, run offline —
all passing, 363/364 across the full offline-runnable suite; the one failure is
`test_models.py`, which needs SQLAlchemy and was never offline-runnable to begin with).
`test_analytics.py` covers overview counting/date-range/open-rate/reply-rate,
meetings/deals, top industries/services, and prompt-version bucketing against the
in-memory SQLite fixture — written in full but not executed here (no SQLAlchemy/FastAPI
in this sandbox, same constraint as every prior phase); run with `pytest` locally.

### Known gaps carried forward from Phase 7

- Outreach email is plain-text only, so open-rate tracking is wired but inert until
  that changes (see above).
- No admin UI for creating/reviewing `PromptVersion` rows or reading the dashboard —
  API-only for now.
- `POST /prompt-versions` doesn't validate that a submitted prompt actually functions
  (e.g. doesn't reference the grounding placeholders the agent expects) — a bad prompt
  version is caught only by its live performance metrics after the fact, not at write time.

## E2E test suite + logging hardening (post-Phase 7)

Requested: end-to-end tests for lead discovery, the audit engine, lead scoring, and the
outreach approval flow, covering no-email-found, duplicate lead, API failure, and
rate-limit-hit edge cases, plus structured logging and a coverage summary.

**New**: `backend/tests/e2e/` — a real `httpx.AsyncClient` driving the actual FastAPI
app (`app.main.app`) against an in-memory SQLite database (`StaticPool` so every
request's session sees the same data), with `get_current_user`/`get_db` overridden
rather than faking things below the route layer. 31 tests across
`test_lead_discovery_e2e.py`, `test_audit_engine_e2e.py`, `test_lead_scoring_e2e.py`,
and `test_outreach_approval_e2e.py`. No network calls anywhere — Places, PageSpeed,
SSL/contact-form checks, and SendGrid are faked or monkeypatched per-test; OpenAI
embeddings are monkeypatched once, globally, in `tests/e2e/conftest.py`, since this
sandbox has no `openai` package installed and several routes construct the real
embedding client inline rather than through an overridable dependency.

Full rationale, the edge-case-to-test mapping, and a logical (not measured — see below)
coverage breakdown per module live in `backend/tests/e2e/COVERAGE_SUMMARY.md`.

**Logging**: audited all four flows against AGENTS.md section 6. Found one gap —
`app/services/lead_scoring.py::score_lead` now logs a `WARNING` (previously only
`INFO`) when the compliance gate forces a lead to `do_not_contact`, since that override
is significant enough to want its own log level rather than blending into routine
scoring activity. Everything else in these four flows was already logging
info/warning/error appropriately; three new e2e tests assert directly on emitted log
records via `caplog` (not just HTTP responses) to keep that regression-tested.

**Not executed here**: same constraint as every prior phase — this sandbox has no
PyPI access (`pip install` returns a proxy 403), so FastAPI/SQLAlchemy/httpx/pytest are
not installed and the suite could not actually be run. Every file was statically
verified (`python -m py_compile`) and every route path, dependency name, and response
field was manually cross-checked against source. Run for real with:

```
cd backend
pytest tests/ --cov=app --cov-report=term-missing
```

## Security & compliance hardening: PII encryption, RBAC, cost guard, GDPR, CAN-SPAM audit

Requested: encrypt PII (email, phone) at rest; role-based access (admin/sales/viewer);
an API cost guard (daily budget cap + 80% alert on Places/LLM calls); a GDPR consent
flag + delete-request endpoint; and confirmation that the outreach module meets the
CAN-SPAM checklist.

Three things were confirmed with the user before starting, since each was either an
architectural fork or required a new dependency: adding `cryptography` for encryption
(approved), collapsing the existing 4-role model (admin/bd_manager/bd_rep/viewer) into
the requested 3-tier admin/sales/viewer (approved -- bd_manager+bd_rep merge into
`sales`, which can now approve *and* send; the old manager-only-approves distinction is
gone), and making the delete-request endpoint public/token-based like `/unsubscribe`
(approved).

**PII encryption**: `app/db/types.py::EncryptedString` (Fernet, via the new
`cryptography` dependency) encrypts `Lead.contact_email`/`contact_phone` and
`OutreachDraft.recipient_email`/`recipient_phone`. Fernet is non-deterministic, so it
can never back an equality lookup or unique constraint directly -- `Lead` gets a
companion `contact_email_hash` (deterministic HMAC-SHA256 blind index,
`app/services/pii.py`), which now backs `uq_leads_contact_email_hash` and every
lookup that used to run against the plaintext column (`Lead.set_contact_email()`
keeps the two in sync; the unsubscribe and bounce-webhook lookups in
`app/api/v1/outreach.py` now query the hash). Migration `0008_pii_gdpr` widens the
affected columns to `TEXT` and adds the hash column; it does NOT re-encrypt/re-hash
any pre-existing plaintext rows (documented in the migration's own docstring -- no
production data exists behind it). `ENCRYPTION_KEY` defaults to a fixed, publicly-known
dev-only placeholder so the app and tests work out of the box; `get_settings()` logs an
ERROR if that default is still in place under `APP_ENV=production`.

**Known gap, not fixed here**: `CallCenterCard.contact_email`/`contact_phone`
(`app/db/models/pipeline.py`) and `SuppressionEntry`/`BounceEvent.identifier`
(`app/db/models/outreach.py`) still store plaintext email/phone. The former is a
straightforward follow-up (same `EncryptedString` treatment, no lookups against it).
The latter is higher-risk to get right without a live DB to verify against: suppression
matching is exactly the code path where a hashing bug could silently let a
should-be-suppressed address through, so it wasn't done as a drive-by change in this
pass.

**RBAC**: `UserRole` is now `ADMIN` / `SALES` / `VIEWER` (`app/db/models/user.py`).
`APPROVER_ROLES` = `{ADMIN, SALES}` (send-gate access, unchanged endpoint, widened
membership). New `WRITE_ROLES` = `{ADMIN, SALES}` and `User.can_write()`. New
`app.api.deps.require_write_access` dependency, applied to every POST/PATCH/PUT
route across leads, places, audits, lead_scores, outreach (draft generation/edit/
reject -- approve/send stay on `require_approver`), pipeline, and services --
`VIEWER` now genuinely gets read-only access, which nothing enforced before this
change. New `require_admin` gates the cost-status route. `_coerce_role`'s
unrecognized/missing-claim fallback changed from `bd_rep` to `VIEWER` -- the actual
least-privileged role now that being a viewer means something. Migration
`0009_rbac_roles` swaps the Postgres enum type (`bd_manager`/`bd_rep` -> `sales`,
`admin`/`viewer` unchanged) via the standard create-new-type-and-swap approach, since
Postgres can't drop enum labels from a live type directly.

**API cost guard**: `app/services/cost_guard.py` (stdlib formula, mirrors
`send_limits.py`'s split) + `app/services/cost_tracking.py` (DB-aware ledger) +
`ApiCostEvent` model/migration (`0010_api_cost_events`). Guards Google Places (flat
per-request cost, checked *before* the call since it's known in advance) and OpenAI
chat completions in `outreach.py`, `auditor.py`, `recommender.py`, and
`reply_classifier.py` (cost computed from `response.usage` tokens *after* the call,
since token cost isn't knowable beforehand -- a new call is only blocked once prior
spend has already exhausted the day's budget). PageSpeed (free API) and OpenAI
embeddings (comparatively negligible per-call cost) are explicitly NOT guarded --
reasoning is in the module docstring. A WARNING logs the first time daily spend
crosses 80% of budget; an ERROR logs when a call is blocked or budget is fully spent.
Budget-exceeded on an LLM call degrades to that agent's existing deterministic
fallback (same code path as any other LLM failure); budget-exceeded on Places surfaces
as `429 Too Many Requests`. New admin-only `GET /api/v1/analytics/cost-status` reports
today's spend/budget/remaining per provider. Both budgets and the 80% threshold are
configurable via `COST_GUARD_DAILY_BUDGET_PLACES_USD` /
`COST_GUARD_DAILY_BUDGET_OPENAI_USD` / `COST_GUARD_ALERT_THRESHOLD`. The per-call
dollar estimates (Places flat rate, OpenAI per-1K-token rates) are hardcoded constants
with an explicit "verify against current pricing" caveat, not fetched from any live
pricing API.

**GDPR**: `Lead.gdpr_consent` (bool) + `gdpr_consent_recorded_at` + `gdpr_consent_source`
-- distinct from the pre-existing `consent_basis` free-text field, which records *which*
Article 6 lawful basis applies (consent is only one of several); `gdpr_consent` is
specifically "did they opt in." New public, token-verified `GET
/api/v1/privacy/delete-request` (`app/api/v1/privacy.py`) mirrors `/unsubscribe`
exactly: a tamper-proof HMAC token (`make_erasure_token`/`verify_erasure_token`,
`app/services/canspam.py`) bound to a specific lead+email is embedded in every
outreach email's footer (`finalize_email_body` now also builds an erasure URL
alongside the unsubscribe one), and visiting it scrubs `contact_name`, `contact_title`,
`contact_phone`, `website`, `linkedin_url`, `notes`, and `contact_email` from that lead,
flags `do_not_contact`, and stamps `pii_erased_at`. The row itself is retained (not
deleted) so foreign keys from audits/drafts/pipeline events don't dangle --
`pii_erased_at` is the durable proof the request was fulfilled. Migration
`0008_pii_gdpr` adds all four columns.

**CAN-SPAM checklist**: `backend/CAN_SPAM_CHECKLIST.md`. 5 of 7 FTC requirements are
fully enforced in code (header accuracy, non-deceptive subjects, physical address,
one-click permanent opt-out, prompt opt-out honoring -- immediately, not just within
the 10-business-day floor). One (#7, monitoring third-party senders) is enforced except
for a pre-existing, already-documented gap: the SendGrid bounce webhook doesn't yet
verify SendGrid's signature. One (#3, "identify as an advertisement") has no code
enforcement at all -- flagged as a product/legal-tone decision (where the disclosure
reads least like a hard sell) rather than fixed unilaterally.

**Files added**: `app/services/pii.py`, `app/services/cost_guard.py`,
`app/services/cost_tracking.py`, `app/db/models/cost.py`, `app/schemas/privacy.py`,
`app/api/v1/privacy.py`, migrations `0008_pii_gdpr`/`0009_rbac_roles`/
`0010_api_cost_events`, `CAN_SPAM_CHECKLIST.md`.

**Not executed here**: same sandbox constraint as every prior phase (no PyPI access,
so `cryptography`/FastAPI/SQLAlchemy aren't installed). Every touched file was
statically verified with `python -m py_compile`; the e2e suite from the previous
session was updated for the new 3-role model (`BD_MANAGER`/`BD_REP` references
replaced with `SALES`/`VIEWER`) but not re-run. Run for real with the same command as
before: `pytest tests/ --cov=app --cov-report=term-missing`.

## Phase E — Dockerize, CI/CD, cloud deploy, monitoring/alerts/backups (complete)

Requested: Dockerize the app; GitHub Actions CI/CD (build-test-deploy on push to
`main`); deploy backend+DB to a cost-effective cloud provider; uptime monitoring,
error alerts, daily DB backup.

One decision confirmed with the user before starting (new dependency): adding
`sentry-sdk[fastapi]` for error alerting (approved). The cloud provider choice was
explicitly delegated to me ("suggest cost-effective provider if none preferred") --
picked **Render**: builds this repo's own Dockerfile directly, its managed Postgres
includes pgvector and automated daily backups with point-in-time recovery on the
cheapest paid tier, and pricing is flat rather than usage-metered. Reasoning and
alternatives (Fly.io, Railway) are in `DEPLOYMENT.md`.

**Dockerize**: `backend/Dockerfile` -- two-stage build (`builder` compiles wheels
with build tooling that never reaches the final image; `runtime` is `python:3.11-slim`
plus only `libpq5`/`curl`, runs as a non-root `app` user, `HEALTHCHECK` against
`/health`). Serves via Gunicorn managing Uvicorn workers (auto-restarts a dead worker,
graceful shutdown on redeploy) rather than bare Uvicorn. `backend/.dockerignore`
excludes `.venv`, tests, and any `.env*` file. `backend/docker-compose.yml` is for
local development only (Postgres+pgvector, Redis, backend, running
`alembic upgrade head` before serving) -- Render does not use it; the blueprint below
builds the Dockerfile directly.

**CI/CD**: `.github/workflows/ci-cd.yml`. On every push/PR: install deps, `ruff check`,
`pytest --cov` (against in-memory SQLite -- no live DB, OpenAI, SendGrid, or Places
credentials touched, consistent with AGENTS.md's "never call live APIs in CI"). On
push to `main` specifically (after tests pass): build the Docker image, push to
`ghcr.io/<repo>` (GitHub Container Registry -- no extra registry account needed, auth
via the automatically-provided `GITHUB_TOKEN`), trigger a Render deploy hook, then
poll `/health/ready` until it passes or time out. Both the deploy-hook and
health-check steps degrade to a `::warning::` and a clean exit (not a failure) if
their secrets aren't set yet, so the pipeline is safe to merge before Render is
actually configured.

**Cloud deploy**: `render.yaml` (Blueprint spec, repo root) -- a `everen-backend`
Docker web service (`healthCheckPath: /health`, `autoDeploy: false` since deploys are
triggered explicitly by CI/CD after tests pass, not on every push), an `everen-redis`
instance (Celery broker), and an `everen-db` Postgres database on the `basic-256mb`
plan (cheapest tier that includes automated daily backups + point-in-time recovery).
Every secret-valued env var (`ENCRYPTION_KEY`, `OPENAI_API_KEY`,
`OUTREACH_PHYSICAL_ADDRESS`, auth JWKS settings, `SENTRY_DSN`, etc.) is declared
`sync: false`, meaning Render prompts for a real value in the dashboard rather than
this file ever holding one. `DEPLOYMENT.md` documents the full manual setup an agent
cannot do on the user's behalf: creating the Render/Sentry/UptimeRobot accounts,
connecting the repo, and filling in each `sync: false` value with where it comes from.

**Error alerts**: `app/main.py::_configure_sentry()`, called from the app's
`lifespan`. A true no-op when `SENTRY_DSN` is blank (the default) -- the import itself
is deferred inside the function so environments that never configure Sentry don't pay
an import cost or risk a broken install affecting startup. `send_default_pii=False` is
set explicitly, since lead contact info (already encrypted at rest per the prior
phase) should not leave the system via a third-party error-tracking payload either.
New settings: `sentry_dsn` (default `""`), `sentry_traces_sample_rate` (default `0.1`).

**Uptime monitoring**: new `GET /health/ready` alongside the pre-existing `/health`
(`app/main.py`) -- `/health` stays a pure liveness probe (used by the Docker
`HEALTHCHECK` and Render's `healthCheckPath`, deliberately not touching the DB so a
degraded database doesn't get a healthy process killed); `/health/ready` runs
`SELECT 1` against the database and is what an external monitor should point at.
UptimeRobot (free tier) is the documented primary monitor. `.github/workflows/
uptime-ping.yml` is a zero-account backstop: pings the same endpoint every 10 minutes
and opens/auto-closes a GitHub issue on failure/recovery -- explicitly documented as
a fallback, not the primary, since GitHub Actions cron isn't guaranteed to fire on
schedule and can only alert via an issue (no SMS/push).

**Daily DB backup**: two layers. Primary is Render's own built-in automated Postgres
backup (a one-time dashboard confirmation, no code, on the `basic-256mb` plan already
specified in `render.yaml`). Secondary/portable is `backend/scripts/backup_db.sh`
(`pg_dump --format=custom`, strips the `+asyncpg` driver suffix from `DATABASE_URL`
first since `pg_dump` doesn't understand it, optional S3-compatible upload via
`BACKUP_S3_BUCKET`) run daily at 09:00 UTC by `.github/workflows/db-backup.yml`,
which uploads the dump as a 35-day GitHub Actions artifact if no S3 bucket is
configured. The point of the second layer is portability -- it isn't tied to staying
on Render specifically.

**Files added**: `backend/Dockerfile`, `backend/.dockerignore`,
`backend/docker-compose.yml`, `backend/scripts/backup_db.sh`, `render.yaml`,
`.github/workflows/ci-cd.yml`, `.github/workflows/uptime-ping.yml`,
`.github/workflows/db-backup.yml`, `DEPLOYMENT.md`, `backend/.env.example` (did not
previously exist in this checkout -- recreated from `app/core/config.py`'s current
field set, including the encryption/RBAC/cost-guard/GDPR settings from the prior
phase plus this phase's `SENTRY_DSN`/`SENTRY_TRACES_SAMPLE_RATE`).

**Not done, and shouldn't be done silently**: this repo has no git history yet (no
`.git` directory existed in this checkout) -- `DEPLOYMENT.md` step 1 walks through
`git init`/commit/push, which only the user can do (needs their GitHub credentials).
Similarly, no agent can create the Render, Sentry, or UptimeRobot accounts, click
"Apply Blueprint," or paste in real API keys -- every one of those is a manual step
in `DEPLOYMENT.md` with an explanation of where each value comes from.

**Not executed here**: same sandbox constraint as every prior phase -- no PyPI access,
so `docker build` was not actually run against `backend/Dockerfile`, and the GitHub
Actions workflows were not run against a real GitHub Actions runner. Every YAML file
was manually reviewed for structural correctness (job dependencies, `if:` conditions,
secret references) and every Python change (`app/main.py`, `app/core/config.py`) was
verified with `python -m py_compile`.

## Soft-launch test-mode safety rails: SendGrid sandbox mode, Places request cap

Requested: switch SendGrid to Sandbox Mode for testing (no real sends/cost); add a
Places test-mode flag capping requests per run to stay inside the free monthly credit;
do not add any paid enrichment API yet; re-run the soft-launch batch at 150-300
simulated sends using sandbox mode.

**SendGrid Sandbox Mode**: new `sendgrid_sandbox_mode` setting (default `False`,
deliberately opt-in, never a silent default). `SendGridEmailSender.__init__` accepts
an explicit override but reads this setting by default; when on, `send()` adds
`mail_settings.sandbox_mode.enable=true` to the outgoing payload. SendGrid fully
validates and accepts the request under sandbox mode (so approval/suppression/quota/
CAN-SPAM checks all still run for real) but never delivers the message or affects
sender reputation. `X-Message-Id` is expected to be absent on a sandboxed response, so
`provider_message_id` staying `None` in that case is documented as expected, not a
parsing bug.

**Places test-mode request cap**: new `places_test_mode` (default `False`) and
`places_test_mode_max_requests` (default `10`) settings. A module-level counter in
`app/services/places.py` (module-level, not instance-level, since
`get_discovery_service` constructs a fresh `PlaceDiscoveryService` per request) is
checked at the top of `discover()`; once `places_test_mode_max_requests` requests have
been made this process, further calls raise the new `PlacesTestModeLimitExceeded`
(a `PlacesError` subclass), surfaced as `429` by `app/api/v1/places.py::search_places`
(checked before the broader `PlacesError`->`502` handler, since it subclasses it).
This is deliberately independent of the existing dollar-based cost guard
(`cost_guard_daily_budget_places_usd`) -- a hard request-count rail doesn't depend on
a per-call dollar estimate being exactly right, which matters more when the goal is
"stay trivially inside the free monthly credit," not "stay under a dollar figure."

**No paid enrichment API was added**, as instructed -- the email-coverage gap
identified in the prior soft-launch dry run is unresolved by this pass; it's tracked
as pre-scale fix #1 below and any fix to it needs a separate decision.

**Verification**: this sandbox still has no PyPI access, so the real
`app/services/email_sender.py` and `app/services/places.py` could not be imported
unmodified against real `httpx`/`sqlalchemy`. Rather than skip verification or
re-implement the logic in a parallel test copy, a harness injected minimal
pass-through fakes into `sys.modules` for `httpx`, `sqlalchemy`,
`sqlalchemy.ext.asyncio`, `app.core.config`, `app.db.base`, `app.db.models.place`, and
`app.db.models.cost` (each just enough to satisfy an import, no reimplemented business
logic), then imported and called the actual, unmodified `SendGridEmailSender.send()`
and `PlaceDiscoveryService.discover()`. Confirmed: the captured outgoing JSON payload
carries `mail_settings.sandbox_mode.enable=True`, and calling `discover()` in a loop
with `places_test_mode_max_requests=3` succeeds exactly 3 times before the 4th call
raises `PlacesTestModeLimitExceeded`. Harness deleted after the run, not committed.

**Re-run soft launch (v2)**: scaled to 280 raw candidates (up from the first dry run's
26) -- sized to exactly fit inside a modeled test-mode cap of 15 Places requests
(15 x 20 results/page = 300 max), comfortably inside Google's free monthly credit
(15 Text Search (New) requests ≈ $0.48 at published Essentials-tier pricing). Same
disclaimer as before applies: everything through "sent" runs the real business-logic
modules against synthetic candidate data; everything after "sent" (replies,
conversions) is an illustrative assumption injected to exercise the reply
classifier/pipeline code, not a measured result.

Funnel: 280 raw -> 244 unique (36 duplicates correctly filtered) -> 244 audited/scored
(6 hot, 214 warm, 24 cold, 0 do-not-contact) -> 155 eligible for a draft (89 dropped,
overwhelmingly for no email on file -- the same leak as the first dry run, now visible
at a scale large enough to trust the percentage: 89/244 = 36%, not the earlier run's
noisier 54%-on-24-leads) -> 152 approved (3 rejected on simulated manual review) -> all
152 sent with `sendgrid_sandbox_mode=True` -> only 50/day clear the daily quota, so the
full batch needs 4 days to send completely, not 1 -> 37 replies (24.3% reply rate) ->
0 reached pipeline Hot in this run. That last number is a modeling artifact worth
flagging explicitly, not a finding: the simulation advances each replied lead exactly
one pipeline step from New per the real `next_stage_towards()` logic (matching the
system's deliberate "don't skip straight to Hot on one reply" design -- see the Phase 6
section above), so a `book_call` reply lands at Contacted, not Hot, in a single-touch
simulation. Reaching Hot/Converted needs multiple simulated exchanges per lead, which
this pass didn't model; it should not be read as "0% conversion."

**Updated top pre-scale fixes** (email-coverage gap and bounce-webhook-signature gap
carried forward unchanged from the first dry run; the third is new, surfaced by
seeing the daily-quota math play out at a realistic batch size):
1. Email coverage is still the largest leak (see above) -- unresolved, as instructed
   (no enrichment API added).
2. SendGrid bounce webhook still has no signature verification -- unchanged, still a
   pre-scale item.
3. **New**: at `outreach_daily_send_limit=50`/day, a 150-300 lead batch takes 3-6 days
   to fully send, not one -- worth deciding now whether that's the intended cadence
   for a soft launch or whether the daily cap should flex upward for a bounded test
   window, since "150-300 simulated sends" reads as a single batch but the system's
   own quota will spread it across most of a week.

## Lead trigger-event signals: job postings, business status, review count jumps

Requested: detect trigger events for existing leads (new job posting on their website,
business hours/status change via Google Places, review count jump); store as
signal_type + detected_at + lead_id; surface high-signal leads at the top of the
outreach queue.

**Compliance fork, confirmed with the user before starting.** Two of the three signal
types (business status, review count) need Google Place Details -- a new paid Places
call -- and the raw values involved (`businessStatus`, `rating`, `userRatingCount`) are
already-forbidden Google Maps Content under `app/services/places_policy.py`
(`FORBIDDEN_FIELDS`), which this codebase has never persisted verbatim. Three options
were presented: (a) a keyed hash of a *bucketed* value, recoverable for direction/
magnitude by brute-forcing the small known domain, (b) a keyed hash of the *exact*
value with no direction/magnitude, (c) defer the two Places-derived signals entirely.
The user picked (a). This is the one genuinely new compliance judgment call in this
feature -- everything else follows existing patterns.

**The hash-only design** (`app/services/signal_detection.py`, stdlib + reuses
`app.services.pii.blind_index`): a raw review count is floored into a bucket of 10
(`REVIEW_COUNT_BUCKET_SIZE`); a raw business status is normalized to one of 4 known
enum values. Only a keyed HMAC-SHA256 of that bucket/status, namespaced by
`purpose=signal:{signal_type}` **and** by lead ID, is ever persisted
(`SignalCheckpoint.fingerprint_hash`) -- never the raw value, never even the bucket
integer in plaintext. To still answer "did it go up, by how much" from an opaque hash
without ever having stored the plaintext, `recover_review_bucket()`/
`recover_business_status()` brute-force the deliberately small candidate space (up to
500 review-count buckets covering 5,000 reviews; 4 status values) and report which
candidate's hash matches the stored checkpoint. **The recovered value is used only to
decide direction internally -- it is never written to `LeadSignal.detail`, never
logged, never returned by the API.** `LeadSignal.detail` text is deliberately generic
("Review volume increased by at least 10 reviews... the specific before/after counts
are not stored here") specifically so the persisted event log can't reconstruct the
Google Maps Content the hash was designed to avoid storing in the first place -- a
detail worth flagging because it would have been an easy, defeating mistake to make
one layer up from where the actual hashing happens.

**Job posting signal** (`app/services/job_signals.py`) is the one free,
Places-policy-unencumbered signal: reuses `app.services.web_parsing`'s link resolver
and the audit crawler's politeness conventions (robots.txt respected, identifying
User-Agent) to locate a lead's careers/jobs page (via home-page link matching, falling
back to common path guesses like `/careers`), extract its visible text, and fingerprint
it the same hash-only way (no Google policy reason here, just a bound on how much of a
prospect's page sits in memory). A changed fingerprint fires a `JOB_POSTING` signal,
explicitly caveated in `detail` as a content-change heuristic, not confirmation of a
specific new posting -- a keyword check (`JOB_POSTING_KEYWORDS`) adds "appears to be
actively advertising open roles" when applicable.

**Place Details wiring** (`app/services/places.py`): new
`GooglePlacesClient.get_place_details()` (a different, separately-billed endpoint from
the existing `search_text`), requesting only `businessStatus,rating,userRatingCount`.
New `PlaceDetailsResult` dataclass is deliberately missing a `persistable_fields()`
method (unlike `PlaceSearchResult`) -- there is nothing safe to call. New
`PLACE_DETAILS_COST_PER_CALL_USD` cost-guard constant, checked/recorded through the
same `cost_tracking.enforce_budget_before_call`/`record_spend` path and the same
`CostProvider.PLACES` daily-budget bucket as Text Search -- a signal scan that would
exceed the day's Places budget is skipped (not failed) for just the Places-derived
checks. The test-mode request cap added in the previous session's soft-launch work
(`places_test_mode`/`places_test_mode_max_requests`) was refactored out of
`PlaceDiscoveryService.discover()` into a standalone `enforce_places_test_mode_cap()`
so it's shared across both `search_text` and `get_place_details` -- the cap now bounds
total Places spend across the whole system, not just discovery searches.

**Orchestration** (`app/services/signal_scanner.py`): rep-triggered per lead, mirroring
the website audit agent's explicit "no bulk/automatic path" convention -- there is no
Celery beat wiring anywhere in this codebase yet (same pre-existing gap as the Places
retention sweeper), so nothing scans on a schedule. `scan_lead_for_signals()` checks the
job-posting page unconditionally (if the lead has a website) and the two Places-derived
signals only if the lead has a linked `PlaceCandidate` (found via `PlaceCandidate.lead_id`
-- leads not sourced from Places, or created manually, simply skip those two with a
reason surfaced in the response). Every failure mode (site unreachable, Places provider
error, budget exhausted, test-mode cap reached) degrades to a skip with a logged reason
rather than a failed scan -- a rep who ran the scan still gets the job-posting result even
if Places-derived checks couldn't run that time.

**Storage** (`app/db/models/signal.py`, migration `0011_lead_signals`): two tables,
deliberately split the way `OutreachDraft.status` (current state) is split from
`OutreachAuditLog` (append-only history) -- `LeadSignal` is the append-only event log a
rep reads (one new row per detected change, never updated in place, mirroring
`LeadScore`'s pattern); `SignalCheckpoint` is mutable, upserted-in-place current state
used only to detect the *next* change, one row per `(lead_id, signal_type)`.

**Surfacing at the top of the outreach queue** (`app/api/v1/leads.py`,
`app/services/signal_queue.py`): `GET /leads`'s default ordering now sorts by
`active_signal_count` (unacknowledged signals) first, then `confidence_score`, then
`created_at` -- a lead with a fresh, unactioned trigger event floats to the top
regardless of how it originally scored. Computed via three correlated subqueries in a
single query for the listing (not N+1), and via a small per-lead helper
(`attach_signal_summary`) for the four single-object routes that return a
`LeadResponse` (create/get/update leads, promote a Places candidate). New
`POST /leads/{id}/signals/{signal_id}/acknowledge` lets a rep clear a signal so it stops
pinning the lead once acted on. `LeadResponse` gained `active_signal_count`,
`latest_signal_type`, `latest_signal_at` -- none are mapped columns; they're computed
attributes set on the ORM instance before validation, which is why every route
constructing a `LeadResponse` had to be touched, not just the listing endpoint.

**New routes** (`app/api/v1/signals.py`, prefixed `/leads`):
`POST /{lead_id}/signals/scan` (rep-triggered, `require_write_access`),
`GET /{lead_id}/signals` (history, any authenticated read),
`POST /{lead_id}/signals/{signal_id}/acknowledge` (`require_write_access`).

**No paid enrichment API was added** for anything beyond what this feature explicitly
needed (Place Details for the two Places-derived signals) -- consistent with the prior
session's "do not add any paid enrichment API yet" instruction, which this feature
doesn't touch (it's not solving the contact-email-coverage gap flagged in the soft
launch reports).

**Tests**: `tests/test_signal_detection.py`, 22 tests, stdlib-only -- and, unlike most
of this codebase's tests in this sandbox, **actually executed**: a small harness
stubbed `app.core.config`/`pytest` into `sys.modules` (this sandbox has no PyPI access,
so neither is installed) and ran every test function directly. All 22 passed, including
the bucket-arithmetic edge cases (first observation, same-bucket, cross-bucket increase,
decrease-is-never-a-jump, a configurable minimum-increase threshold, and the fail-safe
behavior when a checkpoint's hash can't be recovered against the current lead's
namespace -- treated as an unquantified change rather than silently swallowed). The
DB/HTTP-touching code (`signal_scanner.py`, the new API routes, the migration) was not
executed, same constraint as every prior phase -- verified instead via a full-repository
`python -m py_compile` sweep (zero errors) and manual review of every query/route.

**Known gaps, not fixed here**: no scheduling -- a scan only runs when a rep explicitly
requests it, same as the audit agent and the still-unscheduled Places retention
sweeper. The job-posting detector is a content-change heuristic on a best-guess
careers-page URL, not a structured job-listing parser -- a copy-edit to an unrelated
part of the same page would also fire a signal. `PLACE_DETAILS_COST_PER_CALL_USD` is a
placeholder estimate (documented as such, same caveat as the existing
`PLACES_COST_PER_SEARCH_USD`) pending confirmation of which Places SKU tier
`businessStatus`/`rating`/`userRatingCount` actually falls under.

## Email enrichment: contact/footer crawl → pattern guess, unverified until confirmed

Requested: a fallback enrichment chain for leads missing an email -- try the website
contact/footer page first, then a common-pattern guess (`name@domain`) with
format-only validation (no paid verifier); mark each result with `confidence_score`
and `source`; never auto-send to unverified emails, flag for manual check instead.

**Design decision, not put to the user.** "Flag for manual check instead" of
auto-sending was read as *block draft generation entirely* for an unverified
address, not "allow a draft but block only at send/approval time." Reasoning: this
codebase already has an approval queue (`OutreachDraft.status="pending_review"`,
AGENTS.md §8) that exists precisely to catch bad drafts before they send --
routing an enrichment-sourced guess into that same queue would make it
indistinguishable from a normal, evidence-backed draft to the reviewer approving it.
Refusing to draft at all, with an explicit reason, is the same shape of decision this
codebase already makes for a suppressed address or a missing phone number
(`app/services/outreach_policy.py`), so this reuses that exact mechanism rather than
inventing a second gate. Worth flagging in case the intent was closer to
"draft anyway, just hold the send" -- easy to change if so.

**Two-step fallback chain, strict order (never merge-and-pick-best across steps)**:

1. **Website contact/footer page** (`app/services/email_discovery.py`, network-touching).
   Finds a likely contact page via a home-page link match (`contact`, `about`,
   `get-in-touch`, ...) or a common-path guess (`/contact`, `/contact-us`, ...);
   extracts a `mailto:` link first (`confidence_score=0.75`), falling back to an
   email-shaped text match (`0.55`) only if no `mailto:` is present. If no contact
   page is found or reachable, falls back to the home page's own footer/body the same
   way (`mailto:` `0.65`, text `0.45`) -- a contact page is always weighted above the
   home page, and a `mailto:` link always above a text-scraped guess, since a link is
   unambiguous and text-matching risks catching an unrelated address on the page.
2. **Common-pattern guess** (`app/services/email_enrichment.py`, pure stdlib) -- only
   attempted if step 1 found nothing. Given the lead's contact name and website
   domain, generates the standard permutations (`first.last@`, `firstlast@`, `flast@`,
   `first@`, `first_last@`, `last.first@`), capped at `MAX_PATTERN_GUESSES=6`, all
   sharing the same conservative `confidence_score=0.30` -- no permutation is more
   "correct" than another, so none is scored higher. Requires both a domain and a
   contact name with at least two usable parts (honorifics like "Dr."/"Mr." stripped
   first); with neither, returns nothing rather than falling back to generic role
   addresses (`info@`, `contact@`) -- those weren't asked for and aren't tied to a
   specific person, so they'd be an even weaker guess than what's already the weakest
   tier here. "Format-only validation" is honored literally: a regex syntax check
   (`is_valid_email_format`, matching the pattern already used by
   `app.services.canspam`), zero network calls, no paid verifier of any kind anywhere
   in this step.

**Module split mirrors the signals feature's precedent** (pure logic vs.
network-touching vs. DB orchestrator), for the same reason: `app/db/models/lead.py`
needs `EmailSource` for a column type, and a DB model should never have to pull in
`httpx` just to get an enum. This was caught and fixed mid-implementation -- the
first draft of `email_enrichment.py` mixed both, which would have made `lead.py`
transitively depend on an HTTP client. Split into `app/services/email_enrichment.py`
(pure: `EmailSource`, `EmailCandidate`, `is_valid_email_format`,
`extract_mailto_addresses`, `extract_text_addresses`, `guess_pattern_emails`) and
`app/services/email_discovery.py` (network: `find_contact_page_emails`, reusing
`app.services.job_signals`'s `USER_AGENT`/`extract_visible_text` and the same
politeness rules -- robots.txt respected, identifying User-Agent, one best-guess page
fetched rather than a full crawl).

**Orchestration** (`app/services/email_enrichment_scanner.py`): rep-triggered per
lead, same "no bulk/automatic path" convention as the audit and signal scanners --
nothing schedules this. `enrich_lead_email()` skips outright if the lead already has
a verified email (never overwrites a trusted address) or has no website on file (no
domain to crawl or guess against). Every candidate found -- from whichever step ran,
including the ones not picked -- is persisted as an
`EmailEnrichmentAttempt` (`was_applied` distinguishes the winner from the rest), so a
rep can see the full set of guesses, not just the one applied. The
highest-`confidence_score` candidate is written to `Lead.contact_email` via
`Lead.set_contact_email(..., verified=False)`.

**Storage** (`app/db/models/lead.py`, `app/db/models/email_enrichment.py`, migration
`0012_email_enrichment`): three new columns on `leads`
(`contact_email_source`, `contact_email_confidence`, `contact_email_verified`) plus a
new append-only `email_enrichment_attempts` table (mirrors `LeadSignal`'s
event-log-vs-current-state split from the signals feature --
`Lead.contact_email*` is the current state, `EmailEnrichmentAttempt` is the history).
`candidate_email` is stored via the existing `EncryptedString` PII-at-rest pattern,
consistent with how `Lead.contact_email` itself is already encrypted.

**Backward compatible by construction, not by special-casing.**
`Lead.contact_email_verified` defaults to `True` at both the DB column and the
`LeadOutreachContext` dataclass level, and `contact_email_source` defaults to
`EmailSource.MANUAL` -- so every pre-existing lead, and every existing call site that
calls `lead.set_contact_email(email)` with no new kwargs (`create_lead`,
`update_lead`, Places candidate promotion), keeps behaving exactly as before. Only an
email this chain itself found is ever written with `verified=False`. The one call
site that needed to opt into the new field, `build_lead_context()` in
`app/agents/outreach.py`, was the only one touched.

**The send gate** (`app/services/outreach_policy.py`): `assess_email()` gained one
new blocker, reusing the existing `ChannelDecision.blockers` mechanism used for a
suppressed or hard-bounced address -- checked after "no address at all" (so a lead
with genuinely no email still gets that more basic message, not a confusing one about
verification) and before the suppression/bounce checks. The blocker text names the
new `POST /leads/{id}/email/verify` endpoint directly so a reviewer isn't left
guessing how to unblock the lead.

**New routes** (`app/api/v1/email_enrichment.py`, prefixed `/leads`):
`POST /{lead_id}/email/enrich` (rep-triggered, `require_write_access`, runs the chain
and returns every candidate plus which one was applied), `GET
/{lead_id}/email/candidates` (any authenticated read, full attempt history, most
recent first), `POST /{lead_id}/email/verify` (`require_write_access`, marks the
current `contact_email` verified; 409 if the lead has no email on file to verify).
`LeadResponse` gained `contact_email_source`/`contact_email_confidence`/
`contact_email_verified` -- unlike the signals feature's computed summary fields,
these are real mapped `Lead` columns, so `model_validate()` picks them up with no
special attachment code needed.

**Tests**: `tests/test_email_enrichment.py` (26 test functions, some parametrized)
plus 4 new tests appended to the existing `tests/test_outreach_policy.py` for the new
blocker. Same constraint as every prior phase in this sandbox (no PyPI access, so
neither `pytest` nor any other dependency is installed) -- both
`app/services/email_enrichment.py` and `app/services/outreach_policy.py` are pure
stdlib with zero third-party imports, so rather than stubbing `pytest` into
`sys.modules`, the equivalent 36 individual assertions (parametrized cases expanded)
were run directly against the real, unmodified source via a throwaway runner script
(not part of the committed suite). All 36 passed, including the format-rejection
cases, mailto/text extraction edge cases (query-string stripping, case-insensitive
de-dup, malformed input), the honorific-stripping and single-word-name cases for
pattern guessing, and the new unverified-email blocker (including that a missing
address still takes precedence over an unverified one). The network-touching
`email_discovery.py`, the DB orchestrator, the migration, and the new API routes were
not executed -- verified instead via a full-repository `python -m py_compile` sweep
(zero errors) and manual review of every query/route, same as every prior phase.

**No paid enrichment API was added**, consistent with the standing instruction from
the SendGrid-sandbox-mode session -- `is_valid_email_format` is a regex syntax check
only, and neither chain step makes any call to a deliverability or verification
service. This feature also directly addresses the "email coverage gap" flagged as a
pre-scale fix in the original soft-launch report, though closing that gap was not
itself the trigger for this work.

**Known gaps, not fixed here**: no scheduling, same as every other scanner in this
codebase. The pattern-guess step only tries person-specific permutations, never
generic role addresses (`info@`, `sales@`) -- deliberately, since those weren't
requested and aren't evidence of anything, but it does mean a lead with no contact
name on file and no visible contact-page email gets zero candidates rather than a
weak-but-plausible `info@domain` guess. There is no re-enrichment trigger if a
website later gets a contact page added -- a rep has to re-run the scan by hand, same
manual-retrigger gap the signals feature already has for its own scans.

## Frontend, phase 1: scaffold + B2B Deal Flow dashboard (mock data)

Requested: add a specific dashboard design (screenshot supplied, also saved at
`Dashboard.png`) into the software without disturbing existing structure/menus, then
provide a localhost URL to review before the next phase's instructions.

**Starting state.** `frontend/` did not exist at all -- Phase 0 (Antigravity) only
scaffolded `backend/`. AGENTS.md section 3 already specifies the target layout
(`frontend/src/{components,pages,hooks,lib,types}`, `tsconfig.json`, `package.json`),
so there was no existing structure to disturb; this phase creates it for the first
time, following that spec exactly.

**Two scope questions put to the user before writing code** (per the standing
plan-first rule): (1) whether the nav/labels should match the screenshot literally
("Dashboard, deals, contacts, partners, analytics") or be renamed to this codebase's
real concepts ("Leads, Outreach, Signals, Analytics") -- **answered: match the
screenshot literally**; (2) whether this pass should render mock data or wire up
live API calls immediately -- **answered: mock data first**, live wiring is an
explicitly deferred next phase.

**Hard sandbox constraint, disclosed up front.** This sandbox has no npm registry
access (`npm view react` -> `403 Forbidden`, same proxy restriction already
documented for `pip` throughout this log), and no dependency is pre-cached --
confirmed via `npm install --offline`, which failed too. So none of `next`, `react`,
`typescript`, or `tailwindcss` could actually be installed or compiled here, and this
sandbox cannot serve a real `next dev` process at all. Two consequences, both handled
explicitly rather than glossed over: (a) the frontend source was hand-written and
verified by static analysis instead of a real TypeScript compile -- see Verification
below; (b) **"give me a localhost URL" isn't literally possible from this side** --
this sandbox is a separate machine from your PC with no port exposed to your
browser, and that would be true even with full npm access. Instead, this phase
rendered an in-chat interactive preview (matching the built page) so the design
could be reviewed immediately, and the steps below let you run the real thing on
your own PC at `http://localhost:3000`.

**What was built** (Next.js 14 + React 18 + TypeScript strict, Tailwind, per AGENTS.md
section 4.1 -- named exports throughout except the two files Next.js itself requires
a default export from, `_app.tsx` and page files under `src/pages/`, each commented
as the sanctioned exception):

- `src/types/dashboard.ts` -- shared shapes (`KpiMetric`, `FunnelColumn`, `DealCard`,
  `OutreachVolumePoint`, `ResponseRateSlice`, `PartnerLocation`, `FollowUpRow`,
  `WorkflowCanvasNode`/`Edge`, ...), explicitly documented as the future sync point
  with backend Pydantic schemas once this is wired to the real API.
- `src/lib/mockDashboardData.ts` -- the only place mock data lives; swapping this
  module for a real `fetch` against `NEXT_PUBLIC_API_BASE_URL` (already stubbed in
  `.env.local.example`, pointed at `http://localhost:8000/api/v1` to match the
  backend's existing CORS allowlist in `backend/app/core/config.py`) is the intended
  shape of the next phase.
- `src/components/layout/` -- `TopNav` (brand mark, the 5 literal nav links, mail/
  notification/avatar cluster), `AppShell` (shared page chrome), `ComingSoon` (used
  by the 4 not-yet-built nav destinations so every link is clickable, not a dead end).
- `src/components/dashboard/` -- `KpiCardRow`, `KanbanFunnel` (6-column deal board,
  read-only -- no drag/drop wired to a real pipeline mutation yet, see
  `backend/app/services/pipeline.py` for where that would attach),
  `PartnerOutreachPanel` (wraps `PartnerOutreachAnalytics`'s recharts bar+donut and
  `TopPartnerLocations`), `FollowUpTracker` (table with a locally-stateful reviewed
  checkbox), `WorkflowNodeBuilder` (static preview canvas of a
  trigger/condition/action graph, not draggable yet).
- `src/pages/index.tsx` -- assembles the dashboard; `deals.tsx`, `contacts.tsx`,
  `partners.tsx`, `analytics.tsx` -- `ComingSoon` placeholders so the nav is complete
  without pretending those pages are real yet.

**One deliberate design deviation from the screenshot**, flagged rather than silently
shipped: "Top Partner Locations" renders as a ranked bar list instead of a literal
shaded world map. A real choropleth needs a mapping library (e.g. `react-simple-maps`)
plus topojson geometry data -- a new dependency this phase intentionally didn't add,
consistent with the "mock data / design pass only" scope agreed above. Swapping in a
real map is a clean, isolated follow-up.

**Verification, given no compiler was available.** `python -m py_compile`'s
equivalent doesn't exist for TypeScript in this sandbox, so three static checks were
run by hand instead of a real `tsc`/`next build`: (1) brace/paren/bracket balance
across all 19 `.ts`/`.tsx` files -- clean; (2) every `@/...` import resolves to a real
file under `src/` -- clean; (3) every named import matches an actual named export in
its target file -- clean. A fourth pass caught a real bug this way: an editing pass
on `PartnerOutreachAnalytics.tsx` (splitting it out from its own panel wrapper) left
one extra stray `</div>`, found by a JSX open/close tag-count script (`<div>` 7 open
vs. 8 close) and fixed. None of this substitutes for actually running `tsc --noEmit`
-- flagged as the first thing to run once dependencies are installed for real.

**To run it for real, on your own PC** (this sandbox can't do it for you):
```
cd frontend
npm install
npm run dev
```
then open `http://localhost:3000`. `npm run typecheck` (added as a package.json
script) runs `tsc --noEmit` if you want the real compiler's verdict before anything
else.

**Known gaps, not fixed here**: no drag-and-drop on the kanban board or workflow
canvas (visual only); no live data (explicitly deferred); no auth/login screen yet
even though the backend already has JWKS auth wired up; "Top Partner Locations" is a
ranked list, not a literal map, as noted above; no automated test suite for the
frontend yet (no Vitest/@testing-library/react setup) -- AGENTS.md's working
conventions call for that alongside features, worth adding once this page is doing
more than rendering static mock data.

## Reply classifier extension: objection-response drafts, human-gated

Requested: when a reply is classified as an objection (price, timing,
not-interested-yet), generate a suggested response draft addressing the objection
using the service knowledge base, and add it to the human approval queue like any
other outreach draft -- no auto-send.

**Design fork, resolved without a round-trip, flagged here.** `ReplyIntent` (the
LLM-classified reply category) has five values and no "timing" category, and its
`NOT_INTERESTED` value already mixes two very different things: a genuine CAN-SPAM
opt-out ("unsubscribe", "stop contacting me") and a merely soft decline ("not
interested, thanks"). Two implementation choices followed from that:

1. **Objection type is a second, additive classification, not a new `ReplyIntent`
   value.** Adding a sixth top-level intent would change the LLM's category contract
   (`app/agents/reply_classifier.py`'s system prompt), the pipeline-stage mapping
   (`app/services/pipeline.py`'s `_INTENT_TARGET_STAGE`), and every other consumer of
   `ReplyIntent` -- none of which this feature has any reason to touch. Instead, a new
   `ObjectionType` enum (`PRICE` / `TIMING` / `NOT_INTERESTED_YET`) sub-classifies
   *within* the two existing intents that already mean "not a yes"
   (`PRICING` and `NOT_INTERESTED`) via a new pure function,
   `classify_objection(text, intent)` in `app/services/reply_classification.py`.
   `PRICING` always yields `PRICE`. `NOT_INTERESTED` yields `TIMING` if the text
   matches a new timing-phrase list ("not the right time", "check back", "circle
   back", ...), otherwise `NOT_INTERESTED_YET` -- *unless* it's a hard opt-out (below),
   in which case it yields nothing at all.

2. **A hard opt-out can never receive a generated objection draft, full stop.** A new
   `is_hard_opt_out()` checks the reply against the subset of `NOT_INTERESTED`'s
   existing keyword list that is explicit compliance language ("unsubscribe", "remove
   me", "stop contacting/emailing", "do not contact", "take me off", ...).
   `classify_objection()` returns `None` for any such reply, regardless of intent --
   there is no code path anywhere downstream that could generate a suggested response
   for one. This was a deliberate, conservative call made without asking: drafting a
   rebuttal to someone who explicitly asked to stop being contacted is wrong no matter
   how polite the copy is or that a human reviews it before it could ever send -- an
   ineligible draft sitting in the queue is itself the risk (same reasoning
   `app/services/outreach_policy.py`'s docstring already states for why ineligible
   channels are skipped rather than drafted-and-blocked-later). Explicitly *not*
   changed: this feature does not auto-suppress the lead or set `do_not_contact` on a
   hard-opt-out reply (today, only the unsubscribe-link click and the bounce webhook
   do that) -- extending automatic suppression to LLM/keyword-classified reply text
   carries real false-positive risk and was judged out of scope for "generate
   objection drafts"; it would be a reasonable, separate follow-up.
   `test_hard_opt_out_takes_precedence_over_timing_language` in
   `tests/test_reply_classification.py` locks in that opt-out language always wins
   even when a reply also contains a timing phrase.

**Generation** (`OutreachDraftAgent.generate_objection_response()`, new public method
on the existing agent in `app/agents/outreach.py`, alongside `generate_call_script`
which `app/agents/call_card.py` already reuses the same way): three new system
prompts, one per objection type, each grounded in the prospect's own reply text plus
the same audit findings and best-matching Everen Techno service (the "service
knowledge base") that cold-outreach drafts already use -- `PRICE` reinforces value
using only the service's stored summary/price range without inventing a discount;
`TIMING` respects the stated timeline and offers a no-pressure check-back rather than
pushing for a call; `NOT_INTERESTED_YET` is a single respectful acknowledgment that
leaves the door open without arguing the decision. All three explicitly forbid
inventing a problem, a price, or a discount, matching every other drafting prompt in
this codebase. A deterministic fallback (`_fallback_objection_response`) covers LLM
failure or the daily OpenAI budget being exhausted, same `_generate_with_llm` path
(and therefore the same cost-guard budget enforcement) as every other draft.

**Orchestration** (`app/services/objection_response_scanner.py`, new DB-aware
module, mirrors the established agent-vs-scanner split from the signals and
email-enrichment features): `maybe_generate_objection_draft(db, lead, message,
classification)` -- classify the objection; skip (with a logged reason, not an
exception) if there isn't one, if a draft was already generated for this exact
message (idempotent against re-classification), or if the lead currently fails
`assess_channel`. That last check is the important one: it reuses **the exact same
channel-eligibility gate** (`build_lead_context` + `assess_channel`) that normal,
rep-requested outreach drafting already goes through, so suppression, hard-bounce,
WhatsApp opt-in, the unverified-email block from the email-enrichment feature, and
`do_not_contact` all apply automatically with zero new bypass path. The reply's
channel maps directly to the draft's channel (email→email, whatsapp→whatsapp);
a phone-note reply (a rep's logged call summary) defaults to email, since that's the
one channel this system can actually send once approved. The persisted
`OutreachDraft` goes through `finalize_email_body` for the CAN-SPAM footer exactly
like the `POST /outreach/leads/{id}/drafts` route does, and is created with
`status=pending_review` -- there is no other status this module, or any module in
this codebase, is permitted to create a draft with.

**Storage** (`app/db/models/outreach.py`, migration `0013_objection_response`):
`OutreachDraft` gains two nullable columns -- `objection_type` (null for every
ordinary cold-outreach draft) and `triggering_message_id` (FK to
`inbound_messages`, mirroring `CallCenterCard.triggering_message_id`, and what the
scanner's idempotency check keys on). Both nullable with no backfill needed, since
nothing before this migration was objection-triggered.

**Audit logging deduplicated, not duplicated.** The `OutreachAuditLog` insert
(AGENTS.md section 8.5 -- every status transition must be logged) previously lived
only as a private `_log_transition` helper inside `app/api/v1/outreach.py`. Rather
than copy that compliance-critical code into the new scanner, it was extracted into
`app/services/outreach_audit.py`'s `log_draft_transition()`, and `outreach.py` now
aliases its old private name to the shared function so every existing call site is
unchanged. There is exactly one place in the codebase that writes an
`OutreachAuditLog` row, same as before, just reachable from two callers now. The
scanner logs its creation with `changed_by_id=None` (a system action, same
convention already used elsewhere for unattributed automated changes) and a note
naming which objection type triggered it.

**Wired in** (`app/api/v1/pipeline.py`): both `POST /leads/{id}/messages` (logging a
new reply) and `POST /leads/{id}/messages/{id}/classify` (re-classifying one) call
the new scanner right after pipeline advancement -- deliberately *not* nested inside
the "did the stage change" branch, since a `PRICING` reply from a lead already past
`Interested` produces no stage change but is still worth a response. Wrapped in
try/except, logged not raised, same defensive pattern already established for
call-center card generation (`_maybe_generate_call_card`) -- an optional suggested
draft failing to generate must never take down the message-logging request it rode
in on. `InboundMessageResponse` gained `objection_type` and `objection_draft_id`
(attached post-validation, same existing pattern as `stage_change` on this same
schema, since neither is a real mapped column on `InboundMessage`) so a caller
immediately knows a draft was queued without a new endpoint -- it already surfaces
in the standard `GET /outreach/queue`, exactly like a rep-requested draft.
`DraftResponse` gained `objection_type`/`triggering_message_id` for the same
visibility on the queue side.

**Tests**: 12 new test functions (several parametrized) appended to the existing
`tests/test_reply_classification.py`, covering `is_hard_opt_out` and
`classify_objection` -- both pure stdlib, zero third-party imports, so (same
constraint as every prior phase in this sandbox: no PyPI access, nothing installed)
the equivalent 27 individual assertions were run directly against the real,
unmodified source via a throwaway runner script, not part of the committed suite.
All 27 passed, including the load-bearing one: a hard opt-out combined with a timing
phrase in the same reply still yields no objection type. The LLM-calling,
DB-touching, and API-route pieces (`generate_objection_response`,
`objection_response_scanner.py`, the migration, the two wired-in routes) were not
executed -- verified instead via a full-repository `python -m py_compile` sweep
(zero errors) and manual review of every query/route, same as every prior phase.

**Known gaps, not fixed here**: no automatic suppression on a hard-opt-out reply
received outside the unsubscribe link or bounce webhook (see the design-fork note
above -- a rep who sees one should still set `do_not_contact` by hand today); no
scheduling (this is entirely event-triggered off a classify call, consistent with
every other scanner in this codebase); the `TIMING` phrase list is a fixed keyword
set with the same recall limitations as every other keyword fallback here, not an
LLM judgment call, so an unusual phrasing of a timing objection may fall through to
`NOT_INTERESTED_YET` instead -- a reasonable default outcome (still eligible for a
draft, just with the generic prompt) rather than a failure.

---

## Deliverability checklist: SPF/DKIM/DMARC checker, warmup tracker, readiness report

Rep-triggered, on-demand, read-only-with-respect-to-sending -- same category as the
audit engine and signal scanner: `POST /deliverability/checks` runs live DNS
queries and persists a result, `POST /warmup/plans` records a ramp configuration,
and everything else is a `GET`. Nothing in this module sends outreach or touches
`OutreachDraft`; it only changes *what the existing send gate is allowed to
permit* (see the warmup enforcement note below).

**Module split** follows the pattern established for reply classification and
CAN-SPAM: pure stdlib logic separated from network I/O separated from the
DB-aware orchestrator that ties them together and persists a result.
- `app/services/deliverability.py` (pure) -- SPF/DKIM/DMARC *parsing and
  validation* given already-fetched TXT/CNAME records. `CheckStatus`
  (PASS/WARN/MISSING/FAIL, in that severity order) and `combine_statuses()`
  (worst-wins) are shared by every section of the readiness report.
  `parse_spf_record()` treats more than one SPF record as FAIL, not a
  mergeable set -- RFC 7208 section 4.5 makes that a PermError, receiving
  servers reject it outright rather than picking one. Terminal-mechanism
  qualifiers are graded `-all` PASS, `~all`/`?all`/no-`all` WARN, `+all` FAIL
  (an open relay for spoofing). `parse_dmarc_record()` grades `p=reject` PASS,
  `p=quarantine`/`p=none` WARN (real policy but not the strictest, or
  monitoring-only respectively), and separately flags a missing `rua=`
  reporting address regardless of policy. `parse_dkim_selector()` accepts
  either a direct TXT key record or a CNAME delegation target (SendGrid's
  automated domain authentication delegates by default) as a PASS;
  `combine_dkim_results()` treats DKIM as satisfied if *any* guessed selector
  passes, since there's no way to auto-discover the real selector name.
- `app/services/dns_lookup.py` (network) -- DNS-over-HTTPS via Cloudflare's
  JSON API (`https://cloudflare-dns.com/dns-query`), reusing the existing
  `httpx` dependency rather than adding `dnspython`: this sandbox has no
  PyPI access to install anything new, and DoH avoids needing a UDP/53
  resolver library at all. `resolve_txt_records()` and `resolve_cname()`.
- `app/services/warmup.py` (pure) -- the ramp math. `WarmupPlan` (frozen
  dataclass: `start_date`, `start_volume`, `target_daily_volume`,
  `ramp_days`) validates itself in `__post_init__` (target must be >= start;
  a warmup plan only ramps up -- a deliberate volume *cut* is a different
  action, just lowering `OUTREACH_DAILY_SEND_LIMIT` directly).
  `planned_cap_for_day()` linearly interpolates from `start_volume` on day 0
  to `target_daily_volume` on day `ramp_days - 1`, flat afterward.
  `effective_daily_limit()` is `min(static_limit, planned_cap_for_day(...))`
  -- the ramp can only ever tighten the static limit, never raise it past
  what's already configured.
- `app/db/models/deliverability.py` / `app/db/models/warmup.py` -- persisted
  rows: `DeliverabilityCheck` (one row per run, full detail per section) and
  `WarmupSchedule` (deliberately *not* named `WarmupPlan`, to avoid colliding
  with the pure dataclass of that name -- `is_active` flag, single-current-row
  pattern mirroring `PromptVersion.is_active`). Migration
  `0014_deliverability`, `down_revision = 0013_objection_response`.
- `app/services/deliverability_checker.py` (DB orchestrator) --
  `run_deliverability_check()` resolves which domain to check
  (`DELIVERABILITY_CHECK_DOMAIN`, else the domain half of
  `OUTREACH_FROM_EMAIL`, else a `ValueError`), fetches TXT/CNAME records,
  hands them to the pure parsers, and persists one `DeliverabilityCheck` row.
  A DNS lookup failure is recorded as FAIL with an explanatory message, not
  silently treated as "record absent" -- those are different findings.
- `app/services/warmup_tracker.py` (DB orchestrator) --
  `resolve_effective_daily_limit()` is the actual enforcement point: it looks
  up the active `WarmupSchedule` for a channel (if any), converts it to a
  `WarmupPlan`, and returns the effective cap for right now.
  `build_warmup_status_report()` returns planned-vs-actual sends for today
  plus up to 30 days of history (bounded so an old, long-forgotten schedule
  can't force an unbounded scan), comparing the ramp's planned cap against
  the existing `DailySendCounter` rows already tracking real sends.
- `app/services/readiness_report.py` -- combines a fresh deliverability
  check, the email channel's warmup standing, whether CAN-SPAM sender
  identity settings are still at placeholder values (mirrors
  `app.services.canspam`'s own gate rather than introducing a second one),
  and whether SendGrid sandbox mode is on, into one `overall_status` via
  `combine_statuses()`. Computed live on every call, deliberately **not**
  persisted as its own table -- a stale readiness report right before an
  actual launch would be worse than no report at all. The
  `DeliverabilityCheck` it runs along the way is still persisted, so check
  history keeps accumulating even though the combined verdict doesn't.
- `app/api/v1/deliverability.py` -- `POST /deliverability/checks` (write
  access, 422 on an unresolvable domain), `GET /deliverability/checks` (list,
  newest first), `GET /deliverability/readiness`, `POST /warmup/plans`
  (approver-only -- same bar as approving an outreach draft, since this
  changes what real sending is allowed to do; 422 on an invalid ramp), `GET
  /warmup/status`. Registered in `app/api/v1/router.py`.

**Design fork, asked rather than assumed**: whether an active warmup schedule
should actually *gate* real sending, or just be advisory/informational. Asked via
`AskUserQuestion` since it directly changes send-gate behavior; the user chose to
enforce it. `app/api/v1/outreach.py` now computes `daily_limit` via
`resolve_effective_daily_limit(db, OutreachChannel.EMAIL,
settings.outreach_daily_send_limit)` in both `GET /outreach/quota` and the
pre-send/post-send quota checks inside `POST /outreach/drafts/{id}/send`, replacing
the previous direct use of `settings.outreach_daily_send_limit`. No schedule
configured means the static limit applies unchanged -- warmup is opt-in, not a new
default restriction on top of everything already shipped.

**Bug found and fixed during test-writing**: `planned_cap_for_day()` originally
checked `day_index <= 0` before checking `day_index >= ramp_days - 1`. For a
one-day ramp (`ramp_days=1`), day 0 is simultaneously "day 0" and "day
`ramp_days - 1`" -- the function's own docstring says day `ramp_days - 1` should
already be `target_daily_volume`, but the `day_index <= 0` branch fired first and
returned `start_volume` instead, silently capping a "ramp straight to target"
schedule at the start volume forever. Fixed by checking the "ramp already
complete" bound first; every other case (multi-day ramps, dates before the start
date) is unaffected -- confirmed by re-running the full assertion set after the
fix.

**Tests**: `tests/test_deliverability.py` (30 assertions -- `combine_statuses`
severity ordering, every SPF qualifier grade, DMARC policy tiers and the missing-
`rua` flag, DKIM direct-TXT vs. CNAME-delegation vs. any-selector-passes
combination) and `tests/test_warmup.py` (21 assertions -- plan validation,
day-0/last-day/before-start/well-past-ramp cap values, linear interpolation and
rounding, the single-day-ramp edge case above, `effective_daily_limit`'s
never-exceeds-static-limit guarantee, `within_cap` boundary at equality). Both are
plain stdlib `pytest`-style test files following this repo's existing convention
(`tests/test_reply_classification.py` etc.), but as with every prior phase in this
sandbox there is no PyPI access to install `pytest` itself, so all 51 assertions
were additionally executed directly against the real, unmodified source via a
minimal stdlib runner (no third-party dependency, no mocking) as a substitute for
`pytest test_deliverability.py test_warmup.py -v`; all 51 passed after the ramp-math
fix above. `app/services/deliverability_checker.py`, `warmup_tracker.py`,
`readiness_report.py`, the migration, and the API routes were not executed (they
need a real Postgres session and live DNS) -- verified instead via a full-repository
`python -m py_compile`/`compileall` sweep (zero errors) and manual review of every
query and route, same as every prior phase.

**Sandbox network restriction, disclosed**: a live end-to-end test of the DNS-
over-HTTPS lookup against `cloudflare-dns.com` was attempted (using the `httpx`
already present in `backend/.venv`) and got a `403 Forbidden` from this sandbox's
own egress allowlist proxy -- the same class of restriction already documented for
`pip`/`npm` in earlier phases, not a bug in the lookup code. The implementation is
believed correct by protocol and code review; it will work in the real deployed
environment, which has normal outbound HTTPS access.

**Known gaps, not fixed here**: DKIM selector discovery is a configured best-effort
guess list (`SENDGRID_DKIM_SELECTORS`, default `s1,s2`), not automatic -- there is
no DNS record that announces which selector an ESP is using, so a domain using a
non-default selector needs that selector added to the setting by hand, and the
report is transparent about exactly which selectors it tried. The sender-identity
placeholder check in the readiness report is a fixed list of four known-placeholder
settings values (mirroring what CAN-SPAM validation already blocks on), not a
general "is this config real" heuristic -- a new placeholder-style default added to
`config.py` later without updating `_PLACEHOLDER_VALUES` would silently pass this
check. `resolve_effective_daily_limit()` only ever *lowers* the effective limit,
never raises it above `OUTREACH_DAILY_SEND_LIMIT` -- a warmup schedule configured
with a `target_daily_volume` higher than the current static limit will complete
its ramp and then sit capped at the static limit, which is intentional (the
warmup config isn't a backdoor around the hard limit) but worth knowing if the two
were meant to move together.

---

## Campaign type: cold/warm/re-engagement segmentation, cadence, and tone

A new `campaign_type` field (`cold` / `warm` / `re_engagement`) on both `Lead` and
`OutreachDraft`, driving three things: draft tone, follow-up cadence, and a new
analytics breakdown. Nothing here changes the send gate -- every follow-up drafted
by this feature is still `pending_review`, exactly like a rep-requested draft.

**Where the enum lives.** `CampaignType` is defined in
`app/services/outreach_policy.py`, alongside `OutreachChannel` -- not in
`app/db/models/lead.py` where `LeadSource`/`LeadStatus` live, because unlike those
two, campaign_type is referenced by both `Lead` and `OutreachDraft`, plus the
outreach agent and analytics, the same reasoning that already placed
`OutreachChannel` there rather than locally.

**Lead.campaign_type** (`app/db/models/lead.py`): `nullable=False`, default
`COLD`, backed by a new `ix_leads_campaign_type_pipeline_stage` index (the
follow-up scanner's query shape). Every lead created before this migration
backfills to `cold` -- matching this codebase's actual behavior before this field
existed, where every draft was written as a first cold open.

**OutreachDraft.campaign_type + follow_up_sequence** (`app/db/models/outreach.py`):
`campaign_type` is a *snapshot* taken at draft-generation time, same rationale as
`recipient_email`'s snapshot -- a later change to the lead's campaign_type must
never retroactively change what an already-created draft is attributed to in
analytics. `follow_up_sequence` (int, default 0) marks a draft's position in its
lead's cadence: 0 = the initial send, 1 = the first cadence-triggered follow-up,
2 = the second, and so on -- set only by the new scanner below, never by a human.
Migration `0015_campaign_type`, `down_revision = 0014_deliverability`, backfills
both existing tables to `cold` / `0` via `server_default`.

**Cadence math** (`app/services/campaign_cadence.py`, pure stdlib): day-offset
schedules per campaign type, each offset measured from the *immediately preceding*
send, not the original send date --

| Campaign type | Offsets (days) | Rationale |
|---|---|---|
| `cold` | 3, 7, 14 | No relationship yet -- patient, spread-out touches, tapering off rather than escalating. |
| `warm` | 2, 5 | Referral/inbound/already-engaged -- shorter gaps read as attentive, not pushy. |
| `re_engagement` | 7, 21 | Already went quiet or was lost once -- repeating a fast cadence would repeat whatever didn't work the first time. |

`is_follow_up_due()`/`next_follow_up_due_at()`/`is_cadence_exhausted()` are the
three functions everything else calls; none of them ever touch a database.

**Tone** (`app/agents/outreach.py`): a `_CAMPAIGN_TONE_NOTES` dict (one short
paragraph per campaign type) is appended to whichever system prompt is already in
use -- the hardcoded default or an active `PromptVersion` override -- inside
`_generate_for_channel`, so campaign_type shapes tone without needing a separate
prompt per `(channel, campaign_type)` pairing. Cold gets a plain first-contact
framing; warm explicitly says to write with familiarity and acknowledge the
existing connection; re-engagement explicitly says to acknowledge the gap ("it's
been a while") rather than pretending this is the first contact.

**Follow-up generation is a separate method and prompt, deliberately.**
`OutreachDraftAgent.generate_follow_up()` uses its own `_FOLLOW_UP_SYSTEM_PROMPT`
(plus the same campaign tone note), not the first-contact prompt -- a follow-up
must read as a follow-up ("following up on my note last week"), get shorter and
lower-pressure with each successive touch, and add a new angle rather than
repeating the previous message verbatim. Raises `ValueError` for `CALL_SCRIPT`:
this system has no record of whether or when a call happened, so there is nothing
to cadence a follow-up off of.

**`app/services/campaign_followup_scanner.py`** (new DB orchestrator,
rep-triggered): mirrors `objection_response_scanner.py`'s shape and guarantees
exactly. Candidate selection deliberately reuses state that already exists rather
than tracking anything new -- `Lead.pipeline_stage == CONTACTED` is precisely "an
outreach draft was sent and the lead has not replied" (a reply moves the lead to
`INTERESTED`/`HOT`/`LOST` via existing pipeline transitions), so it is exactly the
population a follow-up cadence exists for. For each CONTACTED, non-suppressed
lead, on each of EMAIL/WHATSAPP (CALL_SCRIPT excluded, see above): finds the
lead's most recently *sent* draft on that channel, checks whether the cadence says
the next touch is due, skips if any draft already exists for that lead+channel
since the last send (idempotency -- no duplicate follow-ups piling up), re-runs
the full channel-eligibility gate (`assess_channel`, suppression, hard-bounce,
opt-in), then generates and persists a new `pending_review` `OutreachDraft` via
`generate_follow_up()`, with the same CAN-SPAM footer assembly and audit-log
transition as every other draft in this codebase.

**`POST /outreach/follow-ups/scan`** (`app/api/v1/outreach.py`, write-access):
runs the scanner on demand and returns every draft created plus every
lead+channel combination considered and skipped, with a reason. Rep-triggered
rather than scheduled -- as documented in every prior scanner in this codebase
(objection responses, email enrichment, lead signals), nothing here runs Celery
beat yet, so this follows the identical on-demand pattern rather than introducing
new background-job infrastructure this session.

**Lead schemas** (`app/schemas/lead.py`): `campaign_type` added to `LeadBase`
(so `LeadCreate` and `LeadResponse` both get it, default `COLD`) and to
`LeadUpdate` (optional). No route code changes were needed in
`app/api/v1/leads.py` -- both the create and update routes already build/patch the
model generically via `payload.model_dump()`/`setattr`, so the new field flows
through automatically.

**Analytics** (`app/services/analytics.py::get_campaign_performance`,
`GET /analytics/campaign-performance`): rolls up sent/opened/replied/meetings/won
per campaign type on sent emails, reusing `VariantPerformance`'s exact bucketing
shape from `get_prompt_version_performance` -- same query pattern (fetch sent
drafts in range, side-lookup opened/replied/hot/won lead-id sets, bucket in
Python), keyed on each draft's own snapshotted `campaign_type` instead of
`(prompt_version_id, ab_variant)`. `CampaignPerformanceListResponse` reuses
`VariantPerformanceResponse` per entry, with `variant_id`/`label` carrying the
campaign type's value (e.g. `"cold"`).

**Objection-response drafts also snapshot campaign_type.**
`objection_response_scanner.py`'s draft creation now sets `campaign_type=
lead.campaign_type` too, so a reply to a re-engagement touch still attributes to
the re-engagement bucket in analytics. `follow_up_sequence` is left at its
default (0) there -- an objection-response draft is reply-triggered, not a
cadence step, so this feature's numbering doesn't apply to it.

**Tests**: `tests/test_campaign_cadence.py` (20 assertions -- max_follow_ups and
exhaustion per campaign type, next-due-at offsets including the "offsets are
relative to the preceding send, not the original" behavior, due/not-due
boundaries, and a full three-touch cold cadence walked end to end). Pure stdlib,
no third-party imports, so (same constraint as every prior phase in this sandbox:
no PyPI access, nothing installed) all 20 were additionally executed directly
against the real, unmodified source via the same minimal stdlib runner used for
the deliverability phase; all passed. `campaign_followup_scanner.py`, the
migration, the agent's LLM-calling methods, and the new API/analytics routes were
not executed -- verified instead via a full-repository `python -m py_compile`/
`compileall` sweep (zero errors) and manual review of every query and route, same
as every prior phase.

**Known gaps, not fixed here**: the cadence day-offsets (3/7/14, 2/5, 7/21) are a
reasonable default, not a business-validated number -- there is no existing
guidance on cadence timing anywhere in `AGENTS.md`/`CLAUDE.md`, so this was a
judgment call rather than a documented requirement; adjust
`CADENCE_SCHEDULES` in `campaign_cadence.py` directly if the business wants
different timing. Follow-up scanning only ever looks at a lead's *most recently
sent* draft to determine cadence position -- a lead manually moved backward in
status, or one whose only sent draft was later deleted at the DB level (never
happens through this API, which never deletes a sent draft), would read as having
no cadence history. WhatsApp follow-ups are subject to the same Meta template-
approval requirement as any other WhatsApp draft (`assess_channel` still gates
it) -- this feature does not add a second WhatsApp-specific follow-up template
concept. As with every other scanner in this codebase, there is no scheduling
infrastructure yet, so `POST /outreach/follow-ups/scan` must be called by a rep
(or wired to a cron/Celery beat once that infrastructure exists) rather than
running itself.

---

## Frontend: Lead Workflow spreadsheet view (mock data)

A new `/workflow` page: one row per lead, one column per pipeline step
(discovered -> enriched -> audited -> scored -> drafted -> approved -> sent ->
replied), matching the design/layout-pass pattern every other frontend page in
this repo has followed so far (confirmed with the user before building --
renders mock data only, no real API client exists in this frontend yet).

**Two design forks asked rather than assumed**, via `AskUserQuestion`, since both
directly affect scope/architecture and (per CLAUDE.md's working conventions) this
codebase asks rather than assumes around data access and outreach sending:

1. **Mock vs. live-wired.** Chose mock data, matching every prior frontend
   phase -- the real API client (auth, fetch wrapper, error handling) doesn't
   exist in this frontend yet and building it was out of scope for a first
   layout pass.
2. **What "approved"/"sent" cells do on click.** These two steps are gated by
   AGENTS.md section 8's non-negotiable human-approval rule. Chose "open the
   draft for review" rather than "fire the action directly" -- a spreadsheet
   cell must never be able to one-click approve or send, even in a future
   live-wired version; it can only ever open the real review surface.

**Types** (`frontend/src/types/workflow.ts`): `PipelineStepKey` (the 8 fixed
columns -- no drag-drop column builder yet, per this request's scope),
`StepStatus` (done/in_progress/pending/failed/not_started), `StepInteraction`
-- the key modeling decision: each column is tagged "rerun" (enriched, audited,
scored, drafted -- real single-lead endpoints already exist for all four:
`POST /leads/{id}/email/enrich`, `POST /audits`, `POST /leads/{id}/score`,
`POST /outreach/leads/{id}/drafts`), "review" (approved, sent -- per the fork
above), or "detail" (discovered, replied -- an origin event and an inbound
reply respectively have nothing to "re-run").

**Mock data** (`frontend/src/lib/mockWorkflowData.ts`): the 8-column
`pipelineSteps` metadata array, plus 10 `WorkflowLeadRow` entries deliberately
covering every status and stopping point -- a lead all the way through to a
reply, one delivered but not yet replied, one approved but quota-delayed, one
awaiting approval, one with no draft yet, a failed score computation, a failed
enrichment, one mid-enrichment ("in_progress"), a failed draft (CAN-SPAM sender
identity placeholder, mirroring a real failure mode from
`app/services/canspam.py`), and one brand-new lead with nothing run yet.

**Components** (`frontend/src/components/workflow/`): `WorkflowGrid.tsx` --
the spreadsheet table (sticky header row and sticky first column so the lead
name stays visible while scrolling 8 columns), color-coded status chips per
cell, and the per-cell click dispatch described above. A "rerun" click is
simulated client-side only: the cell flips to "Running" (spinning icon) then
back to "Done" ~900ms later via `window.setTimeout`, with proper cleanup on
unmount (a `Set` of pending timeout ids cleared in a `useEffect` teardown, so
the mock never sets state after the component unmounts) -- wiring this to an
actual `await fetch(...)` against the real endpoints listed above is the
follow-up phase's job, and the code comments say so at the exact line that
will change. `WorkflowCellPanel.tsx` -- the read-only panel opened by
"review"/"detail" clicks; for "review" it explicitly states it cannot approve
or send on its own, rather than silently doing nothing (a blank-feeling
dead-end button would be worse than an explained one).

**Page + nav** (`frontend/src/pages/workflow.tsx`,
`frontend/src/components/layout/TopNav.tsx`): new `/workflow` route added to
`NAV_ITEMS` between "partners" and "analytics".

**Verification**: `next/core-web-vitals` ESLint passed clean (zero errors/
warnings) on all 6 new/modified files. A full `tsc --noEmit` project-wide type
check could not be completed in this sandbox -- three attempts (via `npx`, the
local `.bin/tsc` directly, and a retry) all exceeded this environment's 45-second
per-command limit before producing any output at all, apparently due to slow
file I/O reading `node_modules`/type declarations across the Windows-mounted
project folder from the Linux sandbox (the same class of cross-platform mount
overhead noted in earlier phases, not a defect in the new code). Every new type
pattern used here -- `Record<K, V>` status-keyed lookups, optional `PanelHeader`
props, named-export components returning `JSX.Element`, the page's sanctioned
default export -- mirrors an existing file in this codebase that already
type-checks under the same `strict`/`noUncheckedIndexedAccess` config
(`FollowUpTracker.tsx`'s `STATUS_STYLES` lookup is the direct precedent for
this file's status-keyed records), so this is believed type-correct by pattern-
matching and manual review, but a real `tsc --noEmit` run (e.g. from a normal
terminal on the user's machine, or `npm run typecheck`) is the way to fully
confirm it.

**Known gaps, not fixed here**: nothing on this page calls the real backend --
every status, timestamp, and click outcome is mock data or a client-side
simulation, exactly as scoped. The grid does not yet support sorting, filtering,
or pagination (10 mock rows fit on screen; a real lead list would need at least
pagination, the same `page`/`page_size` convention already used by
`GET /leads`). "Review" clicks show a static read-only panel rather than an
actual link into the outreach queue, since that queue page/route doesn't exist
in this frontend yet either.

---

## Frontend: dashboard chat panel (first real API wiring)

A chat panel on the main dashboard: plain-text requests like "find restaurants
in Dallas with no website" or "show me leads scored above 80" get parsed into
an intent, mapped onto the two existing endpoints that already do this work
(`GET /leads`, `POST /places/search`), and rendered in a shared results table.
No new backend logic anywhere -- every field this feature reads already exists
on an existing response schema.

**This is the first real API call this frontend has ever made.** Every prior
phase (dashboard widgets, the workflow grid) rendered mock data only, each
confirmed with the user before building. This request was different in kind --
"wires natural language to existing endpoints" only means something if the
endpoints are real -- so two forks were asked rather than assumed (via
`AskUserQuestion`) before writing any code:

1. **Auth, given there's no sign-in flow.** Every backend route requires a
   verified JWT (Clerk/Auth.js JWKS, `app/api/deps.py::get_current_user`), and
   building a real sign-in flow was out of scope for "just wire NL to existing
   endpoints." Chose: build the real fetch client and real endpoint calls,
   reading a bearer token from a `NEXT_PUBLIC_DEV_API_TOKEN` env var set
   locally; fall back to realistic mock results (clearly labeled) when no
   token is configured or a call fails/401s -- genuinely wired, not fake, but
   never hard-blocked on auth existing yet.
2. **The score-filter gap.** `GET /leads` filters by status/category/
   min_confidence only -- there's no bulk lead-score endpoint (score lookups
   are per-lead: `GET /leads/{id}/score`), so "leads scored above 80" cannot
   map onto one existing call. Chose: approximate client-side -- fetch a page
   of leads, look up each one's score, filter locally -- over silently
   guessing or refusing the request outright, with the response text saying
   explicitly that this checked a page, not the whole database.

**Parser** (`frontend/src/lib/parseChatQuery.ts`, pure, no LLM call): a
deliberately non-clever rule-based/regex parser, not full NLP -- recognizes a
fixed set of phrasings well and says so plainly when it doesn't recognize
something (`{ kind: "unrecognized", message: "..." }`), rather than guessing
at intent it isn't confident about. The presence of the word "lead(s)"
disambiguates a `leads_list` intent from a `places_search` one (the two never
need the same parsing). For places, `POST /places/search` requires a
`postal_code`, not a free-text city (`PlaceSearchRequest` has no city field)
-- so a small `CITY_ZIP_LOOKUP` (~20 major US cities, one representative ZIP
each, documented as approximate, not a geocoder) resolves "Dallas" to `75201`;
an unrecognized city gets an honest "I don't have a ZIP code for that, try
including one" rather than a wrong guess. "No website" is applied client-side
to real results after the fact (`PlaceSearchResultResponse.website` is already
in the response) rather than needing a new backend filter param. Every
`RegExpMatchArray` index access is routed through a small `group()` helper
that treats a missing/empty capture as absent -- required by this project's
`noUncheckedIndexedAccess` tsconfig flag, which types every array index
(including regex match groups) as possibly `undefined` regardless of whether
the outer match already succeeded.

**Real queries + mock fallback** (`frontend/src/lib/apiClient.ts`,
`chatQueries.ts`, `mockChatResults.ts`): `apiClient.ts` is a small generic
`apiFetch<T>()` wrapper (bearer auth header when `NEXT_PUBLIC_DEV_API_TOKEN`
is set, a typed `ApiError` with an HTTP status or `null` for a network
failure) and `hasApiToken()`, which callers check first so an unconfigured
frontend skips straight to mock data instead of firing a request that can
only ever 401. `chatQueries.ts` runs the real calls and converts each response
into the same normalized row shape (`LeadResultRow`/`PlaceResultRow`,
`frontend/src/types/chat.ts`) the mock fallback also produces, so the table
component never needs to know which source a result came from. A score-
filtered leads query fetches `LEADS_PAGE_SIZE` (50) leads, then calls
`GET /leads/{id}/score` for each in parallel via `Promise.all` -- a 404 there
means "not yet scored," handled as "excluded from a score-filtered result,"
not an error. `mockChatResults.ts` filters its canned sample rows by the same
parsed intent fields the real query would (status, confidence, score
threshold, no-website), so a fallback response still reads as an answer to
what was actually typed.

**UI** (`frontend/src/components/dashboard/ChatPanel.tsx`,
`ChatResultsTable.tsx`, wired into `src/pages/index.tsx`): a message-history
chat panel with three clickable example prompts shown until the first message.
Every assistant message that used the mock fallback is labeled "Showing sample
results" directly under the response text -- a sample result must never look
identical to a real one. `ChatResultsTable` is one shared component for both
result kinds ("return results in the same table view," per this request),
switching only its column set on `results.kind`.

**Verification**: `next/core-web-vitals` ESLint passed clean (zero errors/
warnings) across every new/modified file, run both individually and as a
full `src/**/*.{ts,tsx}` sweep. As with the workflow-grid phase, a full
`tsc --noEmit` could not be completed in this sandbox -- repeated attempts
(via `npx`, the local `.bin/tsc` directly, with and without a `timeout`
wrapper) all exceeded or were consistent with exceeding this environment's
45-second per-command limit before producing output, the same cross-platform
mount I/O overhead noted in the prior phase, not a defect in the new code.
Every non-trivial type pattern here (discriminated unions on `results.kind`,
`Extract<ParsedIntent, {...}>` narrowing, the `noUncheckedIndexedAccess`-safe
`group()` helper, a custom `Error` subclass) was manually re-checked against
the compiler's actual narrowing rules rather than assumed; a real
`npm run typecheck` run is still the way to fully confirm it.

**Known gaps, not fixed here**: the parser recognizes a fixed set of
phrasings -- unusual wordings fall through to "unrecognized" with a hint
rather than being creatively interpreted, which is intentional (no LLM call,
per this request's "no new backend logic" constraint) but means this is not
free-form natural language understanding. `CITY_ZIP_LOOKUP` covers about 20
major US cities; anything else needs a ZIP typed directly. The score-filter
approximation only ever looks at the first `LEADS_PAGE_SIZE` (50) leads
matching the query's other filters, not the whole table -- a real "leads
scored above N" feature would need a new backend query param joining
`LeadScore`, which is explicitly out of scope here. There is still no sign-in
flow in this frontend; `NEXT_PUBLIC_DEV_API_TOKEN` is a local-development
convenience, not a real auth integration, and is documented as such in
`.env.local.example`.

---

## Calendar booking module (Google Calendar, one shared sales calendar)

When a lead replies "interested" or "book a call," a booking-link reply draft
is auto-generated pointing at a public link showing the shared sales
calendar's real free slots. Confirming a slot books it on that calendar,
records a `Meeting`, and advances the lead's pipeline stage to a new
`meeting_booked` stage.

**Two design forks were asked before writing any code** (via
`AskUserQuestion`), since both were genuine product/architecture decisions
CLAUDE.md's "ask when requirements are ambiguous" explicitly calls out:

1. **Calendar model.** Chose: one shared sales calendar -- a single Google
   account's OAuth refresh token, obtained once by an admin outside this
   application (e.g. via Google's OAuth Playground), configured in
   `GOOGLE_CALENDAR_*` settings. Not a per-rep "connect your calendar" flow --
   there is no rep-identity model in this schema to hang that off of, and it
   would have meant building an OAuth connect UI, which is a materially
   bigger feature than "generate a booking link."
2. **Pipeline stage handling.** Chose: add a new `PipelineStage.MEETING_BOOKED`
   and redefine the existing `meetings_booked` analytics metric to mean
   "reached `MEETING_BOOKED`" rather than its previous proxy definition
   ("entered Hot," `to_stage=hot`/`from_stage != hot`). The old definition
   counted every Hot entry regardless of whether a meeting was ever actually
   booked; the new one only counts a real, confirmed calendar booking. See
   `app/services/analytics.py`'s module docstring and `get_overview`'s
   docstring for the exact before/after.

**Is booking a meeting "outreach" subject to AGENTS.md section 8's human-
approval gate?** No, and this distinction is made explicitly in code comments
(`app/api/v1/booking.py`'s module docstring, `google_calendar.py`'s
`create_event` docstring): the gate governs agent-*generated* content -- an
LLM-drafted email or WhatsApp message that must be reviewed before it goes
out. Confirming a calendar slot is a transactional action the prospect takes
themselves, on a public, token-scoped endpoint they reached themselves, after
already clicking a link in a message a human *did* approve. The only thing
generated here is a calendar event whose time, attendee, and existence the
prospect just chose -- there is no LLM-generated marketing content anywhere
in the confirm path. The booking-link *reply draft* that points at this link,
by contrast, is fully subject to the gate: it's created with
`status=pending_review` exactly like any other outreach content and only
reaches the prospect after a human approves and sends it.

**No new dependencies.** Google Calendar access is raw `httpx` REST calls
(OAuth token refresh via `POST https://oauth2.googleapis.com/token`, then the
Calendar v3 `freeBusy` and `events` endpoints) rather than
`google-api-python-client`/`google-auth`, mirroring the DNS-over-HTTPS
precedent from the deliverability phase (`app/services/dns_lookup.py`): both
are a plain HTTPS JSON API, so a full client SDK buys nothing here that two
typed wrapper methods don't, and it avoids new-dependency approval friction
per CLAUDE.md's "ask before adding dependencies."

**Pure modules** (`app/services/booking_slots.py`, `booking_token.py`):
standard library only, so slot math and token verification are unit-tested
without a database or network call.
- `booking_slots.py`: `compute_available_slots()` walks each weekday in the
  lookahead window, in `booking_timezone`, generating fixed-length slots
  inside working hours, dropping any that start before the minimum lead time
  or overlap a supplied `BusyInterval` (compared on absolute time, so a busy
  interval in any timezone is handled correctly without the caller
  converting it first).
- `booking_token.py`: unlike the unsubscribe/erasure tokens in
  `canspam.py` (which never expire, because an opt-out must stay honoured
  indefinitely), a booking link is a standing, unauthenticated *write*
  capability against the shared calendar, so every token carries and signs
  its own expiry (`lead_id:message_id:expires_at` base64url-encoded,
  HMAC-SHA256 signed, `"<payload>.<signature>"` format) -- verification
  never needs a database lookup, and an attacker can't strip the expiry
  since it's inside the signed payload.

**Network client** (`app/services/google_calendar.py`): `GoogleCalendarClient`
fetches an access token fresh on every call rather than caching it -- a
documented latency/simplicity tradeoff, not an oversight, since booking-flow
calls are low volume. `GoogleCalendarNotConfiguredError` (a distinct subclass
of `GoogleCalendarError`) lets callers show "booking isn't set up yet" rather
than a generic 500 when the `REPLACE_ME` placeholder settings haven't been
replaced. A standalone `is_configured()` function lets
`booking_link_scanner.py` check this cheaply before generating a link at all.

**Pipeline stage machine** (`app/services/pipeline.py`): `MEETING_BOOKED` is
reachable directly from `CONTACTED`, `INTERESTED`, and `HOT` (not from `NEW`
-- booking is always reply-triggered, and a lead can't reply before being
contacted). It's reachable from every one of those, not gated behind first
passing through `HOT`, because a real calendar booking is certain ground
truth from an external event, unlike every other transition in this graph,
which is a probabilistic inference from a classified reply -- forcing it
through `HOT` first, or through `next_stage_towards`'s BFS multi-hop stepping
(designed for exactly that probabilistic case), would risk a stage-graph
technicality contradicting a fact that has already happened.
`advance_on_meeting_booked()` (`pipeline_transitions.py`) defensively
swallows `InvalidTransitionError` (logged, not raised) for the edge case of
a lead reaching `CONVERTED`/`LOST` between link generation and confirmation --
there is nothing a caller could usefully do to "fix" that after the meeting
already exists on the calendar.

**Draft generation** (`app/agents/outreach.py::generate_booking_reply()`):
mirrors `generate_objection_response()`'s shape. The one part that must be
correct byte-for-byte -- the actual booking URL -- is never trusted to the
LLM, exactly like `finalize_email_body()`'s CAN-SPAM footer: the system
prompt explicitly instructs the model not to write a link at all, and as a
defensive backstop, any `http(s)://` URL the model writes anyway is stripped
from the output before it's used (logged as a warning when this fires). The
real URL is built deterministically and appended by
`booking_link_scanner.py` after generation.

**Scanner + dedup fix** (`app/services/booking_link_scanner.py`,
`objection_response_scanner.py`): the new scanner reacts to
`BOOK_CALL`/`INTERESTED`-classified replies, same event-triggered pattern as
the objection scanner (triggered from `log_message`/`classify_message` in
`app/api/v1/pipeline.py`, never on a schedule). Because both scanners key
their existing-draft dedup check off the same `OutreachDraft
.triggering_message_id` column, a reply that could plausibly trigger both
(in principle -- objection classification and reply-intent classification
are separate classifiers over the same message) would make each scanner
mistake the other's draft for its own prior run and wrongly skip. Fixed by
additionally filtering both scanners' dedup queries on
`created_by_agent == "<this-scanner>+<outreach-agent>"`, so each only ever
sees its own drafts.

**Data model** (`app/db/models/meeting.py`, migration `0016_calendar_booking`):
`Meeting` (lead_id, triggering_message_id, scheduled_start/end, encrypted
`attendee_email` -- same `EncryptedString` pattern as `Lead.contact_email`,
calendar_event_id/link, status, booked_at). The migration extends
`pipeline_stage` and `pipeline_transition_reason` with `meeting_booked` via
`ALTER TYPE ... ADD VALUE IF NOT EXISTS` -- the same pattern already used in
`0002_places_discovery` to add `lead_source.google_places`, no
`autocommit_block` needed. Downgrade intentionally leaves those enum values
in place (Postgres cannot remove an enum label), matching `0002`'s precedent.

**API routes** (`app/api/v1/booking.py`): `GET /booking/{token}/slots` and
`POST /booking/{token}/confirm` are public and unauthenticated (reached from
the link in an approved, sent message) -- an invalid, expired, or
lead-not-found token all return the same generic 400 so neither confirms nor
denies a specific lead id to an anonymous caller. `confirm` re-derives
available slots from a fresh `freeBusy` check and requires the requested slot
to be an exact match, which is both the race defense (two prospects hitting
the same slot) and the tamper defense (a crafted request naming a time
outside working hours, past the lead-time cutoff, or on a weekend -- none of
which `compute_available_slots` would ever have offered). A third,
authenticated route, `GET /leads/{lead_id}/meetings`, lists a lead's booked
meetings for staff.

**Verification**: `python -m py_compile` on every new/modified file
individually, plus a full `python -m compileall app alembic tests` sweep
(clean, zero errors). `booking_token.py` and `booking_slots.py` were smoke-
tested by hand (token round-trip, tamper/wrong-secret/expiry/malformed
rejection; slot math's weekday-only filtering, lead-time cutoff, exact and
partial busy-interval overlap blocking, cross-timezone busy-interval
comparison) before being written up as 13 and 18 formal `pytest`-style tests
respectively in `tests/test_booking_token.py` / `tests/test_booking_slots.py`
-- both pass, run in this sandbox via a throwaway stdlib shim (no PyPI
access here; the project's real `.venv` has `pytest` installed and these run
under it unmodified). The pre-existing `tests/test_pipeline.py` suite (23
tests) was re-run against the modified `pipeline.py` and still passes in
full, confirming the `MEETING_BOOKED` additions didn't disturb the existing
transition graph. `google_calendar.py`, `pipeline_transitions.py`,
`booking_link_scanner.py`, and `app/api/v1/booking.py` are DB/network-aware
and depend on `sqlalchemy`/`httpx`, neither of which is installed in this
sandbox (no PyPI access, the same constraint noted throughout this project's
backend phases) -- these were verified via `py_compile`, an AST-level sweep
confirming every function has a docstring and full type annotations, and
manual review pattern-matched against already-working precedent
(`dns_lookup.py` for the REST client shape, `objection_response_scanner.py`
for the scanner shape, `privacy.py`'s delete-request route for the public
token-verified route shape). A real `pytest` run against a live Postgres
instance (e.g. via `docker compose up` + `alembic upgrade head` +
`pytest tests/test_pipeline_transitions.py`) is the way to fully confirm the
DB-touching paths.

**Known gaps, not fixed here**: no webhook or reconciliation job detects a
meeting being cancelled or rescheduled directly in Google Calendar (outside
this app) -- `Meeting.status` only ever gets set by this app's own code, so
an out-of-band cancellation would leave a stale `booked` row. There is no
"reschedule" flow; a prospect who wants a different time needs a fresh
booking link. `GoogleCalendarClient` fetches a new access token on every
call rather than caching it against its `expires_in` -- a documented
tradeoff, not a bug, revisit if call volume grows. The frontend has no UI
for any of this yet (no booked-meetings view, no way to trigger a booking
link manually outside of the reply-triggered scanner) -- everything here is
backend-only, matching the scope of the request.

---

## LinkedIn outreach channel (draft-only, copy-to-clipboard)

A new `OutreachChannel.LINKEDIN`: generates a connection-request note and a
follow-up message as plain text, grounded in the same audit findings and
matched service every other channel uses. No sending, no scraping, no
LinkedIn API integration anywhere -- LinkedIn's User Agreement prohibits
automating platform actions without their separate written permission, so
this system only ever produces text for a rep to copy and paste manually.

**One design fork was asked before touching the frontend** (via
`AskUserQuestion`): how real should the "copy to clipboard" UI be. Chose:
build a genuinely new, real (non-mock-only) outreach-queue page that calls
the actual `GET /outreach/queue` and `GET /leads/{id}` endpoints, falling
back to clearly-labeled sample drafts when unconfigured -- over bolting
copy buttons onto the existing mock-only workflow-grid panel, or shipping
backend-only this round. This is the frontend's second genuinely wired
feature (after the dashboard chat panel) and its first real drafts-review
surface.

**No backend send-route changes were needed.** `POST
/outreach/drafts/{id}/send` already rejects every non-EMAIL channel
outright (`app/api/v1/outreach.py`'s generic `if draft.channel is not
OutreachChannel.EMAIL` check, confirmed to already cover WhatsApp and
call-script drafts identically) -- adding `LINKEDIN` to the enum
automatically gets swept into that same rejection, with no special-casing.
Approval (`POST /outreach/drafts/{id}/approve`) also works unmodified,
since it doesn't branch on channel at all.

**Policy layer** (`app/services/outreach_policy.py`): `assess_linkedin()`
mirrors `assess_whatsapp()`'s shape but with a materially different
regime -- there's no platform-enforced opt-in gate the way WhatsApp has
one, so the only hard blocker is having no `linkedin_url` on file (a field
that already existed on `Lead`, unused until now). The "this is manual,
never automate it" warning is unconditional, appearing on every LinkedIn
decision regardless of whether the draft is otherwise allowed, since
unlike WhatsApp's template-approval warning (which only matters once
opt-in clears the hard gate) there's no scenario where this constraint
stops applying.

**Two-piece content shape** (`app/agents/outreach.py::generate_linkedin_content`):
a LinkedIn draft is genuinely two independent pieces of text sent at two
different times -- the connection note now, the follow-up only after the
prospect accepts -- not one message with an appendix. The note lives in
the existing `body` column (mirroring how every other channel's primary
text lives there); the follow-up lives in a new `linkedin_followup_message`
column, since forcing two time-separated messages into one field would
have made the frontend's "copy the right thing" job worse, not better.
LinkedIn's own 300-character connection-note limit is enforced twice: the
system prompt instructs the model to stay well under it, and a defensive
server-side truncation (logged as a warning if it fires) never lets a
verbose LLM response exceed LinkedIn's actual UI limit -- the same "don't
just trust the prompt for a hard, checkable constraint" posture already
used for URL-stripping in `generate_booking_reply`.

**Scoping decision, not yet a task anyone asked for**:
`campaign_followup_scanner.py` (the automated non-responding-lead cadence
scanner) was deliberately left untouched -- it still only cadences
EMAIL/WHATSAPP. LinkedIn's "follow-up" here is generated once, upfront,
alongside the connection note, timed by the rep manually after a real
LinkedIn acceptance event this system has no way to observe; it is not an
automated, non-responding-lead nudge the way the cadence scanner's
follow-ups are. Conflating the two would have meant either inventing a
fake signal for "did they accept the connection" or auto-generating a
LinkedIn follow-up on a schedule with no idea whether it's even relevant
yet.

**Data model + migration** (`app/db/models/outreach.py`,
`0017_linkedin_channel`): `outreach_drafts.linkedin_followup_message`
(nullable `Text`), `outreach_channel` enum extended with `'linkedin'` via
the same `ALTER TYPE ... ADD VALUE IF NOT EXISTS` pattern used in
`0002_places_discovery` and `0016_calendar_booking`. `UpdateDraftRequest`
also gained the field, so a rep can edit the follow-up text before
approving/copying it, mirroring how `whatsapp_template_name` is editable.

**Frontend** (`src/pages/outreach-queue.tsx`, `src/lib/outreachQueueApi.ts`,
`src/components/outreach/LinkedInDraftCard.tsx`): fetches pending-review
LinkedIn drafts via the real API when `NEXT_PUBLIC_DEV_API_TOKEN` is set
(same convention as the chat panel), joins each draft with its lead's
display name via parallel `GET /leads/{id}` calls, and falls back to two
clearly-labeled sample drafts (`src/lib/mockOutreachQueue.ts`) otherwise.
Each card shows the connection note (with a live character count against
LinkedIn's 300-char limit) and, if present, the follow-up message, each
with its own "Copy to clipboard" button (`navigator.clipboard.writeText`,
failing quietly if the browser denies clipboard access rather than
throwing) and a brief "Copied" confirmation. The card deliberately has no
approve/send button -- copy-to-clipboard is the entire supported
interaction, matching the channel's manual-only nature. New nav entry
"LinkedIn queue" added to `TopNav`.

**Verification**: `python -m py_compile` on every new/modified backend
file, a full `python -m compileall app alembic tests` sweep (clean), and an
AST sweep confirming every new function has a docstring. `assess_linkedin`
and the eligible-channels aggregation logic (pure, no DB/LLM) were exercised
directly in this sandbox -- both the policy checks and the LinkedIn fallback
text-generation logic (character limits, truncation math, no-contact-name
edge case) were smoke-tested by hand before being written up as 14 new/
updated `pytest`-style tests in `tests/test_outreach_policy.py`; the full
file (44 tests) passes via the sandbox's stdlib pytest shim.
`generate_linkedin_content` itself and the API routes are DB/LLM-dependent
(`sqlalchemy`/`openai`, neither installed in this sandbox) and were verified
by compile + manual review against the already-working `generate_objection_response`/
`generate_booking_reply` precedent instead. On the frontend, `next/core-web-vitals`
ESLint passed clean (zero errors/warnings) on every new/modified file; a
full `tsc --noEmit` could not be completed in this sandbox for the same
cross-platform mount I/O reason documented in every prior frontend phase --
every non-trivial type pattern here (a discriminated `CopiedField` union, a
`Map`-based lead join, optional chaining on a nullable API result) was
manually checked against the compiler's actual rules rather than assumed.

**Known gaps, not fixed here**: no "approve" button on the queue page --
reps can copy and use the text today, but marking a draft reviewed still
requires the API directly (e.g. Swagger UI) or a future pass wiring
`POST /outreach/drafts/{id}/approve` into this page. There is no way to
regenerate a single LinkedIn draft from the page (only from
`POST /leads/{id}/drafts` directly). The character counter on the
connection note is informational only -- the real 300-char enforcement is
server-side truncation at generation time, not a live-editing constraint,
since this page doesn't yet support editing draft text in place.
