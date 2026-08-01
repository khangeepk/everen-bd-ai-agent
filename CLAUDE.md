# CLAUDE.md — Everen BD Agent

> Guidance for Claude Code when working in this repository.

## Read AGENTS.md first

**[`AGENTS.md`](./AGENTS.md) is the source of truth** for coding rules, architecture
conventions, and agent behavior policies. Read it in full before generating or
modifying any code. This file does not replace it — it summarizes the constraints
that matter most and adds Claude-specific notes.

## Project status — continuing from Phase 0

Phase 0 (foundation scaffolding) was completed by **Antigravity**. The repo skeleton,
base configuration, and initial structure already exist.

- **Do not re-scaffold.** Inspect what's already there before creating directories,
  config files, or boilerplate.
- Extend existing patterns rather than introducing parallel ones.
- If scaffolded code conflicts with `AGENTS.md`, flag it rather than silently
  rewriting it.

## Non-negotiables

| Rule | Detail |
|------|--------|
| **Human approval before any outreach send** | Agents draft only. Every outreach object is created with `status="pending_review"`. Sending is a separate endpoint (`POST /api/v1/outreach/{draft_id}/send`) that verifies `status == "approved"`. No Celery task may auto-send. See AGENTS.md §8. |
| **Secrets live in `.env`** | Never hardcode keys. Backend reads them via the `Settings` class (`backend/app/core/config.py`). Never read, write, or populate `.env` — only update `.env.example`. See AGENTS.md §5. |
| **TypeScript on the frontend** | No `.js`/`.jsx` in `frontend/src/`. `strict: true` always. Avoid `any`; use `unknown` + type guards. Named exports. See AGENTS.md §4.1. |
| **FastAPI + Pydantic v2 on the backend** | Python 3.11+, async I/O throughout, all routes under `/api/v1/`, no raw `dict` return types. See AGENTS.md §4.2. |
| **PostgreSQL + pgvector** | UUID primary keys, `TIMESTAMPTZ` in UTC, Alembic for every migration, HNSW/IVFFlat index on vector columns, parameterized queries only. See AGENTS.md §9. |

## Every function Claude writes needs

1. A docstring — Google style for Python, JSDoc for TypeScript (AGENTS.md §7).
2. Logging — module-level `logger = logging.getLogger(__name__)`, `logger.info(...)`
   on success, `logger.exception(...)` in error paths (AGENTS.md §6).
3. Types — explicit annotations on all signatures.

## Layout

```
frontend/    TypeScript React/Next.js (Vite)
backend/     FastAPI — api/ core/ db/ services/ agents/ tasks/ schemas/
alembic/     Migrations
.env.example Template; .env is gitignored
```

## Working conventions

- **Ask before adding dependencies.** New `pip`/`npm` packages need approval.
- **Ask when requirements are ambiguous**, especially around data access or outreach
  sending. Do not assume.
- **Tests alongside features**: `pytest` + `pytest-asyncio` + `httpx.AsyncClient`
  (backend, ≥80% coverage on services/agents); `Vitest` +
  `@testing-library/react` (frontend). Mock OpenAI and SendGrid — never call live
  APIs in CI.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
  Branches: `feature/<ticket>-short-desc`. `main` is protected.
- Run the AGENTS.md §13 security checklist before any PR touching auth, outreach,
  or data access.

---

*Companion to AGENTS.md — if the two ever disagree, AGENTS.md wins.*
