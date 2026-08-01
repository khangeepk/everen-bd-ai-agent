# Everen Techno — BD AI Agent — Master Progress File

> **Single source-of-truth summary of work completed to date.**  
> Detailed implementation history remains in `master(1).md`.  
> This file is intentionally concise and should be updated after every completed phase.

---

## 🧭 Current State

**Project:** Everen Techno BD AI Agent  
**Backend:** FastAPI + Python 3.11+ + PostgreSQL + pgvector + SQLAlchemy 2.x + Alembic  
**Frontend:** Next.js 14 + React 18 + TypeScript strict + Tailwind  
**Async / Scheduling foundation:** Redis + Celery infrastructure present; full recurring scheduler wiring still pending  
**Auth:** JWKS-based JWT verification, compatible with Clerk/Auth.js; backend RBAC implemented  
**LLM / AI:** OpenAI-backed agents with deterministic fallbacks and API cost guard  
**Primary deployment target:** Render  
**Current active coding tool:** Antigravity (shifted from Claude Code when token limit was reached)

### Non-negotiable system rule

**AI-generated outreach must never auto-send.**  
Every outbound AI-generated message must enter the human approval workflow first. Existing send-gate, suppression, channel-policy, CAN-SPAM, warmup and quota controls must never be bypassed.

---

# ✅ COMPLETED / IMPLEMENTED WORK

## Phase 0 — Project Rules & Repo Foundation
- [x] `AGENTS.md` / `CLAUDE.md` project rules established
- [x] TypeScript frontend conventions defined
- [x] FastAPI backend architecture defined
- [x] PostgreSQL + pgvector selected
- [x] Human-approval-before-send rule made architectural requirement

---

## Phase 1 — Foundation: Knowledge Base, RAG, Auth, Leads
- [x] Services Knowledge Base models
- [x] pgvector embeddings / similarity search
- [x] OpenAI `text-embedding-3-small` integration
- [x] RAG service recommender with deterministic fallback
- [x] JWT/JWKS auth verification
- [x] JIT user provisioning
- [x] Lead database schema
- [x] Service routes
- [x] Lead routes

### Remaining production action
- [ ] Replace placeholder Everen Techno service/pricing/portfolio seed data with real business data before client-facing production use

---

## Phase 2 — Lead Discovery Engine
- [x] Google Places Text Search integration
- [x] Location + industry lead discovery
- [x] Google Places data-retention policy enforcement
- [x] `place_id`-based deduplication
- [x] Coordinate 30-day retention logic
- [x] Place candidate promotion flow
- [x] Google Places source tracking

### Remaining production action
- [ ] Wire Places coordinate-retention sweeper into recurring scheduler / Celery Beat

---

## Phase 3 — Website Audit + Social Presence Review
- [x] PageSpeed Insights integration
- [x] Performance / SEO / Accessibility / Best Practices audit
- [x] SSL/TLS checks
- [x] Contact-form detection without submitting forms
- [x] Robots-respecting bounded crawler
- [x] Website health scoring
- [x] Social checklist reviewer without prohibited scraping
- [x] AI-generated business-friendly audit report
- [x] Deterministic audit fallback

### Remaining production action
- [ ] Ensure production crawler User-Agent uses a real Everen contact URL if still placeholder

---

## Phase 4 — Lead Scoring Engine
- [x] Need score
- [x] Fit score
- [x] Contactability score
- [x] Revenue score
- [x] Compliance-risk score
- [x] Weighted Hot / Warm / Cold classification
- [x] Hard `do_not_contact` gate overriding lead score
- [x] Score history / audit trail
- [x] Score API routes

---

## Phase 5 — Compliant Outreach Engine
- [x] Email draft generation
- [x] WhatsApp draft eligibility logic
- [x] Call-script generation
- [x] Human approval queue
- [x] CAN-SPAM sender validation
- [x] Physical-address enforcement
- [x] Non-deceptive subject validation
- [x] HMAC unsubscribe tokens
- [x] Permanent suppression behavior
- [x] Bounce / complaint suppression path
- [x] Daily send quota
- [x] WhatsApp hard opt-in gate
- [x] Audit log for outreach transitions
- [x] No automatic sending from agents / background tasks

### Remaining production action
- [ ] SendGrid Event Webhook signature verification remains a launch-blocking security item unless Phase 21 already implemented it

---

