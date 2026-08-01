"""End-to-end API tests: full HTTP-request-to-database round trips.

Distinct from the unit-level tests in ``tests/``, which exercise one service
or agent in isolation. These tests drive the real FastAPI app (``app.main.app``)
through ``httpx.AsyncClient`` against an in-memory SQLite database, so a whole
route -- request parsing, auth/RBAC dependency, service calls, ORM writes,
response serialization -- is verified together for the four flows AGENTS.md
treats as most safety-critical: lead discovery, the audit engine, lead
scoring, and the outreach approval/send gate.

No network calls: Places, PageSpeed, SSL/contact-form checks, and the email
sender are always faked or monkeypatched (AGENTS.md section 11). The OpenAI
SDK is not installed in this environment, so any code path that imports it
(draft/report generation, embeddings) either takes its documented deterministic
fallback or has its embedding client monkeypatched -- see conftest.py.
"""
