# LAUNCH_SECRETS — key-paste launch checklist

Everything the live system needs, so going live is a paste job. Set these in
**Render → everen-backend → Environment** (the `sync:false` ones from
`render.yaml`). Env var names are the UPPERCASE of the `Settings` fields in
`backend/app/core/config.py`.

> **Safety first:** for the very first live run, keep `PLACES_TEST_MODE=true`
> and `SENDGRID_SANDBOX_MODE=true` (below). With those on, a mistyped key
> cannot burn credit or send a real email.

---

## A. REQUIRED TO BOOT (production starts correctly)

Every field in `config.py` has a placeholder default, so the process will not
*crash* without these — but production is only correct once they're set. The
first three are injected by Render automatically from `render.yaml`.

| Env var | Where it comes from | Test-mode / safe value |
|---|---|---|
| `DATABASE_URL` | **Auto** — Render `fromDatabase: everen-db` | (managed by Render) |
| `REDIS_URL` | **Auto** — Render `fromService: everen-redis` | (managed by Render) |
| `SECRET_KEY` | **Auto** — Render `generateValue: true` | (managed by Render) |
| `APP_ENV` | Set in `render.yaml` | `production` |
| `LOG_LEVEL` | Set in `render.yaml` | `INFO` |
| `ENCRYPTION_KEY` | **You must set this.** Generate: `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` | paste directly into Render only |

**`ENCRYPTION_KEY` is a real secret — do NOT paste it into this (public) repo.**
A ready-to-use value was generated for you and shared privately in chat; paste
it straight into **Render → Environment**, nowhere else. Keep it safe: if it
changes, all previously-encrypted PII becomes unreadable. If you lost it,
regenerate with the command above (only safe before any real data is stored).

---

## B. REQUIRED FOR FUNNEL (discover → enrich → audit → score → draft → approve → send)

Without these the API boots and `/health` is 200, but the funnel can't do real
work. Leave any as `not-set-yet` and that stage no-ops (safe, not a crash).

| Env var | Where obtained | Safe test-mode value |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | Google Cloud Console → enable **Places API** → Credentials | real key **+ `PLACES_TEST_MODE=true`** (caps to 10 reqs) |
| `PAGESPEED_API_KEY` | Google Cloud Console → enable **PageSpeed Insights API** | real key (this API is free) |
| `OPENAI_API_KEY` | platform.openai.com → API keys | real key; **budget-capped at $20/day** (below) |
| `SENDGRID_API_KEY` | SendGrid → Settings → API Keys (free account ok) | real key **+ `SENDGRID_SANDBOX_MODE=true`** (accepts, never delivers) |
| `OUTREACH_FROM_EMAIL` | a **verified** SendGrid sender address | your verified sender |
| `OUTREACH_PHYSICAL_ADDRESS` | real street address / PO box (CAN-SPAM legal req) | your business address |
| `OUTREACH_PUBLIC_BASE_URL` | your Render URL (for unsubscribe/tracking links) | `https://everen-backend.onrender.com` |
| `AUTH_JWKS_URL` | Clerk/Auth.js dashboard → JWKS endpoint | needed to log in & hit protected routes |
| `AUTH_ISSUER` | Clerk/Auth.js dashboard | ″ |
| `AUTH_AUDIENCE` | Clerk/Auth.js dashboard | ″ |

### Launch-day safety toggles (SET THESE for the first run)

| Env var | Set to | Effect |
|---|---|---|
| `PLACES_TEST_MODE` | `true` | Hard-caps Places calls to `PLACES_TEST_MODE_MAX_REQUESTS` (10) per process |
| `SENDGRID_SANDBOX_MODE` | `true` | SendGrid validates + accepts but **never delivers** — full pipeline runs, no real email |

---

## C. OPTIONAL (add when needed)

