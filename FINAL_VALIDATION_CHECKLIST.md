# FINAL VALIDATION CHECKLIST — PRODUCTION READINESS
**Status:** Ready for local testing before Render deployment

Execute ALL items below on your local machine. Each must PASS before proceeding to production.

---

## ✅ ITEM 1: BACKEND TESTS + COVERAGE >85%

### Prerequisites
```bash
cd backend
# Ensure you have:
# - Python 3.11+ 
# - Virtual environment activated (.venv)
# - Requirements installed: pip install -r requirements.txt
# - pytest 9.1.1+, pytest-asyncio, pytest-cov installed
```

### Run Tests
```bash
cd backend

# Full test suite with coverage report
pytest tests/test_sendgrid_webhook_security.py tests/test_outreach_pause.py \
  -v \
  --cov=app \
  --cov-report=term-summary \
  --cov-report=html

# Expected output:
# ✓ test_sendgrid_webhook_security.py::test_valid_signature_accepted PASSED
# ✓ test_sendgrid_webhook_security.py::test_invalid_signature_rejected PASSED
# ✓ test_sendgrid_webhook_security.py::test_missing_headers_rejected PASSED
# ✓ test_sendgrid_webhook_security.py::test_production_fail_closed_unconfigured PASSED
# ✓ test_outreach_pause.py::test_pause_endpoint_rejects_bad_secret PASSED
# ✓ test_outreach_pause.py::test_pause_endpoint_pauses_pending_drafts PASSED
# ... (total 14+ test cases)
#
# coverage summary:
# Name                    Stmts   Miss  Cover
# app/agents/            ...
# app/api/                ...
# app/services/          ...
# app/db/                ...
# TOTAL                   XXXX    XXX   XX%
```

### ✅ PASS CRITERIA
- [ ] All tests PASS (no FAILED or ERROR)
- [ ] Coverage ≥ 85% (minimum threshold)
- [ ] No warnings (only INFO/DEBUG logs acceptable)
- [ ] Run time < 2 minutes

### View HTML Report
```bash
# Open coverage report in browser
open htmlcov/index.html        # macOS
xdg-open htmlcov/index.html    # Linux
start htmlcov/index.html       # Windows
# Look for: coverage bar should be GREEN (>85%)
```

### Troubleshooting
```bash
# If "ModuleNotFoundError: No module named 'pytest'"
pip install pytest pytest-asyncio pytest-cov --break-system-packages

# If "FAILED test_sendgrid_webhook_security.py::test_..."
# → Check that SENDGRID_WEBHOOK_VERIFICATION_KEY is set in .env (can be empty for dev)
# → Re-run with verbose output: pytest -vvs tests/test_sendgrid_webhook_security.py

# If coverage < 85%
# → Run full test suite: pytest tests/ --cov=app --cov-report=term-summary
# → Check coverage by module: look at htmlcov/index.html for gaps
```

---

## ✅ ITEM 2: DATABASE BACKUP/RESTORE DRILL

### Prerequisites
```bash
# Ensure you have:
# - PostgreSQL 15+ installed (psql, pg_dump available)
# - Docker Compose running (if using local postgres container)
# - Valid DATABASE_URL in .env
export DATABASE_URL="postgresql://user:password@localhost:5432/everen_db"
```

### Create Backup from Production-Like Database
```bash
# Backup the development database (simulating production backup)
pg_dump -U postgres \
  --clean \
  --if-exists \
  --format=custom \
  everen_db > backup_prod.sql.dump

# Verify backup file exists and is not empty
ls -lh backup_prod.sql.dump
# Expected: 10 MB - 50 MB file size (depends on seed data)

# Log backup creation
echo "✓ Backup created: $(date)" >> backup_log.txt
```

### Restore to Staging Database
```bash
# Create staging database if it doesn't exist
createdb everen_db_staging 2>/dev/null || echo "Database exists"

# Restore from backup
pg_restore --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  -d everen_db_staging \
  backup_prod.sql.dump

# Verify restore succeeded
psql -d everen_db_staging -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public';"
# Expected: 40+ tables
```

