"""Disconnecting a provider: only the caller's own token is deleted, and the provider is asked to revoke it when it can."""

from datetime import timedelta

import httpx

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.crypto import encrypt_secret
from linkedin_agent.database import SessionLocal


def _store_token(email: str, provider: str, token: str = "provider-access-token") -> None:
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == email).one()
        db.add(
            models.OAuthToken(
                user_id=user.id,
                provider=provider,
                access_token=encrypt_secret(token),
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        db.commit()


def _rows(provider: str) -> list[str]:
    with SessionLocal() as db:
        return [row.user.email for row in db.query(models.OAuthToken).filter(models.OAuthToken.provider == provider)]


def test_disconnect_needs_a_login(client) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/oauth/linkedin").status_code in (401, 403)


def test_an_unknown_provider_is_refused(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/oauth/kaggle", headers=auth_headers).status_code == 422


def test_disconnecting_a_provider_that_is_not_connected_is_404(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/oauth/github", headers=auth_headers).status_code == 404


def test_linkedin_is_disconnected_locally_and_the_answer_says_it_cannot_be_revoked_here(
    client,
    auth_headers,
    mock_http,  # type: ignore[no-untyped-def]
) -> None:
    _store_token("alice@example.com", "linkedin")
    mock_http(lambda request: httpx.Response(500))  # must not be called for LinkedIn

    response = client.delete("/oauth/linkedin", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"provider": "linkedin", "status": "disconnected", "provider_revoked": "not_supported"}
    states = {
        item["provider"]: item["status"] for item in client.get("/oauth/connections", headers=auth_headers).json()
    }
    assert states["linkedin"] == "not_connected"


def test_github_grant_is_revoked_at_the_provider_with_the_app_credentials(
    client,
    auth_headers,
    mock_http,  # type: ignore[no-untyped-def]
) -> None:
    _store_token("alice@example.com", "github", token="gho_secret_value")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    mock_http(handler)
    response = client.delete("/oauth/github", headers=auth_headers)
    assert response.json()["provider_revoked"] == "revoked"
    assert seen[0].method == "DELETE"
    assert seen[0].url.path.endswith("/grant")
    assert b"gho_secret_value" in seen[0].content  # sent to the provider, never returned to the browser
    assert b"gho_secret_value" not in response.content


def test_a_failed_provider_revocation_still_disconnects_and_says_so(
    client,
    auth_headers,
    mock_http,  # type: ignore[no-untyped-def]
) -> None:
    _store_token("alice@example.com", "github")
    mock_http(lambda request: httpx.Response(502))

    response = client.delete("/oauth/github", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["provider_revoked"] == "failed"
    assert _rows("github") == []


def test_only_the_callers_own_token_is_deleted(client, auth_headers, mock_http) -> None:  # type: ignore[no-untyped-def]
    client.post("/auth/register", json={"email": "bob-oauth@example.com", "password": "bob-oauth-password"})
    _store_token("alice@example.com", "linkedin")
    _store_token("bob-oauth@example.com", "linkedin")

    response = client.delete("/oauth/linkedin", headers=auth_headers)
    assert response.status_code == 200
    assert _rows("linkedin") == ["bob-oauth@example.com"]
