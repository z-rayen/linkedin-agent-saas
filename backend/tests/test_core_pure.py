"""Unit tests for the core package: settings, exceptions and log redaction. No database needed."""

import pytest
from pydantic import ValidationError

from linkedin_agent.core.exceptions import (
    AppError,
    AuthenticationError,
    ExternalServiceError,
    NotFoundError,
)
from linkedin_agent.core.logging import redact
from linkedin_agent.core.settings import Settings


def test_redact_hides_bearer_tokens() -> None:
    assert redact("Authorization: Bearer abc.def-ghi") == "Authorization: Bearer ***"


def test_redact_hides_key_value_secrets() -> None:
    assert redact("api_key=sk-12345 user=alice") == "api_key=*** user=alice"
    assert "hunter2" not in redact("password: hunter2")


def test_redact_leaves_ordinary_text_alone() -> None:
    message = "groq request failed status=429 error=rate_limit_exceeded"
    assert redact(message) == message


def test_settings_reject_short_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x", secret_key="too-short")


def test_settings_accept_valid_secret_key() -> None:
    settings = Settings(database_url="postgresql://x", secret_key="a-long-enough-secret-key")
    assert settings.jwt_algorithm == "HS256"
    assert settings.groq_model == "openai/gpt-oss-20b"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NotFoundError("missing"), 404, "not_found"),
        (AuthenticationError("nope"), 401, "authentication_failed"),
        (ExternalServiceError("upstream"), 502, "external_service_error"),
    ],
)
def test_domain_errors_carry_status_and_code(error: AppError, status: int, code: str) -> None:
    assert error.status_code == status
    assert error.code == code
    assert error.message in str(error)