### Data Integrity Verification
```bash
# Connect to restored database
psql -d everen_db_staging

# Run verification queries:
SELECT COUNT(*) as leads_count FROM leads;
SELECT COUNT(*) as drafts_count FROM outreach_drafts;
SELECT COUNT(*) as audit_log_count FROM outreach_audit_logs;
SELECT COUNT(*) as pipeline_events FROM pipeline_events;
SELECT COUNT(*) as meetings FROM meetings;
SELECT COUNT(*) as suppression_entries FROM suppression_entries;

# Example expected results (with seed data):
#  leads_count: 100+
#  drafts_count: 50+
#  audit_log_count: 200+
#  pipeline_events: 150+
#  meetings: 20+
#  suppression_entries: 10+

# Verify no data corruption
SELECT COUNT(*) FROM outreach_drafts WHERE body IS NULL;
# Expected: 0 (no NULL bodies, all drafts have content)

SELECT COUNT(*) FROM leads WHERE contact_email_hash IS NULL AND contact_email IS NOT NULL;
# Expected: 0 (all emails have blind-index hash)

# Check for orphaned foreign keys
SELECT COUNT(*) FROM outreach_drafts d 
WHERE NOT EXISTS(SELECT 1 FROM leads l WHERE l.id = d.lead_id);
# Expected: 0 (all drafts reference valid leads)

\q  # Exit psql
```

### ✅ PASS CRITERIA
- [ ] `pg_dump` completes without errors (backup file created)
- [ ] `pg_restore` completes without errors
- [ ] All verification queries return expected row counts
- [ ] No NULL or orphaned data detected
- [ ] Data types are correct (encrypted fields are bytea, hashes are text, etc.)

### Cleanup
```bash
# Drop staging database after verification
dropdb everen_db_staging

# Archive backup (keep for 30 days as per DEPLOYMENT.md)
mv backup_prod.sql.dump backups/backup_$(date +%Y%m%d_%H%M%S).dump
```

### Troubleshooting
```bash
# If "pg_dump: command not found"
# Install PostgreSQL client tools:
# macOS: brew install postgresql
# Linux: sudo apt-get install postgresql-client
# Windows: Use WSL or install PostgreSQL for Windows

# If "permission denied" on database
# Check superuser role:
psql -U postgres -c "SELECT current_user, usesuper FROM pg_user;"
# If usesuper=false, use a superuser account or grant privileges:
psql -U postgres -c "ALTER USER your_user SUPERUSER;"

# If restore fails with "does not exist"
# Drop database first:
dropdb everen_db_staging
createdb everen_db_staging
pg_restore ... (retry)
```

---

## ✅ ITEM 3: FRONTEND PRODUCTION BUILD + TYPESCRIPT CHECK

### Prerequisites
```bash
cd frontend

# Ensure:
# - Node 18+ installed (node --version)
# - npm 9+ installed (npm --version)
# - node_modules installed (npm install)
# - package.json has build + type-check scripts
```

### Run TypeScript Strict Mode Check
```bash
cd frontend

# Type checking only (no emit)
npm run type-check

# Expected output:
# > next build --noEmit
# Checking TypeScript...
# ✓ No errors found.
# Next.js compilation successful
```

### Run Production Build
```bash
cd frontend

# Full production build
npm run build

# Expected output:
# > next build
# ▲ Next.js 15.1.0
# ▀ Creating an optimized production build...
# ✓ Compiled successfully
# ✓ Collecting page data...
# ✓ Generating static pages...
#
# ┌ ▲ Next.js 15.1.0
# │ Compiled and linted in: 120s
# │ Build complete. Ready to deploy.
# ├ _app.tsx (SSG)
# ├ pages/dashboard.tsx (SSG)
# ├ pages/leads.tsx (SSG)
# ...
# └ [output] .next/static/...
```

### Verify Bundle Size
```bash
cd frontend

# Check output directory
du -sh .next/
# Expected: 5-20 MB total
# Expected build time: 2-5 minutes

du -sh .next/static/
# Expected: JavaScript bundles 500KB - 2MB (gzipped)

# View detailed bundle breakdown
ls -lh .next/static/chunks/
# Largest chunk should be < 500KB (if exceeds, consider code-splitting)
```

