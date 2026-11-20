"""GitHub OAuth callback, with the token exchange mocked. No network and no real GitHub account."""

from urllib.parse import parse_qs, urlparse

import httpx

from linkedin_agent import models
from linkedin_agent.core.crypto import decrypt_secret
from linkedin_agent.database import SessionLocal


def _state(client, headers) -> str:  # type: ignore[no-untyped-def]
    url = client.get("/oauth/github/authorize-url", headers=headers).json()["url"]
    return parse_qs(urlparse(url).query)["state"][0]


def test_callback_stores_encrypted_token_and_redirects(client, auth_headers, mock_http) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "github.com"
        return httpx.Response(200, json={"access_token": "gho_plain_test_token", "token_type": "bearer"})

    mock_http(handler)
    state = _state(client, auth_headers)

    response = client.get(
        "/oauth/github/callback", params={"code": "test-code", "state": state}, follow_redirects=False
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("/dashboard.html?github=connected")
    sent = seen[0]
    assert sent.headers["Accept"] == "application/json"
    assert b"test-code" in sent.content

    with SessionLocal() as db:
        row = db.query(models.OAuthToken).filter(models.OAuthToken.provider == "github").one()
        assert row.access_token != "gho_plain_test_token"  # encrypted at rest
        assert decrypt_secret(row.access_token) == "gho_plain_test_token"


def test_callback_without_access_token_is_refused(client, auth_headers, mock_http) -> None:  # type: ignore[no-untyped-def]
    mock_http(lambda request: httpx.Response(200, json={"error": "bad_verification_code"}))
    state = _state(client, auth_headers)

    response = client.get("/oauth/github/callback", params={"code": "stale-code", "state": state})

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub token exchange failed"
    with SessionLocal() as db:
        assert db.query(models.OAuthToken).filter(models.OAuthToken.provider == "github").count() == 0


def test_callback_rejects_a_state_that_was_not_issued(client, mock_http) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []
    mock_http(lambda request: calls.append(request) or httpx.Response(200, json={"access_token": "x"}))

    response = client.get("/oauth/github/callback", params={"code": "c", "state": "forged"})

    assert response.status_code == 400
    assert calls == []  # the token exchange never runs for a forged state
