# Everen BD Agent — Backend

FastAPI service covering the Services Knowledge Base (RAG), leads, and auth.

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt

cp ../.env.example ../.env        # then fill in real values
```

PostgreSQL 15+ with the `vector` extension available is required. The first
migration runs `CREATE EXTENSION IF NOT EXISTS vector`.

## Migrations

```bash
alembic upgrade head
```

## Seed data

`app/db/seed.py` contains **placeholder** services, pricing ranges, and
portfolio write-ups. Replace them with real Everen Techno content before any
client-facing use — the recommender quotes these prices verbatim.

```bash
python -m app.db.seed
```

## Run

```bash
uvicorn app.main:app --reload
```

Docs at `http://localhost:8000/docs`.

## Tests

```bash
pytest                              # full suite
pytest --cov=app --cov-report=term  # with coverage
```

The suite runs against in-memory SQLite and fakes OpenAI, so no database
server, API key, or network access is needed. Vector columns degrade to
JSON text on SQLite (see `app/db/types.py`); ranking correctness is asserted
directly against `app/services/similarity.py`.

## Auth

JWT verification is JWKS-based, so it works with **Clerk** or **Auth.js**
without a vendor SDK. Point these at your provider:

```dotenv
AUTH_JWKS_URL=https://<app>.clerk.accounts.dev/.well-known/jwks.json
AUTH_ISSUER=https://<app>.clerk.accounts.dev
AUTH_AUDIENCE=everen-bd-agent
```

