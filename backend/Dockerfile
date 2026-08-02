# syntax=docker/dockerfile:1
#
# Multi-stage build for the Everen BD Agent FastAPI backend.
#
# Stage 1 ("builder") compiles wheels for all dependencies, including ones
# that need build tooling (e.g. asyncpg, cryptography). Stage 2 ("runtime")
# copies only the built wheels into a slim image and installs them without a
# compiler toolchain present, keeping the final image small and reducing the
# attack surface (AGENTS.md security posture: least tooling in the runtime
# image). The app runs as a non-root user throughout.

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Build tooling needed only to compile wheels (not present in the final image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# libpq5 is the runtime (not -dev) Postgres client library asyncpg needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

COPY --from=builder /build/wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

COPY --chown=app:app . .

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

# Gunicorn manages a pool of uvicorn workers -- more resilient in production
# than running uvicorn standalone (auto-restarts a worker that dies, and
# `--graceful-timeout` lets in-flight requests finish on redeploy).
# Worker count intentionally low/fixed rather than CPU-derived: this is a
# low-QPS internal BD tool, not a high-throughput public API, and Render's
# cheapest instances are 0.5-1 vCPU, so a CPU-count formula would either
# under- or over-provision depending on plan.
# Run DB migrations, then hand off (exec) to gunicorn so it becomes PID 1 and
# receives signals correctly. Migrating at startup keeps the schema in sync on
# every tier, including Render's free plan (which does not run preDeployCommand).
# The container is single-instance, so `alembic upgrade head` runs once before
# any worker serves traffic.
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:8000 --timeout 60 --graceful-timeout 30 --access-logfile - --error-logfile -"]
