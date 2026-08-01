# AGENTS.md - Everen BD Agent

> **Coding rules, architecture conventions, and agent behavior policies**
> for all contributors and AI agents working on this repository.
> Any AI agent (Copilot, Cursor, Antigravity, Claude, GPT, etc.) MUST read
> and follow every section below before generating or modifying code.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Repository Structure](#3-repository-structure)
4. [Language & Framework Rules](#4-language--framework-rules)
5. [Environment & Secrets Management](#5-environment--secrets-management)
6. [Error Handling & Logging](#6-error-handling--logging)
7. [Documentation & Docstrings](#7-documentation--docstrings)
8. [Outreach / Email - Human-Approval-Before-Send](#8-outreach--email--human-approval-before-send)
9. [Database Conventions](#9-database-conventions)
10. [API Design](#10-api-design)
11. [Testing](#11-testing)
12. [Git & PR Conventions](#12-git--pr-conventions)
13. [Security Checklist](#13-security-checklist)
14. [Agent Interaction Policies](#14-agent-interaction-policies)

---

## 1. Project Overview

**Everen BD Agent** is a full-stack SaaS platform designed to automate and assist
Business Development (BD) workflows. The system leverages AI agents to research leads,
draft outreach messages, and track pipeline activity -- always keeping a human in the
loop before any external communication is sent.

| Property      | Value                           |
|---------------|---------------------------------|
| Project Name  | Everen BD Agent                 |
| Type          | Node / Python Full-Stack SaaS   |
| Audience      | Internal BD teams & clients     |
| AI Agents     | Research, Drafting, Scheduling  |

---

## 2. Tech Stack

| Layer        | Technology                                      |
|--------------|-------------------------------------------------|
| **Frontend** | TypeScript - React (or Next.js) - Vite          |
| **Backend**  | Python - FastAPI - Pydantic v2                  |
| **Database** | PostgreSQL - pgvector (for AI embeddings)       |
| **ORM**      | SQLAlchemy 2.x (async) - Alembic (migrations)   |
| **Auth**     | JWT (access + refresh tokens) - OAuth 2.0       |
| **Queue**    | Celery + Redis (background tasks)               |
| **AI/LLM**   | OpenAI API / Anthropic API (via `.env` keys)    |
| **Email**    | SendGrid / SMTP (human-gated -- see Section 8)  |
| **Infra**    | Docker - Docker Compose - GitHub Actions CI     |

---

## 3. Repository Structure

```
everen-bd-agent/
|-- frontend/               # TypeScript React/Next.js app
|   |-- src/
|   |   |-- components/
|   |   |-- pages/
|   |   |-- hooks/
|   |   |-- lib/
|   |   +-- types/
|   |-- tsconfig.json
|   +-- package.json
|
|-- backend/                # Python FastAPI application
|   |-- app/
|   |   |-- api/            # Route handlers (v1/)
|   |   |-- core/           # Config, security, logging
|   |   |-- db/             # Models, migrations, sessions
|   |   |-- services/       # Business logic layer
|   |   |-- agents/         # AI agent orchestration
|   |   |-- tasks/          # Celery background tasks
|   |   +-- schemas/        # Pydantic request/response models
|   |-- tests/
|   |-- alembic/
|   |-- requirements.txt
|   +-- pyproject.toml
|
|-- .env.example            # Template -- copy to .env, never commit .env
|-- docker-compose.yml
|-- AGENTS.md               # <- this file
+-- README.md
```

---

## 4. Language & Framework Rules

### 4.1 Frontend -- TypeScript (REQUIRED)

- **All frontend code MUST be written in TypeScript.** JavaScript (`.js` / `.jsx`) files
  are not permitted inside `frontend/src/`.
- Enable `strict: true` in `tsconfig.json` at all times. Never disable strict checks.
- Use **explicit type annotations** on all function signatures, props, and API response
  types. Avoid `any`; use `unknown` + type guards instead.
- Define shared API response shapes in `frontend/src/types/` and keep them in sync with
  backend Pydantic schemas.
- Use **named exports** over default exports for components and utilities.

```typescript
// CORRECT
export function LeadCard({ lead }: LeadCardProps): JSX.Element { ... }

// WRONG
export default function(props: any) { ... }
```

### 4.2 Backend -- FastAPI (Python)

- All backend code MUST target **Python 3.11+**.
- Use **FastAPI** as the sole HTTP framework. Do not introduce Flask, Django, or plain
  ASGI apps.
- Define all request and response bodies with **Pydantic v2** models. Never use raw
  `dict` as a return type for endpoints.
- Use **async/await** for all I/O-bound operations (DB queries, HTTP calls, LLM calls).
- Route files live in `backend/app/api/v1/`. Always version the API under `/api/v1/`.

```python
# CORRECT
@router.post("/leads", response_model=LeadResponse, status_code=201)
async def create_lead(
    payload: LeadCreate, db: AsyncSession = Depends(get_db)
) -> LeadResponse:
    ...

# WRONG -- no types, no versioning, sync handler
@app.post("/lead")
def create(data):
    ...
```

---

## 5. Environment & Secrets Management

> **CRITICAL -- Never hardcode API keys, passwords, tokens, or any secret values
> in source code.**

### Rules

1. **All secrets MUST live in `.env`** (or a secrets manager such as AWS Secrets
   Manager / GCP Secret Manager in production).
2. The `.env` file is **gitignored** and MUST never be committed. Commit only
   `.env.example` with placeholder values.
3. Access environment variables in Python exclusively via the **`Settings` class**
   (Pydantic `BaseSettings`) in `backend/app/core/config.py`.
4. Access environment variables in TypeScript via `process.env.NEXT_PUBLIC_*` for
   public vars and server-side env utils for private vars. Never expose private keys
   to the client bundle.
5. Rotate any key found committed in Git history immediately and revoke the old key.

### .env.example Template

```dotenv
# App
APP_ENV=development          # development | staging | production
SECRET_KEY=CHANGE_ME

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/everen_db

# Redis
REDIS_URL=redis://localhost:6379/0

# AI / LLM
OPENAI_API_KEY=sk-REPLACE_ME
ANTHROPIC_API_KEY=REPLACE_ME

# Email / Outreach
SENDGRID_API_KEY=SG.REPLACE_ME
OUTREACH_FROM_EMAIL=bd@yourdomain.com

# Auth
JWT_SECRET=REPLACE_ME
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
```

### Backend Config Pattern

```python
# backend/app/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_env: str = "development"
    secret_key: str
    database_url: str
    openai_api_key: str
    sendgrid_api_key: str
    # ... other fields

    model_config = {"env_file": ".env", "case_sensitive": False}

settings = Settings()
```

---

## 6. Error Handling & Logging

> **Every service, agent, and background task MUST include structured error logging.**

### 6.1 Python Logging (Backend)

- Use Python's built-in `logging` module configured with **JSON-structured output**
  via `python-json-logger` (or `structlog`).
- Initialize a module-level logger in **every** Python file:

```python
import logging

logger = logging.getLogger(__name__)
```

- Log at appropriate levels:
  - `DEBUG`    -- fine-grained diagnostic info (dev only)
  - `INFO`     -- normal operations, key lifecycle events
  - `WARNING`  -- unexpected but recoverable situations
  - `ERROR`    -- caught exceptions, failed operations
  - `CRITICAL` -- system-level failures requiring immediate attention

- **Always log exceptions with full stack traces** using `logger.exception(...)`,
  NOT `logger.error(...)`:

```python
# CORRECT -- captures stack trace
try:
    result = await llm_client.complete(prompt)
except OpenAIError as exc:
    logger.exception("LLM completion failed for lead_id=%s", lead_id)
    raise HTTPException(status_code=502, detail="AI service unavailable") from exc

# WRONG -- swallows the stack trace
except Exception:
    print("Error occurred")
```

- Include contextual fields in every log: `user_id`, `lead_id`, `request_id`,
  `agent_name` where relevant.

### 6.2 TypeScript Logging (Frontend)

- Use a logging utility (e.g., `pino` or a thin wrapper over `console`) instead of
  raw `console.log` in production code.
- Never leave `console.log` statements in committed code.
- Surface API errors to users via toast/snackbar notifications; log technical details
  to the logger, not the UI.

### 6.3 FastAPI Global Exception Handler

Every unhandled exception MUST be caught by a global handler in `backend/app/main.py`:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler to log and return a safe error response."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again later."},
    )
```

---

## 7. Documentation & Docstrings

> **Every public function, class, and module MUST have a docstring.**

### 7.1 Python Docstring Style -- Google Style

```python
async def draft_outreach_email(
    lead: Lead, tone: str = "professional"
) -> OutreachDraft:
    """
    Generate a personalized outreach email draft for a given lead.

    Uses the LLM to compose a message based on the lead's profile and
    the specified tone. Does NOT send the email -- returns a draft for
    human review.

    Args:
        lead: The Lead ORM object containing contact info and context.
        tone: Desired tone for the email. One of 'professional', 'casual',
              or 'friendly'. Defaults to 'professional'.

    Returns:
        An OutreachDraft schema with subject, body, and metadata.

    Raises:
        LLMServiceError: If the AI completion call fails after retries.
        ValidationError: If the lead object is missing required fields.
    """
    ...
```

### 7.2 TypeScript/JSDoc Style

```typescript
/**
 * Fetches paginated leads from the backend API.
 *
 * @param page - The 1-indexed page number to retrieve.
 * @param filters - Optional filters to narrow the result set.
 * @returns A promise resolving to a paginated leads response.
 * @throws {ApiError} When the API returns a non-2xx status code.
 */
export async function fetchLeads(
  page: number,
  filters?: LeadFilters
): Promise<PaginatedResponse<Lead>> { ... }
```

### 7.3 Rules

- Module-level docstrings at the top of every Python file explain what the module does.
- React components must have a JSDoc comment describing props and purpose.
- Complex business logic (agent orchestration, vector search) must include inline
  comments explaining the *why*, not just the *what*.
- Keep docstrings up-to-date when changing behavior. An outdated docstring is worse
  than none.

---

## 8. Outreach / Email - Human-Approval-Before-Send

> **STOP -- No email, LinkedIn message, or any external outreach communication may
> be sent without explicit human approval.**

This is a non-negotiable safety policy. Violating it risks sending unsolicited or
incorrect messages to real contacts on behalf of users.

### 8.1 Required Workflow

```
[Agent drafts message]
        |
        v
[Draft stored in DB with status = "pending_review"]
        |
        v
[User notified in UI -> Approval Queue shown]
        |
        v
[Human reviews: edits / approves / rejects]
        |
        v
   [APPROVED?]
   /          \
 YES           NO
  |             |
  v             v
[Email        [Draft status = "rejected",
  sent]          archived, no send]
```

### 8.2 Implementation Requirements

1. **Draft-first, never auto-send.** All outreach generation functions MUST return a
   draft object -- never trigger delivery directly.

2. **`OutreachDraft` schema** must include:
   - `id` (UUID)
   - `lead_id`
   - `status`: `"pending_review"` | `"approved"` | `"rejected"` | `"sent"` | `"failed"`
   - `subject`, `body`
   - `created_by_agent` (agent name/version string)
   - `approved_by` (user ID -- null until approved)
   - `approved_at` (timestamp -- null until approved)
   - `sent_at` (timestamp -- null until sent)

3. **Separate `send` endpoint.** Sending MUST be a distinct API call
   (`POST /api/v1/outreach/{draft_id}/send`) that:
   - Verifies `status == "approved"` before proceeding.
   - Records `approved_by` and `approved_at` from the authenticated user session.
   - Sets `status = "sent"` and `sent_at` only after successful delivery confirmation.

4. **No background auto-send.** Celery tasks MUST NOT autonomously pick up and send
   drafts. They may only process sends already human-approved via the API.

5. **Audit log.** Every status transition on an `OutreachDraft` must be recorded in an
   `outreach_audit_log` table (`draft_id`, `old_status`, `new_status`, `changed_by`,
   `changed_at`).

### 8.3 Code Pattern

```python
# CORRECT -- agent returns draft, does not send
async def generate_outreach(lead_id: UUID) -> OutreachDraft:
    """
    Generate an outreach email draft for human review.

    IMPORTANT: This function does NOT send the email. It creates a draft
    with status='pending_review' that must be explicitly approved by a
    human operator before any delivery occurs.

    Args:
        lead_id: UUID of the lead to generate outreach for.

    Returns:
        An OutreachDraft instance with status='pending_review'.
    """
    draft = OutreachDraft(
        lead_id=lead_id,
        subject=await _generate_subject(lead_id),
        body=await _generate_body(lead_id),
        status="pending_review",
        created_by_agent="outreach-agent-v1",
    )
    await db.save(draft)
    logger.info(
        "Outreach draft created",
        extra={"draft_id": str(draft.id), "lead_id": str(lead_id)},
    )
    return draft


# WRONG -- never auto-send from an agent
async def generate_and_send_outreach(lead_id: UUID) -> None:
    draft = await build_draft(lead_id)
    await email_client.send(draft)  # FORBIDDEN without human approval
```

---

## 9. Database Conventions

### 9.1 PostgreSQL + pgvector

- All tables use **UUID primary keys** (`gen_random_uuid()`).
- Timestamps: always store as `TIMESTAMP WITH TIME ZONE` (UTC). Use `created_at` and
  `updated_at` on every table.
- Use **Alembic** for all schema migrations. Never alter production schema manually.
- Vector columns (embeddings) use pgvector's `Vector(1536)` type (adjust dimension to
  match the embedding model in use).
- Add an **HNSW or IVFFlat index** on vector columns for efficient similarity search.

### 9.2 SQLAlchemy Patterns

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column
import uuid
from datetime import datetime

class Lead(Base):
    """Represents a business development lead."""

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(nullable=False, index=True)
    contact_email: Mapped[str | None] = mapped_column(nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )
```

### 9.3 Query Safety

- Always use **parameterized queries** via SQLAlchemy. Never use string formatting to
  build SQL.
- Paginate all list endpoints. Default page size: 20. Max page size: 100.
- Never return all rows without a `LIMIT` clause.

---

## 10. API Design

- Follow **RESTful conventions**: `GET` for reads, `POST` for creates, `PUT`/`PATCH`
  for updates, `DELETE` for deletes.
- Always version routes: `/api/v1/...`
- Return consistent error envelopes:

```json
{
  "detail": "Human-readable error message",
  "code": "MACHINE_READABLE_CODE",
  "request_id": "uuid"
}
```

- Include `X-Request-ID` header propagation for traceability across services.
- Document every endpoint with FastAPI's `summary`, `description`, and `tags` params.

---

## 11. Testing

- **Backend**: Use `pytest` + `pytest-asyncio` + `httpx.AsyncClient`. Aim for >= 80%
  coverage on services and agents.
- **Frontend**: Use `Vitest` + `@testing-library/react`. Test all custom hooks and key
  UI flows.
- Write tests **before or alongside** new features, not after.
- Mock all external services (OpenAI, SendGrid) in tests -- never call live APIs in CI.
- Integration tests for outreach workflows MUST assert that no email is delivered
  without `status == "approved"`.

---

## 12. Git & PR Conventions

| Convention     | Rule                                                                     |
|----------------|--------------------------------------------------------------------------|
| Branch naming  | `feature/<ticket>-short-desc`, `fix/<ticket>-short-desc`, `chore/...`   |
| Commit style   | Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`       |
| PR size        | Prefer small, focused PRs (< 400 lines changed where possible)           |
| Reviews        | Require at least 1 human approval before merge                           |
| Secrets scan   | CI MUST run `trufflehog` or `gitleaks` on every PR to detect leaked keys |
| Main branch    | `main` is protected -- no direct pushes                                  |

---

## 13. Security Checklist

Before submitting any PR that touches auth, outreach, or data access:

- [ ] No secrets or credentials appear in diff
- [ ] All user inputs are validated via Pydantic / TypeScript types
- [ ] SQL queries use parameterized statements (no string interpolation)
- [ ] Outreach endpoints enforce `status == "approved"` gate
- [ ] JWTs are validated on every protected route
- [ ] Sensitive data is not logged (emails, tokens, phone numbers)
- [ ] CORS origins are explicitly allowlisted (not `*`) in production
- [ ] Rate limiting is applied to auth and outreach endpoints

---

## 14. Agent Interaction Policies

Rules specifically for AI coding agents (Copilot, Cursor, Antigravity, etc.):

1. **Read this file first.** Before generating or modifying any code in this repo,
   re-read `AGENTS.md` in full.
2. **Never generate hardcoded secrets.** If a placeholder is needed, use the variable
   name (e.g., `settings.openai_api_key`) and reference `.env.example`.
3. **Always add docstrings.** Any function or class you generate must include a
   complete docstring per Section 7.
4. **Always add logging.** Any service method, agent function, or background task you
   generate must include at least one `logger.info(...)` on success and
   `logger.exception(...)` in error paths.
5. **Respect the human-approval gate.** Never generate code that calls the
   email/outreach delivery function without first checking
   `draft.status == "approved"`.
6. **Use TypeScript, not JavaScript.** Frontend files must use `.ts` or `.tsx`
   extensions with strict types.
7. **Ask before introducing new dependencies.** If a task requires a new `pip` or
   `npm` package not already in `requirements.txt` / `package.json`, surface it as a
   suggestion and wait for approval.
8. **Prefer async patterns.** All backend I/O operations must be `async`. Do not
   introduce synchronous blocking calls inside FastAPI route handlers.
9. **Do not modify `.env`.** Agents must never read from, write to, or generate a
   populated `.env` file. Modify only `.env.example` when adding new variables.
10. **Flag unclear requirements.** If a task is ambiguous -- especially regarding data
    access or outreach sending -- stop and ask a clarifying question rather than
    making assumptions.

---

*Last updated: 2026-07-29 | Maintainer: Everen Engineering Team*