## Phase 6 — CRM Pipeline, Reply Classifier & Call-Center Handoff
- [x] Pipeline stages and validated transition graph
- [x] Reply classifier
- [x] `book_call`, `pricing`, `interested`, `not_interested`, `unclear` intents
- [x] Human-routing for unclear replies
- [x] Auto-advance one safe pipeline step at a time
- [x] Hot-lead call-center card generation
- [x] Message history consolidation
- [x] Suggested call scripts
- [x] Pipeline event history
- [x] Outbound send → Contacted transition hook

---

## Phase 7 — Analytics, A/B Testing & Prompt Versioning
- [x] Analytics overview
- [x] Email sent / reply / meeting / won-deal metrics
- [x] Top industries / services analytics
- [x] Prompt version database
- [x] Deterministic A/B bucketing
- [x] Prompt performance reporting
- [x] Email-open tracking endpoint / event model
- [x] Prompt version tracking on drafts

### Known limitation
- [ ] Open tracking remains limited if production email body remains plain-text; HTML tracking implementation must remain compliant and intentional
- [ ] Prompt-version input validation should be hardened before allowing malformed active prompts

---

## Phase 8 — Testing & QA Hardening
- [x] End-to-end test structure added
- [x] Lead discovery E2E tests
- [x] Audit-engine E2E tests
- [x] Lead-scoring E2E tests
- [x] Outreach-approval E2E tests
- [x] Edge cases: no email, duplicate lead, API failure, rate limit
- [x] Logging audit / warning-level compliance-gate logging
- [x] Coverage summary documentation

### Verification note
Some DB/network-aware suites were historically statically verified in restricted sandboxes rather than executed end-to-end. A real full-suite staging run is still required before production launch.

---

## Phase 9 — Security & Compliance Hardening
- [x] Fernet-based PII encryption for primary lead/draft email & phone fields
- [x] Deterministic HMAC blind-index email lookup
- [x] RBAC simplified to `ADMIN` / `SALES` / `VIEWER`
- [x] Write-access enforcement across mutation routes
- [x] API cost guard for Google Places + OpenAI chat
- [x] 80% cost warning threshold
- [x] GDPR consent fields
- [x] Token-based deletion / erasure endpoint
- [x] PII scrubbing flow
- [x] CAN-SPAM checklist documentation

### Known hardening gaps
- [ ] Re-audit remaining plaintext PII fields such as call-card / suppression / bounce identifiers
- [ ] Close SendGrid signed webhook verification gap before scale

---

## Phase 10 — Deployment & DevOps Foundation
- [x] Dockerized backend
- [x] Multi-stage Docker build
- [x] Non-root runtime
- [x] Gunicorn + Uvicorn workers
- [x] Local Docker Compose
- [x] GitHub Actions CI/CD workflow
- [x] GHCR image build/push path
- [x] Render blueprint
- [x] Redis service
- [x] Managed PostgreSQL plan configuration
- [x] Sentry integration hook
- [x] `/health`
- [x] `/health/ready`
- [x] UptimeRobot guidance
- [x] GitHub uptime fallback workflow
- [x] Daily PostgreSQL backup script
- [x] GitHub Actions DB-backup workflow
- [x] Portable S3-compatible backup option
- [x] Deployment documentation

### Still manual / deployment-time
- [ ] GitHub repo credentials / first push
- [ ] Apply Render Blueprint
- [ ] Configure real production secrets
- [ ] Configure Sentry / uptime account
- [ ] Confirm managed Postgres automated backups in provider dashboard

---

## Phase 11 — Soft Launch / Test Mode
- [x] SendGrid Sandbox Mode support
- [x] Google Places test-mode request cap
- [x] No paid enrichment provider added
- [x] 280-candidate synthetic soft-launch re-run
- [x] 244 unique candidates after dedup
- [x] 244 audited/scored
- [x] 155 draft-eligible
- [x] 152 approved simulated sends
- [x] SendGrid sandbox sending validated
- [x] Daily quota behavior surfaced

### Soft-launch observations
- Email coverage was the largest funnel leak before enrichment work.
- A 50/day quota spreads a 150–300 send batch across multiple days.
- Synthetic reply/conversion outcomes must not be interpreted as real market performance.

---

