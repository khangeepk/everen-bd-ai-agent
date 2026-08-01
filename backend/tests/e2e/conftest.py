"""Shared fixtures for the end-to-end API test suite.

Builds a real ``httpx.AsyncClient`` against ``app.main.app`` backed by a
function-scoped, single-connection in-memory SQLite database (StaticPool, so
every session created by the overridden ``get_db`` dependency sees the same
data). Authentication is bypassed by overriding ``get_current_user`` with a
fixture-selectable role, rather than a real JWT -- see AGENTS.md section 4.2's
"no code path may be untestable without live credentials" expectation.

Two things every e2e test needs are provided as autouse fixtures so individual
test modules do not have to remember them:

* ``_valid_outreach_sender`` -- patches the CAN-SPAM sender settings away from
  their ``REPLACE_ME`` placeholders, since :mod:`app.services.canspam` rejects
  those by design. One test in ``test_outreach_approval_e2e.py`` explicitly
  restores the placeholder to verify that rejection still fires.
* ``_fake_embeddings`` -- monkeypatches ``OpenAIEmbeddingClient.embed`` at the
  class level with a deterministic bag-of-characters vector generator (the
  same approach as ``tests/conftest.py``'s ``FakeEmbeddingClient``), since
  several routes construct the real client inline rather than through an
  overridable dependency. Without this, any route touching the knowledge base
  would raise ``EmbeddingError`` (openai is not installed here) instead of
  exercising its actual logic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401 - registers models on Base.metadata
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.main import app
from app.services import embeddings as embeddings_module


def _deterministic_vector(text: str, dimension: int | None = None) -> list[float]:
    """Build a deterministic bag-of-characters vector for a string.

    Mirrors ``tests/conftest.py``'s ``FakeEmbeddingClient`` so semantically
    similar strings land near each other without any network call.

    Args:
        text: The input string.
        dimension: Width of the generated vector.

    Returns:
        A vector of the requested width.
    """
    from app.core.config import settings

    dim = dimension or settings.embedding_dimension
    vector = [0.0] * dim
    for char in text.lower():
        if char.isalnum():
            vector[ord(char) % dim] += 1.0
    return vector


@pytest_asyncio.fixture
async def e2e_engine() -> AsyncIterator[object]:
    """A fresh in-memory SQLite engine, shared across every connection.

    ``StaticPool`` is required here (unlike ``tests/conftest.py``'s
    single-session ``db_session`` fixture): the API's overridden ``get_db``
    opens a new session per request, and a plain in-memory SQLite database is
    otherwise per-connection, which would make every request see an empty
    database.

    Yields:
        The configured async engine, with all tables created.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def e2e_session_factory(e2e_engine: object) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to the shared e2e engine.

    Args:
        e2e_engine: The shared in-memory engine.

    Returns:
        A configured session factory.
    """
    return async_sessionmaker(bind=e2e_engine, class_=AsyncSession, expire_on_commit=False)


def _make_current_user_override(
    session_factory: async_sessionmaker[AsyncSession], role: UserRole
):
    """Build a ``get_current_user`` override that persists a fixed test user.

    Mirrors the real dependency's just-in-time provisioning so foreign keys
    (``approved_by_id``, ``requested_by_id``, ``executed_by_id``, ...)
    resolve against a real row, without going through JWT verification.

    Args:
        session_factory: Factory bound to the test engine.
        role: Role to provision the caller with.

    Returns:
        An async dependency function usable with ``app.dependency_overrides``.
    """
    from sqlalchemy import select

    subject = f"e2e-test-{role.value}"

    async def _override() -> User:
        async with session_factory() as db:
            result = await db.execute(select(User).where(User.provider_subject == subject))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(
                    provider_subject=subject,
                    email=f"{role.value}@e2e.test",
                    full_name=f"E2E {role.value}",
                    role=role,
                    is_active=True,
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
            return user

    return _override


@pytest.fixture(autouse=True)
def _valid_outreach_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the CAN-SPAM placeholder settings with valid values.

    ``Settings`` defaults ``outreach_physical_address`` and
    ``outreach_public_base_url`` to ``REPLACE_ME`` placeholders that
    :mod:`app.services.canspam` deliberately rejects. Draft generation would
    422 on every test unless this is patched. The one test that needs the
    rejection path back (``test_email_draft_blocked_by_missing_canspam_config``)
    reverts it locally.

    Args:
        monkeypatch: Pytest's monkeypatch fixture.
    """
    monkeypatch.setattr(
        settings, "outreach_physical_address", "12 Test Street, Austin, TX 78701, USA"
    )
    monkeypatch.setattr(settings, "outreach_public_base_url", "https://e2e-test.example")
    monkeypatch.setattr(settings, "secret_key", "e2e-test-signing-secret")
    monkeypatch.setattr(settings, "outreach_from_email", "bd@e2e-test.example")
    monkeypatch.setattr(settings, "outreach_from_name", "Everen Techno")
    monkeypatch.setattr(settings, "outreach_company_name", "Everen Techno")


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the OpenAI embedding client to avoid any network call.

    Several routes build ``KnowledgeBaseService(embedder=OpenAIEmbeddingClient())``
    inline rather than through an overridable FastAPI dependency, so the class
    method itself is patched rather than a dependency override.

    Args:
        monkeypatch: Pytest's monkeypatch fixture.
    """

    async def _fake_embed(self: object, texts: Sequence[str]) -> list[list[float]]:
        return [_deterministic_vector(text) for text in texts]

    monkeypatch.setattr(embeddings_module.OpenAIEmbeddingClient, "embed", _fake_embed)


@pytest_asyncio.fixture
async def e2e_client(
    e2e_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An ``httpx.AsyncClient`` against the real app, as an approver.

    ``get_current_user`` is overridden to a persisted SALES user (an approver
    role), which is what most of the outreach approval flow needs; tests that
    specifically need a non-approver override ``get_current_user`` again on
    the same app object via the ``as_role`` helper below.

    Args:
        e2e_session_factory: Factory bound to the test engine.

    Yields:
        A configured async client. Dependency overrides are cleared on exit.
    """
    app.dependency_overrides[get_db] = _db_override_factory(e2e_session_factory)
    app.dependency_overrides[get_current_user] = _make_current_user_override(
        e2e_session_factory, UserRole.SALES
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        yield client

    app.dependency_overrides.clear()


def _db_override_factory(session_factory: async_sessionmaker[AsyncSession]):
    """Build a ``get_db`` override bound to the test session factory.

    Mirrors the real dependency's commit/rollback/close semantics.

    Args:
        session_factory: Factory bound to the test engine.

    Returns:
        An async generator function usable with ``app.dependency_overrides``.
    """

    async def _override() -> AsyncIterator[AsyncSession]:
        session = session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    return _override


def set_caller_role(role: UserRole, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Swap the authenticated caller's role mid-test.

    Args:
        role: The role the next requests should be made as.
        session_factory: Factory bound to the test engine (same one the
            client fixture was built with).
    """
    app.dependency_overrides[get_current_user] = _make_current_user_override(
        session_factory, role
    )


@pytest.fixture
def unique_suffix() -> str:
    """A short unique string for building non-colliding test emails/URLs.

    Returns:
        An 8-character hex fragment.
    """
    return uuid.uuid4().hex[:8]
