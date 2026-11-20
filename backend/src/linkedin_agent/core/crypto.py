"""Encryption at rest for third-party OAuth tokens (Fernet: AES-128-CBC with HMAC-SHA256)."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .settings import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    """Return a URL-safe token that can be stored as text."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    """Return the plaintext, or None if it cannot be decrypted (wrong key, or stored before encryption)."""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