## Phase 12 — Signals Engine
- [x] Job-posting change signal
- [x] Business-status change signal
- [x] Review-volume jump signal
- [x] Privacy/compliance-preserving hash-only Places-derived checkpoints
- [x] Signal history table
- [x] Mutable signal checkpoint table
- [x] Signal acknowledgement
- [x] High-signal leads promoted to top of lead queue
- [x] Places cost guard applied to Place Details calls
- [x] Google Places test-mode cap shared across discovery + Place Details

### Known limitation
- [ ] Signal scanning is still rep-triggered unless later scheduling work has been added

---

## Phase 13 — Free Waterfall Email Enrichment
- [x] Website contact-page email discovery
- [x] Footer/home-page fallback
- [x] `mailto:` extraction
- [x] Text email extraction
- [x] Person-specific domain-pattern guessing
- [x] Confidence score + source tracking
- [x] Enrichment attempt history
- [x] Applied candidate tracking
- [x] Enriched email stored unverified
- [x] Manual verification endpoint
- [x] Unverified enriched email blocked from outreach
- [x] No paid enrichment API

### Known limitation
- [ ] No automatic re-enrichment schedule yet unless later scheduling work has been added
- [ ] Generic role addresses such as `info@` / `sales@` are deliberately not guessed

---

## Frontend Foundation — B2B Deal Flow Dashboard
- [x] `frontend/` scaffold created
- [x] Next.js / React / TypeScript strict structure
- [x] Tailwind UI
- [x] Main B2B Deal Flow dashboard
- [x] KPI cards
- [x] Kanban-style funnel
- [x] Partner outreach analytics UI
- [x] Follow-up tracker
- [x] Workflow-node preview
- [x] Navigation structure
- [x] Mock-data isolation

### Known limitation
- Frontend initially launched mock-first; live API integration is only partially completed across later frontend phases.

---

## Phase 14 — Reply Objection Assistant
- [x] Objection sub-classification
- [x] Price objection
- [x] Timing objection
- [x] Not-interested-yet objection
- [x] Deterministic hard-opt-out recognition before objection generation
- [x] AI objection-response drafts
- [x] KB/audit/service grounding
- [x] Deterministic fallback response
- [x] Human-approved `pending_review` drafts only
- [x] Triggering-message linkage
- [x] Idempotency by triggering message + agent identity
- [x] Shared outreach audit logging

### Known limitation
- [ ] Hard opt-out text detected inside inbound replies should be reviewed for automatic suppression hardening; current historical implementation avoided auto-suppressing ambiguous classified replies

---

## Phase 15 — Deliverability Prep
- [x] SPF checker
- [x] DKIM checker
- [x] DMARC checker
- [x] Cloudflare DNS-over-HTTPS lookup client
- [x] Deliverability check history
- [x] Warmup schedule tracker
- [x] Warmup cap enforcement integrated into send quota
- [x] Readiness report
- [x] Sender-identity readiness check
- [x] SendGrid sandbox readiness awareness

### Known limitation
- DKIM selector discovery is configuration-based best effort; non-default selectors must be supplied manually.

---

## Phase 16 — Campaign Segmentation, Tone & Cadence
- [x] `cold`
- [x] `warm`
- [x] `re_engagement`
- [x] Campaign type on leads
- [x] Campaign type snapshot on drafts
- [x] Campaign-specific tone guidance
- [x] Follow-up cadence math
- [x] Follow-up sequence tracking
- [x] Follow-up draft generator
- [x] Campaign-performance analytics
- [x] Existing outreach eligibility reused for follow-ups
- [x] Follow-ups remain human-reviewed

### Default cadence currently implemented
- Cold: 3 / 7 / 14 days
- Warm: 2 / 5 days
- Re-engagement: 7 / 21 days

### Known limitation
- [ ] Cadence scanner remains rep-triggered unless later scheduler work has been added

---

## Phase 17 — Flexible Table / Lead Workflow UI
- [x] `/workflow` spreadsheet-style page
- [x] Lead rows + workflow-step columns
- [x] Discovered → enriched → audited → scored → drafted → approved → sent → replied flow
- [x] Sticky headers / first column
- [x] Status chips
- [x] Read-only review/detail panel
- [x] Safe rule: approved/sent spreadsheet cells do NOT directly approve/send
- [x] Navigation entry

### Known limitation
- [ ] Workflow grid is still substantially mock/simulated and requires full live API state wiring
- [ ] Sorting / filtering / pagination still needed for production-scale data

