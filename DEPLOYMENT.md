# Deployment guide — Everen BD Agent (Phase E)

This documents the manual, human-only steps needed to take the infrastructure
in this repo (`backend/Dockerfile`, `render.yaml`, `.github/workflows/`)
from code to a running production deployment. None of these steps can be
done by an agent — they require a real account, real credentials, and a
person clicking "confirm."

## 1. Push this repo to GitHub

This repo has no git history yet.

```bash
cd "Evren BD AI Agent"
git init
git add .
git status   # sanity-check nothing in backend/.env or other secrets is staged
git commit -m "chore: initial commit"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Then, on GitHub: **Settings → Branches** → add a protection rule for `main`
requiring the `lint-and-test` check to pass before merge (CLAUDE.md: "`main`
is protected").

## 2. Create a Render account and connect the repo

1. Sign up at [render.com](https://render.com) (GitHub OAuth is fastest).
2. **New → Blueprint**, select this repo. Render reads `render.yaml` from
   the repo root and proposes: a `everen-backend` web service, an
   `everen-redis` instance, and an `everen-db` Postgres database.
3. Apply the blueprint. Render will prompt you to fill in every `sync: false`
   env var declared in `render.yaml`:

   | Variable | Where it comes from |
   |---|---|
   | `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **must** be different from the dev placeholder in `.env.example` |
   | `OPENAI_API_KEY` | platform.openai.com API keys page |
   | `PAGESPEED_API_KEY` | Google Cloud Console → PageSpeed Insights API |
   | `GOOGLE_PLACES_API_KEY` | Google Cloud Console → Places API |
   | `SENDGRID_API_KEY` | SendGrid → Settings → API Keys |
   | `OUTREACH_FROM_EMAIL` | a verified SendGrid sender address |
   | `OUTREACH_PHYSICAL_ADDRESS` | a real street address / registered PO box (CAN-SPAM requirement — see `backend/CAN_SPAM_CHECKLIST.md`) |
   | `OUTREACH_PUBLIC_BASE_URL` | your Render service's public URL, e.g. `https://everen-backend.onrender.com` |
   | `AUTH_JWKS_URL` / `AUTH_ISSUER` / `AUTH_AUDIENCE` | from your Clerk/Auth.js identity provider dashboard |
   | `CORS_ORIGINS` | your deployed frontend's origin(s) |
   | `SENTRY_DSN` | from step 4 below (leave blank until then, error alerting stays disabled) |

4. After the first manual deploy succeeds, enable **Postgres → Backups** on
   the `everen-db` database (automated daily backups + point-in-time
   recovery are included on the `basic-256mb` plan already specified in
   `render.yaml` — this is a one-time confirmation click, not a paid
   upgrade).
5. Under `everen-backend` → **Settings → Deploy Hook**, copy the deploy hook
   URL. This is what CI/CD calls to trigger a deploy after tests pass (step
   3 below).

## 3. Wire up GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `RENDER_DEPLOY_HOOK_URL` | the deploy hook URL from step 2.5 |
| `HEALTH_CHECK_URL` | `https://<your-render-service>.onrender.com/health/ready` |
| `DATABASE_URL` | the **external** connection string from Render's Postgres dashboard (for the daily backup workflow only — different from the internal one the web service uses) |
| `BACKUP_S3_BUCKET` (optional) | if you want backups mirrored to S3-compatible storage instead of just GitHub artifacts |
| `BACKUP_AWS_ACCESS_KEY_ID` / `BACKUP_AWS_SECRET_ACCESS_KEY` (optional) | only needed alongside `BACKUP_S3_BUCKET` |

`GITHUB_TOKEN` (for pushing to GHCR) is provided automatically — no setup
needed.

Once these are set, pushing to `main` runs: lint → test → build & push image
to `ghcr.io/<you>/<repo>` → trigger Render deploy → poll `/health/ready`
until it passes.

## 4. Sentry (error alerting)

1. Sign up at [sentry.io](https://sentry.io) (free tier: 5k errors/month).
2. Create a new project, platform "FastAPI".
3. Copy the DSN it gives you into the `SENTRY_DSN` env var on the Render
   service (Settings → Environment).
4. Nothing else to do — `app/main.py` calls `sentry_sdk.init()` on startup
   only when `SENTRY_DSN` is non-blank (see the `_configure_sentry` function
   there). `send_default_pii=False` is set explicitly so lead contact info
   never leaves the system via an error report.

## 5. UptimeRobot (primary uptime monitor)

1. Sign up at [uptimerobot.com](https://uptimerobot.com) (free tier: 50
   monitors, 5-minute interval).
2. **Add New Monitor** → HTTP(s) → URL:
   `https://<your-render-service>.onrender.com/health/ready`.
3. Set alert contacts (email/SMS/Slack webhook) under
   **My Settings → Alert Contacts**.
4. This is the primary monitor. `.github/workflows/uptime-ping.yml` is a
   free, zero-account backstop that pings the same endpoint every 10
   minutes and opens a GitHub issue if it fails — useful before UptimeRobot
   is set up, or as a second independent signal, but GitHub Actions cron
   isn't guaranteed to fire on schedule and can only alert via an issue, not
   SMS/push, so don't rely on it alone.

## 6. Daily DB backup — what's already automatic vs. what needs the above

- **Automatic once step 2.4 is confirmed**: Render's own daily Postgres
  backup with point-in-time recovery. This is the primary mechanism and
  needs no code.
- **Automatic once `DATABASE_URL` secret is set (step 3)**:
  `.github/workflows/db-backup.yml` runs `backend/scripts/backup_db.sh`
  daily at 09:00 UTC, uploading the dump as a 35-day GitHub Actions
  artifact (or to S3-compatible storage if `BACKUP_S3_BUCKET` is set). This
  exists so your backups aren't locked to Render specifically — useful if
  you ever migrate providers.
- Restore a dump with:
  `pg_restore --clean --if-exists --no-owner --no-privileges -d "$DATABASE_URL" <file>.dump`

## Local development

```bash
cd backend
cp .env.example .env   # then fill in real values
docker compose up --build
```

This runs Postgres (with pgvector), Redis, and the backend together, running
`alembic upgrade head` before starting Gunicorn. The test suite itself does
not need this stack — `pytest` runs entirely against in-memory SQLite (see
`tests/conftest.py`).

## Cost estimate (Render, monthly)

| Item | Plan | ~Cost |
|---|---|---|
| Web service | Starter | $7 |
| Postgres | Basic-256mb (incl. daily backups) | $6 |
| Redis | Starter | $10 |
| Sentry | Free tier | $0 |
| UptimeRobot | Free tier | $0 |
| **Total** | | **~$23/mo** |

Cheaper alternatives exist (Fly.io's smallest VMs, Railway's usage-based
pricing) but require more manual Postgres/backup setup in exchange; Render
was chosen for the lowest total setup effort at a comparable price point for
this project's current scale. Revisit if usage grows significantly.
