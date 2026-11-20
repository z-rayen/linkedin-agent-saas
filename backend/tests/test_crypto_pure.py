"""Unit tests for token encryption at rest. No database or network."""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from linkedin_agent.core.crypto import decrypt_secret, encrypt_secret
from linkedin_agent.core.settings import Settings


def test_encrypted_token_round_trips() -> None:
    token = "AQV-linkedin-access-token-example"
    stored = encrypt_secret(token)
    assert stored != token
    assert decrypt_secret(stored) == token


def test_encryption_is_not_deterministic() -> None:
    assert encrypt_secret("same") != encrypt_secret("same")


def test_plaintext_from_before_encryption_is_unreadable() -> None:
    assert decrypt_secret("AQV-old-plaintext-token") is None


def test_token_encrypted_with_another_key_is_unreadable() -> None:
    foreign = Fernet(Fernet.generate_key()).encrypt(b"secret").decode()
    assert decrypt_secret(foreign) is None


def test_settings_reject_a_key_that_is_not_fernet() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://x",
            secret_key="a-long-enough-secret-key",
            token_encryption_key="not-a-fernet-key",
        )