---

## Phase 18 — Natural Language Agent Control / Dashboard Chat
- [x] Dashboard chat panel
- [x] Rule-based natural-language parser
- [x] Lead-list query mapping
- [x] Google Places search query mapping
- [x] Existing backend endpoint reuse
- [x] Shared results table
- [x] Real API client introduced
- [x] Dev bearer-token support for current frontend development
- [x] Clearly labeled mock fallback in development

### Known limitations
- [ ] No production login/sign-in flow yet
- [ ] `NEXT_PUBLIC_DEV_API_TOKEN` is development-only and must not be production auth
- [ ] Fixed parser vocabulary / city ZIP lookup
- [ ] Score-filter query remains inefficient client-side approximation unless later backend query support is added

---

## Phase 19 — Google Calendar Meeting Booking
- [x] Shared sales-calendar architecture
- [x] Google Calendar OAuth refresh flow via REST
- [x] `freeBusy` integration
- [x] Public booking slots endpoint
- [x] Signed expiring booking tokens
- [x] Meeting confirmation endpoint
- [x] Fresh free/busy race check before booking
- [x] Calendar event creation
- [x] Meeting database model
- [x] Encrypted attendee email
- [x] `MEETING_BOOKED` pipeline stage
- [x] Analytics meeting metric corrected to confirmed bookings
- [x] Booking-link reply drafts remain human-approved
- [x] Deterministic booking URL generation outside the LLM

### Known limitations
- [ ] No Google Calendar cancellation/reschedule reconciliation yet
- [ ] No prospect reschedule flow yet
- [ ] Google Calendar access-token caching not implemented
- [ ] Booking frontend UI remains limited/backend-focused

---

## Phase 20 — LinkedIn Draft Channel
- [x] `OutreachChannel.LINKEDIN`
- [x] Connection-request note generation
- [x] LinkedIn follow-up message generation
- [x] Server-side 300-character connection-note enforcement
- [x] Manual-only policy
- [x] No LinkedIn API sending
- [x] No LinkedIn scraping
- [x] Existing generic send route rejects LinkedIn sends
- [x] LinkedIn follow-up DB field
- [x] Real outreach-queue frontend page
- [x] Real pending-draft API fetch when development token configured
- [x] Copy-to-clipboard UI
- [x] Sample-data fallback clearly labeled in development
- [x] Navigation entry

### Known limitations
- [ ] Queue page still needs real approve/reject/edit workflow integration
- [ ] No single-draft regenerate action from frontend
- [ ] LinkedIn timing remains manual because the system cannot observe connection acceptance

---

# 🔄 PARTIAL / UNCONFIRMED PHASES

## Phase 21 — Mailbox Health Monitoring
**Status: [~] PARTIAL / IN PROGRESS / MUST VERIFY FROM REPOSITORY**

Short master previously recorded:
- n8n selected for mailbox-health / bounce / spam alerts
- phase shifted from Claude Code to Antigravity mid-phase due to token limit
- prompt reissued in Antigravity task-list/plan format

However, the detailed progress log currently available does **not** contain a completed Phase 21 implementation section.

Therefore:
- [~] Treat as incomplete until actual source-code / n8n workflow evidence confirms completion
- [ ] Do not duplicate any Phase 21 work without inspecting repository first
- [ ] Verify whether SendGrid webhook-signature hardening was included here
- [ ] Verify whether mailbox auto-pause / health thresholds were implemented
- [ ] Verify whether n8n workflow file/export exists

---

## Phase 22 — Multilingual Outreach
**Status: [ ] PROMPT ISSUED / COMPLETION NOT CONFIRMED**

Short master recorded the prompt as issued to Antigravity, but the detailed progress log currently available contains no completed implementation section.

Before implementing anything:
- [ ] inspect repo for language detection/localization code
- [ ] inspect draft schemas for language metadata
- [ ] inspect prompts for localized outreach
- [ ] inspect tests
- [ ] only mark complete when real code evidence exists

---

# 🚨 CURRENT PRODUCTION / PRE-SCALE GAPS

These should be treated as higher priority than adding many new features.

## Security / Compliance
- [ ] Verify / implement SendGrid Event Webhook signature verification
- [ ] Re-audit all remaining plaintext PII fields
- [ ] Harden hard-opt-out reply handling into deterministic suppression if still absent
- [ ] Validate active PromptVersion templates before activation
- [ ] Re-audit prospect/user-controlled text for prompt-injection boundaries

