"""PII blind-indexing: deterministic lookup hashes for encrypted columns.

:class:`app.db.types.EncryptedString` is non-deterministic by design (Fernet
mixes in a fresh IV per call), so an encrypted column can never back a unique
constraint or an equality ``WHERE`` clause directly. The standard fix is a
"blind index": store a deterministic HMAC-SHA256 of the normalized plaintext
alongside the encrypted value, and query/constrain on the hash instead.

HMAC (not a bare hash) is used so the index cannot be brute-forced offline
from a leaked column dump without also knowing ``settings.secret_key`` --
a bare SHA-256 of a small, guessable space like email addresses would be
reversible via a rainbow table in practice.

Standard library only (stdlib ``hmac``/``hashlib``), so this is testable
without a database.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings


def blind_index(value: str, *, purpose: str = "contact_email") -> str:
    """Compute a deterministic HMAC-SHA256 lookup hash for a PII value.

    The same normalized input always produces the same output (unlike the
    corresponding ``EncryptedString`` ciphertext), so this is what equality
    lookups and unique constraints are built against.

    Args:
        value: The plaintext value (e.g. a lowercased, trimmed email address).
        purpose: A namespace string mixed into the HMAC so a hash computed for
            one column/purpose can never collide with or be replayed against
            another (e.g. an email blind index vs. a phone blind index).

    Returns:
        A 64-character hex digest.

    Raises:
        ValueError: If ``value`` is blank.
    """
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("value must not be blank")

    message = f"{purpose}:{normalized}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def email_blind_index(email: str) -> str:
    """Blind index for an email address, normalized by lowercasing/trimming.

    Args:
        email: The email address.

    Returns:
        The deterministic lookup hash.
    """
    return blind_index(email, purpose="contact_email")
