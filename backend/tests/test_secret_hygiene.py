"""Secret hygiene: a test process must not be able to see a real credential.

The test fails, and names the variable, if anything that looks like a real key is visible to the test
process. It never prints the value.
"""

import os
import re
from pathlib import Path

import pytest

from linkedin_agent.core.settings import Settings, get_settings

# Shapes of real credentials from the providers this project uses, and generic private key material.
REAL_LOOKING = [
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI-style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
SECRET_VARIABLES = (
    "GROQ_API_KEY",
    "SERPER_API_KEY",
    "SMTP_PASSWORD",
    "SMTP_USERNAME",
    "LINKEDIN_CLIENT_SECRET",
    "GITHUB_CLIENT_SECRET",
)


def test_the_settings_never_read_an_env_file() -> None:
    assert Settings.model_config.get("env_file") is None


def test_an_env_file_in_the_working_directory_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_THIS_WOULD_BE_A_REAL_KEY_1234567890\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from cryptography.fernet import Fernet

    settings = Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    assert settings.groq_api_key != "gsk_THIS_WOULD_BE_A_REAL_KEY_1234567890"
    assert settings.groq_api_key.startswith("test-fake-") or settings.groq_api_key == ""


@pytest.mark.parametrize("name", SECRET_VARIABLES)
def test_credentials_visible_to_tests_are_fake(name: str) -> None:
    value = os.environ.get(name, "")
    assert value == "" or value.startswith("test-fake-"), f"{name} holds a value that is not a test fake"


def test_no_real_looking_secret_is_visible_to_the_test_process() -> None:
    leaks: list[str] = []
    for name, value in os.environ.items():
        if any(pattern.search(value) for pattern in REAL_LOOKING):
            leaks.append(name)
    assert not leaks, f"real-looking credential visible in environment variable(s): {', '.join(sorted(leaks))}"


def test_loaded_settings_hold_no_real_looking_secret() -> None:
    settings = get_settings()
    for name in ("groq_api_key", "serper_api_key", "linkedin_client_secret", "github_client_secret"):
        value = getattr(settings, name)
        assert not any(pattern.search(str(value)) for pattern in REAL_LOOKING), name
