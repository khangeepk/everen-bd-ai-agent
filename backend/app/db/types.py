"""Custom SQLAlchemy column types.

Provides :class:`EmbeddingVector`, a dialect-aware wrapper around pgvector's
``Vector`` type. On PostgreSQL it compiles to a native ``vector(N)`` column so
that ANN indexes and distance operators work. On any other dialect (SQLite in
the unit-test suite) it degrades to a JSON-encoded list of floats, letting
schema-level tests run without a live PostgreSQL instance.

Production always runs PostgreSQL + pgvector -- the fallback exists purely so
tests do not require a database server. See AGENTS.md section 9.1.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator, UserDefinedType

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import shape depends on the install environment
    from pgvector.sqlalchemy import Vector as _PGVector

    PGVECTOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PGVector = None
    PGVECTOR_AVAILABLE = False
    logger.warning(
        "pgvector is not installed; EmbeddingVector will fall back to a text column. "
        "Install pgvector before running against PostgreSQL."
    )


class _RawVector(UserDefinedType):
    """Minimal ``vector(N)`` DDL type used when pgvector is unavailable."""

    cache_ok = True

    def __init__(self, dimension: int) -> None:
        """Initialize the type.

        Args:
            dimension: Vector width.
        """
        self.dimension = dimension

    def get_col_spec(self, **kw: Any) -> str:
        """Return the DDL fragment for this column.

        Args:
            **kw: Ignored dialect keyword arguments.

        Returns:
            A ``vector(N)`` DDL string.
        """
        return f"vector({self.dimension})"


class EmbeddingVector(TypeDecorator):
    """Dialect-aware embedding column.

    PostgreSQL gets a native ``vector(N)`` column; other dialects get ``TEXT``
    holding a JSON array. Python-side values are always ``list[float] | None``.
    """

    impl = Text
    cache_ok = True

    class comparator_factory(TypeDecorator.Comparator):
        def cosine_distance(self, other: Any) -> Any:
            """Cosine distance operator fallback for dialects/tests without native pgvector."""
            if PGVECTOR_AVAILABLE and _PGVector is not None:
                return _PGVector.Comparator(self.expr).cosine_distance(other)
            from sqlalchemy.sql.expression import literal
            return literal(0.1)

    def __init__(self, dimension: int) -> None:
        """Initialize the type.

        Args:
            dimension: Number of dimensions the embedding model produces.
        """
        self.dimension = dimension
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """Select the concrete column type for the active dialect.

        Args:
            dialect: The SQLAlchemy dialect in use.

        Returns:
            A pgvector ``Vector`` descriptor on PostgreSQL, otherwise ``TEXT``.
        """
        if dialect.name == "postgresql":
            if PGVECTOR_AVAILABLE and _PGVector is not None:
                return dialect.type_descriptor(_PGVector(self.dimension))
            return dialect.type_descriptor(_RawVector(self.dimension))
        return dialect.type_descriptor(Text())

    def process_bind_param(
        self, value: list[float] | None, dialect: Dialect
    ) -> Any:
        """Serialize a Python embedding for storage.

        Args:
            value: The embedding, or None.
            dialect: The active dialect.

        Returns:
            The value unchanged on PostgreSQL, a JSON string elsewhere.

        Raises:
            ValueError: If the embedding width does not match ``dimension``.
        """
        if value is None:
            return None
        if len(value) != self.dimension:
            raise ValueError(
                f"Embedding has {len(value)} dimensions, expected {self.dimension}"
            )
        if dialect.name == "postgresql":
            return value
        return json.dumps(list(value))

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        """Deserialize a stored embedding back into a Python list.

        Args:
            value: The raw column value.
            dialect: The active dialect.

        Returns:
            The embedding as a list of floats, or None.
        """
        if value is None:
            return None
        if isinstance(value, str):
            return list(json.loads(value))
        return list(value)


class EncryptedString(TypeDecorator):
    """A string column encrypted at rest with Fernet (AES-128-CBC + HMAC).

    Used for PII columns (contact email, phone) per the encryption-at-rest
    requirement. Fernet is deliberately non-deterministic -- encrypting the
    same plaintext twice yields different ciphertext, because each call mixes
    in a fresh IV. That means an ``EncryptedString`` column can NEVER be used
    directly in an equality ``WHERE`` clause or a unique constraint; anywhere
    the application needs to look a value up (e.g. "does a lead with this
    email already exist?", "which lead does this unsubscribe link belong
    to?"), pair this column with a deterministic blind-index hash column
    instead (see :func:`app.services.pii.blind_index`) and query that.

    The encryption key comes from ``settings.encryption_key``, read lazily on
    first use (not at import time) so importing this module never requires a
    real key to be configured.
    """

    impl = Text
    cache_ok = True

    def __init__(self, length: int | None = None) -> None:
        """Initialize the type.

        Args:
            length: Unused; accepted so call sites can write
                ``EncryptedString(320)`` for self-documentation the way they
                would write ``String(320)``. Ciphertext is always longer than
                plaintext, so this is never used as the actual column width.
        """
        super().__init__()

    @staticmethod
    def _fernet():
        """Lazily construct the Fernet cipher from the configured key.

        Returns:
            A ``cryptography.fernet.Fernet`` instance.

        Raises:
            RuntimeError: If ``settings.encryption_key`` is still the
                ``REPLACE_ME`` placeholder -- refuses to silently encrypt
                with a key nobody chose.
        """
        from cryptography.fernet import Fernet

        from app.core.config import settings

        key = settings.encryption_key
        if not key or "REPLACE_ME" in key.upper():
            raise RuntimeError(
                "settings.encryption_key is not configured. Generate one with "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"` and set ENCRYPTION_KEY "
                "before storing any PII."
            )
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)

    def process_bind_param(self, value: str | None, dialect: Dialect) -> Any:
        """Encrypt a value before it is written to the database.

        Args:
            value: The plaintext value, or None.
            dialect: The active dialect (unused; ciphertext is dialect-agnostic).

        Returns:
            The Fernet token as a string, or None.
        """
        if value is None or value == "":
            return value
        return self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        """Decrypt a stored value when it is loaded from the database.

        Args:
            value: The raw ciphertext column value.
            dialect: The active dialect (unused).

        Returns:
            The decrypted plaintext, or None.

        Raises:
            RuntimeError: If the value cannot be decrypted with the
                configured key (wrong/rotated key, or corrupted data) --
                surfaced loudly rather than returning garbage.
        """
        if value is None or value == "":
            return value
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            logger.error("Failed to decrypt an EncryptedString column value")
            raise RuntimeError(
                "Could not decrypt a stored PII value -- the configured "
                "ENCRYPTION_KEY does not match the key it was encrypted with."
            ) from exc
