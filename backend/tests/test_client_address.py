"""The client address behind a proxy: X-Forwarded-For is believed only from a trusted proxy, right to left."""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from linkedin_agent.core.settings import Settings
from linkedin_agent.services.client_address import UNKNOWN_ADDRESS, client_address

TRUSTED = "10.0.0.0/8, 127.0.0.1"


def test_without_a_trusted_peer_the_header_is_ignored() -> None:
    assert client_address("203.0.113.9", "198.51.100.1", TRUSTED) == "203.0.113.9"


def test_with_no_trusted_proxies_configured_the_peer_is_always_used() -> None:
    assert client_address("10.0.0.2", "198.51.100.1", "") == "10.0.0.2"


def test_a_trusted_proxy_passes_the_real_client() -> None:
    assert client_address("10.0.0.2", "198.51.100.1", TRUSTED) == "198.51.100.1"


def test_entries_written_by_the_client_on_the_left_are_never_used() -> None:
    # The client sent "1.2.3.4"; nginx appended the real address on the right.
    assert client_address("127.0.0.1", "1.2.3.4, 198.51.100.1", TRUSTED) == "198.51.100.1"


def test_a_chain_of_trusted_proxies_is_walked_from_the_right() -> None:
    assert client_address("10.0.0.2", "198.51.100.1, 10.0.0.5", TRUSTED) == "198.51.100.1"


def test_no_peer_is_reported_as_unknown() -> None:
    assert client_address(None, None, TRUSTED) == UNKNOWN_ADDRESS


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://unused/unused_test",
        "secret_key": "a-long-enough-secret-key",
        "token_encryption_key": Fernet.generate_key().decode(),
        "groq_api_key": "test-key",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def test_a_mistyped_trusted_proxy_fails_at_startup() -> None:
    with pytest.raises(ValidationError):
        _settings(trusted_proxies="10.0.0.0/8, not-an-address")
