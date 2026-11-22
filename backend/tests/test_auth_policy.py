"""Registration policy, email normalisation and the failed-login limit, through the API."""

from linkedin_agent.auth_utils import BCRYPT_MAX_BYTES
from linkedin_agent.core.settings import get_settings

NOT_AUTHENTICATED = (401, 403)


def _register(client: object, email: str, password: str):  # type: ignore[no-untyped-def]
    return client.post("/auth/register", json={"email": email, "password": password})  # type: ignore[attr-defined]


def test_the_password_policy_is_public_and_gives_the_limits(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/auth/password-policy")
    assert response.status_code == 200
    assert response.json() == {"min_length": get_settings().password_min_length, "max_bytes": BCRYPT_MAX_BYTES}


def test_a_password_below_the_minimum_is_refused_with_the_limit(client) -> None:  # type: ignore[no-untyped-def]
    minimum = get_settings().password_min_length
    response = _register(client, "short@example.com", "x" * (minimum - 1))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "password_too_short"
    assert body["min_length"] == minimum
    assert str(minimum) in body["detail"]


def test_a_password_at_the_minimum_is_accepted(client) -> None:  # type: ignore[no-untyped-def]
    minimum = get_settings().password_min_length
    assert _register(client, "exact@example.com", "x" * minimum).status_code == 200


def test_the_upper_limit_counts_bytes_not_characters(client) -> None:  # type: ignore[no-untyped-def]
    # 40 characters, 80 bytes in UTF-8: over bcrypt's 72-byte limit although the length rule is met.
    response = _register(client, "long@example.com", "é" * 40)
    assert response.status_code == 422
    assert response.json()["code"] == "password_too_long"
    assert response.json()["max_bytes"] == BCRYPT_MAX_BYTES


def test_email_case_and_spaces_cannot_create_a_duplicate_account(client) -> None:  # type: ignore[no-untyped-def]
    password = "c" * get_settings().password_min_length
    created = _register(client, "  Carol@Example.COM ", password)
    assert created.status_code == 200
    assert created.json()["email"] == "carol@example.com"
    assert _register(client, "carol@example.com", password).status_code == 400
    login = client.post("/auth/login", json={"email": "CAROL@example.com", "password": password})
    assert login.status_code == 200


def test_repeated_failed_logins_block_the_account_even_with_the_right_password(client) -> None:  # type: ignore[no-untyped-def]
    password = "d" * get_settings().password_min_length
    _register(client, "dan@example.com", password)
    limit = get_settings().login_max_failures
    for _ in range(limit):
        wrong = client.post("/auth/login", json={"email": "dan@example.com", "password": "not-the-password"})
        assert wrong.status_code == 401

    blocked = client.post("/auth/login", json={"email": "dan@example.com", "password": "not-the-password"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert blocked.json()["retry_after_seconds"] >= 1

    right = client.post("/auth/login", json={"email": "dan@example.com", "password": password})
    assert right.status_code == 429  # refused before the password is even checked


def test_the_protected_routes_still_need_a_login(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/applications/mail-mode").status_code in NOT_AUTHENTICATED
