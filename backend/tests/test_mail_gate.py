"""Safety gate: turning dry-run off alone must never allow sending to real recipients.

Real sending needs MAIL_REDIRECT_TO (every message goes to a test address) or the explicit
MAIL_ALLOW_REAL_RECIPIENTS=true. Otherwise the settings refuse to load, so the app does not start.
"""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from linkedin_agent.core.settings import Settings

BASE = {
    "database_url": "postgresql://unused/unused_test",
    "secret_key": "a-long-enough-secret-key",
}


def _settings(**overrides: object) -> Settings:
    return Settings(token_encryption_key=Fernet.generate_key().decode(), **BASE, **overrides)  # type: ignore[arg-type]


def test_dry_run_off_alone_is_refused() -> None:
    with pytest.raises(ValidationError, match="MAIL_REDIRECT_TO"):
        _settings(mail_dry_run=False)


def test_dry_run_off_with_a_redirect_is_allowed() -> None:
    settings = _settings(mail_dry_run=False, mail_redirect_to="me@example.com")
    assert settings.mail_redirect_to == "me@example.com"


def test_dry_run_off_with_an_explicit_opt_in_is_allowed() -> None:
    assert _settings(mail_dry_run=False, mail_allow_real_recipients=True).mail_allow_real_recipients is True


def test_whitespace_only_redirect_does_not_count() -> None:
    with pytest.raises(ValidationError):
        _settings(mail_dry_run=False, mail_redirect_to="   ")


def test_dry_run_is_the_default_and_needs_no_target() -> None:
    settings = _settings()
    assert settings.mail_dry_run is True
    assert settings.mail_allow_real_recipients is False


def test_the_environment_cannot_bypass_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set explicitly (not deleted): a deleted variable would fall back to the developer's .env file.
    monkeypatch.setenv("MAIL_DRY_RUN", "false")
    monkeypatch.setenv("MAIL_REDIRECT_TO", "")
    monkeypatch.setenv("MAIL_ALLOW_REAL_RECIPIENTS", "false")
    with pytest.raises(ValidationError):
        Settings(token_encryption_key=Fernet.generate_key().decode(), **BASE)  # type: ignore[arg-type]