| Env var | Where obtained | Notes |
|---|---|---|
| `SENDGRID_WEBHOOK_VERIFICATION_KEY` | **SendGrid → Settings → Mail Settings → Signed Event Webhook** (SendGrid generates it; free) | Empty = bounce webhook fail-closes (401 on everything). See test key below. |
| `SENTRY_DSN` **(recommended)** | sentry.io → new FastAPI project (free tier: 5k errors/mo) | Blank = error alerting disabled (inert, safe). **Recommended** so you find out about production errors — payloads are PII-scrubbed (emails/phones stripped via the `before_send` hook), `traces_sample_rate=0.1` keeps it in the free tier. |
| `CORS_ORIGINS` | your deployed frontend origin(s) | Blank = localhost defaults; fine with no frontend |
| `N8N_WEBHOOK_SECRET` | your choice (shared secret) | Only for the n8n pause webhook |
| `GOOGLE_CALENDAR_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` | Google Cloud OAuth | Only for meeting-booking module |
| `DELIVERABILITY_CHECK_DOMAIN`, `SENDGRID_DKIM_SELECTORS` | your sending domain / SendGrid DKIM | Only for the deliverability checker |
| `DATABASE_URL` (external) + `BACKUP_S3_*` | Render Postgres external string / S3 creds | Only for the GitHub Actions backup workflow |

### SendGrid webhook — the real key vs. the test key

**Production:** the verification key is **issued by SendGrid**, not self-made.
Enable *Signed Event Webhook*, copy the **public** key SendGrid shows, paste it
as `SENDGRID_WEBHOOK_VERIFICATION_KEY`. A self-generated key would reject all
real SendGrid events.

**Staging/testing only** — to exercise the signed→200 / unsigned→401 path
before wiring SendGrid, this repo generated a throwaway P-256 **public** key you
may set temporarily (the matching private key is intentionally NOT stored here;
the pytest suite already proves this path with its own ephemeral keypair):

```
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEam1wf4R8kRDKSdbW9KzQpjEHZxOQ
ALWTdoCY0ejKZuhRuPtLm7S9sKBG16E08Nf+876YwqDS4sYPIGnttUKVDA==
-----END PUBLIC KEY-----
```

---

## D. Cost guards — conservative defaults (already committed)

Verified in `backend/app/core/config.py`. A mistyped/over-eager key cannot run
away because these caps ship in the code, no env var required:

| Guard | Committed default | Line |
|---|---|---|
| `COST_GUARD_DAILY_BUDGET_PLACES_USD` | **$20.00 / day** | config.py:113 |
| `COST_GUARD_DAILY_BUDGET_OPENAI_USD` | **$20.00 / day** | config.py:114 |
| `COST_GUARD_ALERT_THRESHOLD` | **0.80** (alert at 80% of budget) | config.py:115 |
| `OUTREACH_DAILY_SEND_LIMIT` | **50 / day** | config.py:148 |
| `PLACES_TEST_MODE_MAX_REQUESTS` | **10** (when test mode on) | config.py:106 |

⚠️ **Important honesty note:** the *budget caps* above are conservative and
always on, but the two *safety modes* default to **OFF**:
`PLACES_TEST_MODE=false` (config.py:100) and `SENDGRID_SANDBOX_MODE=false`
(config.py:133). So for a safe first launch you **must explicitly set both to
`true`** (Section B). The $20/day caps still protect you either way, but
sandbox/test mode is what guarantees *zero* real spend and *zero* real sends on
run one.

---

## E. Minimum paste block for a safe first launch

Set these in Render and the app boots healthy, runs the full funnel with **no
real spend and no real email**:

```
APP_ENV=production
LOG_LEVEL=INFO
ENCRYPTION_KEY=<paste the key generated privately in chat — never commit it>
OUTREACH_PUBLIC_BASE_URL=https://everen-backend.onrender.com
PLACES_TEST_MODE=true
SENDGRID_SANDBOX_MODE=true
# then add real keys as you get them:
GOOGLE_PLACES_API_KEY=...
PAGESPEED_API_KEY=...
OPENAI_API_KEY=...
SENDGRID_API_KEY=...
OUTREACH_FROM_EMAIL=...
OUTREACH_PHYSICAL_ADDRESS=...
AUTH_JWKS_URL=...
AUTH_ISSUER=...
AUTH_AUDIENCE=...
```

(`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` are injected by Render — don't set
them by hand.)