## Scheduling / Automation
- [ ] Configure production Celery worker + single Celery Beat scheduler
- [ ] Schedule mandatory Google Places retention sweeper
- [ ] Decide which optional scanners may run automatically
- [ ] Prevent overlapping recurring-job executions
- [ ] Add run-history / operational visibility if absent

## Frontend / Auth
- [ ] Implement real production sign-in flow using selected Clerk/Auth.js setup
- [ ] Remove production reliance on `NEXT_PUBLIC_DEV_API_TOKEN`
- [ ] Fully live-wire workflow page
- [ ] Fully live-wire dashboard and remaining mock sections
- [ ] Complete outreach approve / reject / send review UI
- [ ] Add production-safe pagination/filtering
- [ ] Add full frontend test suite + build verification

## Calendar
- [ ] Calendar cancellation reconciliation
- [ ] Calendar reschedule reconciliation
- [ ] Expiring watch-channel renewal if Calendar push notifications are added
- [ ] Prospect reschedule flow

## Launch Validation
- [ ] Full real dependency test run with PostgreSQL + Redis
- [ ] Full migrations test
- [ ] Full backend pytest suite
- [ ] Real frontend typecheck/test/build
- [ ] Backup restore drill
- [ ] Staging integration test matrix
- [ ] Secret scan / placeholder scan
- [ ] Production launch GO/NO-GO review

---

# 🧱 Stack / Architecture Decisions

## Kept
- FastAPI
- PostgreSQL
- pgvector
- SQLAlchemy / Alembic
- Next.js / React / TypeScript
- Redis
- Celery
- Render deployment target
- Standards-based JWKS JWT auth
- Human approval gate

## Added
- n8n for external workflow / mailbox-health-style orchestration

## Explicitly declined
- Supabase migration/rewrite

Reason: keep the existing custom PostgreSQL + auth architecture and avoid unnecessary rework.

---

# 💰 Paid API Policy

Current standing decision:

- Testing should use free / sandbox tiers where practical
- SendGrid → Sandbox Mode for test sends
- Google Places → bounded test-mode request cap
- Paid email enrichment APIs → not added yet
- Real paid API keys / production credentials → only at actual launch/configuration time

---

# 🧪 Verification Policy Going Forward

Every future phase must explicitly distinguish:

### EXECUTED AND PASSED
Actual test/build command ran successfully.

### STATICALLY VERIFIED
Syntax / compile / manual architectural review completed, but runtime dependency environment was unavailable.

### NOT EXECUTED / BLOCKED
Must include exact reason.

Never convert static verification into a claim that a real integration test passed.

---

# 🔜 Recommended Next Execution Order

1. **Finish / verify Phase 21 — Mailbox Health Monitoring**
2. **Finish / verify Phase 22 — Multilingual Outreach**
3. **Phase 23 — Production Scheduler / Celery Beat / Safe Recurring Jobs**
4. **Phase 24 — Production Authentication + Full Frontend/API Wiring**
5. **Phase 25 — Webhook Security + Remaining PII Closure**
6. **Phase 26 — Hard Opt-Out Automation + Prompt Safety**
7. **Phase 27 — Calendar Lifecycle Reconciliation + Rescheduling**
8. **Phase 28 — Staging E2E + Backup Restore + Production Launch Gate**

---

# 🛠 Tool Rotation

Current historical rotation:

**Claude Code (primary)** → when token limit reached → **Antigravity** → **Codex** as final fallback.

Trigger phrases previously used:
- `Shift to Antigravity`
- `Shift to Codex`

**Current recorded active tool: Antigravity.**

---

# 📌 Master File Maintenance Rule

After every completed phase:

1. Update the detailed progress log first.
2. Update this concise `Master.md` second.
3. Mark `[x]` only when implementation evidence exists.
4. Use `[~]` for genuinely partial/in-progress work.
5. Use `[ ]` for planned/prompt-issued/not-implemented work.
6. Never repeat a completed phase prompt.
7. Never silently redesign an existing module when extending it.
8. Before starting a new phase, inspect actual repository state rather than trusting an old checklist.

---

**Last reconciled from available progress records:** 01 Aug 2026  
**Source-of-truth detail file:** `master(1).md`
