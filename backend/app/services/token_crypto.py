"""Symmetric encryption for integration OAuth tokens.

Uses Fernet (AES-128-CBC + HMAC-SHA256). Key comes from `INTEGRATION_ENC_KEY`
env var and is required — we deliberately do NOT auto-generate it at startup,
because a silently-rotated key renders every stored token permanently
undecryptable. Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Encryption is lazy: importing this module does not require the key. Calling
`encrypt` or `decrypt` without it raises RuntimeError.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings


class IntegrationKeyMissing(RuntimeError):
    """Raised when an encryption op is requested without INTEGRATION_ENC_KEY set."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.integration_enc_key
    if not key:
        raise IntegrationKeyMissing(
            "INTEGRATION_ENC_KEY is not set. Generate a key with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and add it to your .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as e:
        raise IntegrationKeyMissing(f"INTEGRATION_ENC_KEY is invalid: {e}") from e


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise IntegrationKeyMissing(
            "Stored token cannot be decrypted with the current INTEGRATION_ENC_KEY. "
            "Has the key been rotated?"
        ) from e