Users are provisioned locally on first authenticated request, keyed on the
provider's `sub` claim. Roles come from a `role` claim, defaulting to `bd_rep`
when absent or unrecognized — never failing open.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/v1/services` | Create + index a service |
| `GET`  | `/api/v1/services` | List, paginated (max 100) |
| `GET`  | `/api/v1/services/{id}` | Fetch one |
| `POST` | `/api/v1/services/portfolio` | Create + index a case study |
| `POST` | `/api/v1/services/recommend` | RAG recommendation |
| `POST` | `/api/v1/services/reindex` | Rebuild embeddings (approver role only) |
| `POST` | `/api/v1/leads` | Create a lead |
| `GET`  | `/api/v1/leads` | List, filter by status/category/confidence |
| `GET`  | `/api/v1/leads/{id}` | Fetch one |
| `PATCH`| `/api/v1/leads/{id}` | Partial update |
| `POST` | `/api/v1/places/search` | Discover businesses by location + industry |
| `GET`  | `/api/v1/places/candidates` | List staged candidates |
| `POST` | `/api/v1/places/candidates/{id}/promote` | Promote to a lead |
| `POST` | `/api/v1/places/retention/sweep` | Manual coordinate purge (approver only) |
| `POST` | `/api/v1/audits` | Run a website audit + generate report |
| `GET`  | `/api/v1/audits/{id}` | Fetch audit, findings, and report |
| `PUT`  | `/api/v1/audits/leads/{id}/social` | Record a social profile review |
| `POST` | `/api/v1/leads/{id}/score` | Compute and store a lead score |
| `GET`  | `/api/v1/leads/{id}/score` | Fetch a lead's latest score |
| `GET`  | `/api/v1/leads/{id}/score/history` | Fetch a lead's score history |
| `POST` | `/api/v1/outreach/leads/{id}/drafts` | Generate drafts (always pending_review) |
| `GET`  | `/api/v1/outreach/queue` | Approval queue |
| `PATCH`| `/api/v1/outreach/drafts/{id}` | Edit a draft before approval |
| `POST` | `/api/v1/outreach/drafts/{id}/approve` | Approve (approver only) — does not send |
| `POST` | `/api/v1/outreach/drafts/{id}/reject` | Reject with a required reason |
| `POST` | `/api/v1/outreach/drafts/{id}/send` | **The only send path** (approver only) |
| `GET`  | `/api/v1/outreach/drafts/{id}/audit-log` | Status transition history |
| `GET`  | `/api/v1/outreach/quota` | Today's send quota standing |
| `GET`  | `/api/v1/outreach/unsubscribe` | Public one-click opt-out |
| `POST` | `/api/v1/outreach/webhooks/bounce` | Provider bounce webhook |

## Outreach

**The human-approval gate (AGENTS.md §8).** Drafts are created
`pending_review` and there is no code path that constructs one otherwise.
Approving marks a draft sendable; it does not dispatch. `POST .../send` is the
only endpoint that transmits, and it checks `status == approved` before
anything else. Every transition writes an `outreach_audit_log` row. Database
CHECK constraints back this up: `status='sent'` requires both `sent_at` and an
`approved_by_id`.

Verified by grep — one call site for the email sender, one place setting
`sent_at`, one place setting `APPROVED`, and no Celery task touches any of them.

**CAN-SPAM.** Every email carries sender identification, a physical postal
address, and a one-click unsubscribe (HMAC-signed, no login, single page
visit). `OUTREACH_PHYSICAL_ADDRESS` must be set to a real address or drafting
fails outright. Subjects are screened for fake `Re:`/`Fwd:` prefixes and false
transaction framing. Compliance is re-validated immediately before dispatch,
so an edit that strips the footer blocks the send rather than shipping.

**Suppression is permanent.** CAN-SPAM opt-outs never expire, so
`app/services/suppression.py` offers `suppress()` but deliberately no
unsuppress or bulk-clear. Unsubscribes, hard bounces, and spam complaints all
suppress the address *and* set `do_not_contact` on the lead, which flows into
the scoring engine's compliance gate — a suppressed lead can never resurface
as Hot.

**WhatsApp is opt-in gated, not cold-capable.** Meta's WhatsApp Business
Messaging Policy requires opt-in permission before any business-initiated
message, and business-initiated conversations must use a Meta-approved
Message Template. Since March 2026 Meta applies preemptive enforcement against
accounts showing rapid contact-list growth with high template send velocity
and low engagement — the exact signature of Places-discovery → bulk WhatsApp.
So a lead without `whatsapp_opt_in` produces **no WhatsApp draft at all**,
with the reason returned in `skipped[]`. Generating one anyway would put an
unsendable message in the queue where a reviewer might approve it.

**Call scripts** are documents a human reads; nothing is transmitted, so
there is no send path for them. UK leads get a CTPS warning, since unlike the
US DNC registry it does cover business numbers.

**Daily send limit** (`OUTREACH_DAILY_SEND_LIMIT`, default 50) is primarily a
deliverability control — cold domains that ramp volume get filtered — and
secondarily a blast-radius limit. The counter uses an atomic upsert so
concurrent sends cannot both read a stale count and exceed the cap.

## Lead scoring

`total = 0.30*Need + 0.25*Fit + 0.20*Contactability + 0.15*Revenue + 0.10*ComplianceRisk`,
banded to Hot (≥0.75) / Warm (≥0.50) / Cold / Do-Not-Contact.

**Design decision: ComplianceRisk is a gate, not just a weight.** As specified,
a 10% weight cannot on its own prevent a lead from surfacing as Hot — a lead
scoring ~0.95 on the other four components totals ~0.86 even with
ComplianceRisk at 0.0. `app/services/lead_scoring.py` instead evaluates
`Lead.do_not_contact` as a hard gate *before* banding: if it's set, the label
is `do_not_contact` regardless of the weighted total. The 10% weight still
applies to the stored total when the gate doesn't trigger, so residual,
sub-threshold compliance risk (e.g. no lawful basis on file) continues to
pull the score down as specified. See
`test_end_to_end_gate_beats_a_near_perfect_lead` in `tests/test_lead_scoring.py`
for the regression this fixes.

Component sources (`app/services/lead_signals.py`):

| Component | Derived from |
|-----------|--------------|
| Need | Inverse of latest website audit health score (70%) + social presence score (30%). Neutral (0.5) with no data. |
| Fit | Knowledge-base similarity search on the lead's category + notes against indexed services. |
| Contactability | Weighted channel presence (email 50%, phone 25%, LinkedIn 15%, website 10%) blended 70/30 with the lead's discovery confidence score. |
| Revenue | Best-fit matched service's price midpoint, normalized against `LEAD_SCORE_REVENUE_SCALE_MIN/MAX`. Neutral for custom-quoted or unmatched leads. |
| ComplianceRisk | 1.0 minus penalties for missing `consent_basis` (heavier in EEA/UK jurisdictions) and unverified Places-sourced contact data. Gate triggers on `do_not_contact`. |

Every computation is stored as a new `lead_scores` row rather than updated in
place, so a lead's scoring history — including the moment a Do-Not-Contact
flag started overriding its label — is auditable.

Scoring is compute-on-demand (`POST .../score`), not automatic on every lead
edit — Fit calls the embeddings API and costs money per call.

## Website audit

Checks and their sources:

| Check | Source |
|-------|--------|
| Speed, SEO meta, accessibility, best practices | PageSpeed Insights v5 (Lighthouse), mobile + desktop |
| Mobile responsiveness | Lighthouse `viewport` audit + mobile performance |
| SSL | Direct TLS handshake and certificate inspection |
| Contact form | HTML detection + `HEAD` probe of the form endpoint |
| Broken links | Bounded, robots-respecting crawl |

**The crawler fetches pages from third-party sites.** That is only defensible
while it stays polite, so it obeys `robots.txt`, identifies itself in the
User-Agent with a contact URL, stays on the audited host, caps at 25 pages /
depth 2, rate-limits to 1 req/sec, and uses `HEAD` before `GET`. Change
`USER_AGENT` in `app/services/site_checks.py` to a real contact URL before
production — an unidentified crawler is what site owners block.

**Contact forms are never submitted.** Detection plus a `HEAD` probe tells you
whether the endpoint exists without delivering a message to anyone's inbox.
Auto-submission would mean sending unsolicited mail to prospects at scale.

Audits are triggered per-lead by a BD rep. There is no bulk or automatic path,
deliberately — a human-initiated, attributable request is what keeps crawling a
stranger's site reasonable.

## Social presence review

**No scraping.** LinkedIn, Instagram, and Facebook all gate profile data behind
the profile *owner's* OAuth consent; there is no API that returns an arbitrary
business's profile, and scraping breaches their terms.

So `app/services/social_review.py` scores a structured checklist that a human
reviewer fills in after looking at public profiles. If a prospect later consents
and connects their accounts, populate the same `ProfileChecklist` from the
platform API and every scoring function keeps working unchanged.

## Google Places compliance

**Read before touching `place_candidates`.**

Google Maps Platform Service Specific Terms §10.3 permits caching *only*
latitude and longitude from the Places API, for at most 30 consecutive calendar
days. Separately, `place_id` is exempt from the caching restrictions and may be
stored indefinitely. Everything else — business name, formatted address, phone,
website, rating, types — is Google Maps Content and **must not be persisted**.

Consequences baked into this design:

- Dedup keys on **`place_id`**, not name+address. Name and address cannot be
  stored, and `place_id` is the better key anyway — stable across renames and
  address reformatting.
- `place_candidates` has **no name, address, phone, or website column**. To
  display those, re-fetch live from Places Details using `place_id`.
- Coordinates carry `coordinates_expire_at`. The sweeper in
  `app/tasks/places_retention.py` **must be scheduled** — without it, retention
  silently lapses.
- Promoting a candidate to a lead requires the caller to supply contact data
  and name an `enrichment_source`. Copying it from the Places response would
  breach the terms, so the endpoint cannot fill it for you.
- `app/services/places_policy.py` enforces the allowlist and **raises** on a
  restricted field rather than dropping it silently.

Attribution is also required when displaying Places content — see the
`attribution` field on the search response.

Not legal advice. Verify against the current terms before launch:
<https://cloud.google.com/maps-platform/terms/maps-service-terms>

## Outreach

No outreach endpoints exist yet. When they are added they must follow
AGENTS.md §8: drafts are created `pending_review`, and sending is a separate
endpoint that verifies `status == "approved"`. Nothing in this module sends
anything — the recommender produces recommendations only.