### ✅ PASS CRITERIA
- [ ] `npm run type-check` returns 0 errors
- [ ] `npm run build` completes without errors or warnings
- [ ] Build time < 5 minutes
- [ ] `.next/` directory created and > 1MB
- [ ] No TypeScript `any` types in errors (strict mode enforced)
- [ ] No console.error or warnings in build output

### Troubleshooting
```bash
# If "npm: command not found"
# Install Node.js from https://nodejs.org/ (18+ LTS recommended)

# If "FAIL types" in type-check
npm run type-check -- --listFiles | grep -i error
# Fix TypeScript errors in src/ files before proceeding

# If build fails with "Module not found"
npm install
npm run build  # Retry

# If build is very slow (>10 min)
# Check for heavy dependencies:
npm ls --depth=0 | grep -E "large|heavy"
# Consider: next/image optimization, dynamic imports for code-splitting
```

---

## 🎯 FINAL CHECKLIST

Once all 3 items PASS locally, you're cleared for production deployment.

```
BACKEND TESTS (Item 1)
  ✓ pytest passes all test cases
  ✓ Coverage ≥ 85%
  ✓ No errors or warnings

DATABASE BACKUP/RESTORE (Item 2)
  ✓ pg_dump creates backup file
  ✓ pg_restore completes successfully
  ✓ Data integrity verified (row counts, no orphans)
  ✓ All tables exist in staging

FRONTEND PRODUCTION BUILD (Item 3)
  ✓ npm run type-check: 0 errors
  ✓ npm run build: successful, no errors
  ✓ Build time < 5 minutes
  ✓ Bundle size reasonable (< 20 MB)
  ✓ .next/ directory ready for deployment

READY FOR PRODUCTION DEPLOYMENT
  ✓ All 3 items pass
  ✓ DEPLOYMENT.md reviewed
  ✓ Environment variables prepared (Render secrets set)
  ✓ Render blueprint configured
  → PROCEED TO DEPLOYMENT
```

---

## 📋 DEPLOYMENT STEPS (After Validation Passes)

Once all local tests pass, deploy to Render:

### 1. Prepare Git
```bash
git add -A
git commit -m "chore: final validation before production"
git push origin main
```

### 2. Monitor CI/CD
```bash
# GitHub Actions should trigger automatically:
# 1. Lint & test (backend)
# 2. Build & push Docker image to GHCR
# 3. Trigger Render deploy hook
# 4. Wait for /health/ready to respond (20-30 seconds)
```

### 3. Verify Production
```bash
# Once Render deployment completes:
curl https://your-render-service.onrender.com/health/ready
# Expected: { "status": "ready" }

# Check logs
# Render dashboard → logs → verify no errors
# Look for: "Application startup complete" message

# Test webhook (optional, using SendGrid test event)
curl -X POST https://your-render-service.onrender.com/api/v1/outreach/webhooks/bounce \
  -H "X-Twilio-Email-Event-Webhook-Signature: test" \
  -H "X-Twilio-Email-Event-Webhook-Timestamp: 1234567890" \
  -d '{"test": "event"}'
# Expected: 401 (invalid signature, which is correct behavior)
```

### 4. Enable Monitoring
```bash
# Sentry: Check error alerting
# UptimeRobot: Verify monitoring active
# Logs: Tail production logs in Render dashboard
```

---

## 🎉 SUCCESS CRITERIA

Production deployment is **GO** when:

1. ✅ All 3 local validation items PASS
2. ✅ GitHub Actions CI/CD completes without errors
3. ✅ `/health/ready` responds with 200 OK
4. ✅ No errors in production logs (Render dashboard)
5. ✅ SendGrid webhook signature verification is enforced (test 401 on invalid signature)

**Estimated time to completion:** 2-3 hours (1 hour validation + 1 hour deployment + monitoring)

---

**Prepared by:** Claude Production Readiness Audit  
**Date:** 2026-08-01  
**Status:** Ready for execution on local machine
