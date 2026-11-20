"""LinkedIn publishing: connection problems and API failures, with the LinkedIn API mocked."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.crypto import encrypt_secret
from linkedin_agent.database import SessionLocal

USERINFO = "https://api.linkedin.com/v2/userinfo"
UGC = "https://api.linkedin.com/v2/ugcPosts"


def _create_post(client: TestClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("linkedin_agent.routers.content.generate_post", AsyncMock(return_value="Hello LinkedIn"))
    return client.post("/content/generate", json={"title": "t", "context": "c"}, headers=headers).json()["id"]


def _store_linkedin_token(*, plaintext: str | None = None, expires_in: timedelta | None = None) -> None:
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == "alice@example.com").one()
        stored = plaintext if plaintext is not None else encrypt_secret("linkedin-access-token")
        db.add(
            models.OAuthToken(
                user_id=user.id,
                provider="linkedin",
                access_token=stored,
                expires_at=(utcnow() + expires_in) if expires_in is not None else None,
            )
        )
        db.commit()


def _queue_status(client: TestClient, headers: dict[str, str]) -> str:
    return client.get("/content/queue", headers=headers).json()[0]["status"]


def test_publish_refused_when_linkedin_is_not_connected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = _create_post(client, auth_headers, monkeypatch)
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "LinkedIn not connected"


def test_publish_refused_when_token_has_expired(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = _create_post(client, auth_headers, monkeypatch)
    _store_linkedin_token(expires_in=timedelta(minutes=-5))
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400
    assert "expired" in r.json()["detail"]


def test_publish_refused_when_stored_token_is_unreadable(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A token stored in clear before encryption existed cannot be decrypted: the user must reconnect.
    post_id = _create_post(client, auth_headers, monkeypatch)
    _store_linkedin_token(plaintext="AQV-old-plaintext-token")
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400
    assert "unreadable" in r.json()["detail"]


def test_publish_fails_cleanly_when_userinfo_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, mock_http
) -> None:
    post_id = _create_post(client, auth_headers, monkeypatch)
    _store_linkedin_token(expires_in=timedelta(days=30))
    mock_http(lambda request: httpx.Response(401, json={"message": "invalid token"}))
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "Failed to fetch LinkedIn user info"
    assert _queue_status(client, auth_headers) == "draft"  # nothing was published, nothing marked as published


def test_publish_api_error_marks_the_post_and_returns_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, mock_http
) -> None:
    post_id = _create_post(client, auth_headers, monkeypatch)
    _store_linkedin_token(expires_in=timedelta(days=30))

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == USERINFO:
            return httpx.Response(200, json={"sub": "member-123"})
        return httpx.Response(422, json={"message": "content rejected"})

    mock_http(handler)
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 502
    assert "LinkedIn publish failed" in r.json()["detail"]
    assert _queue_status(client, auth_headers) == "error"


def test_successful_publish_uses_the_decrypted_token_and_the_member_urn(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, mock_http
) -> None:
    post_id = _create_post(client, auth_headers, monkeypatch)
    _store_linkedin_token(expires_in=timedelta(days=30))
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == USERINFO:
            return httpx.Response(200, json={"sub": "member-123"})
        seen["authorization"] = request.headers["authorization"]
        seen["author"] = json.loads(request.content)["author"]
        return httpx.Response(201, json={"id": "urn:li:share:1"})

    mock_http(handler)
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"status": "published"}
    assert seen["authorization"] == "Bearer linkedin-access-token"
    assert seen["author"] == "urn:li:person:member-123"
    assert _queue_status(client, auth_headers) == "published"
