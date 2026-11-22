"""OAuth state: every way a callback can carry a state it must not accept, plus the one valid path.

External token exchanges are answered by a mocked transport; nothing reaches LinkedIn or GitHub.
"""

import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from jose import jwt

from linkedin_agent import models
from linkedin_agent.auth_utils import OAUTH_STATE_PURPOSE
from linkedin_agent.clock import utcnow
from linkedin_agent.core.crypto import decrypt_secret
from linkedin_agent.core.settings import get_settings
from linkedin_agent.database import SessionLocal

INVALID = {"detail": "Invalid or expired OAuth state"}


def _state_from_authorize_url(client: TestClient, headers: dict[str, str], provider: str = "linkedin") -> str:
    url = client.get(f"/oauth/{provider}/authorize-url", headers=headers).json()["url"]
    return parse_qs(urlparse(url).query)["state"][0]


def _user_id(email: str) -> int:
    with SessionLocal() as db:
        return db.query(models.User).filter(models.User.email == email).one().id


def _signed(claims: dict[str, object]) -> str:
    settings = get_settings()
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def _valid_claims(user_id: int, provider: str = "linkedin") -> dict[str, object]:
    now = utcnow()
    return {
        "sub": str(user_id),
        "purpose": OAUTH_STATE_PURPOSE,
        "provider": provider,
        "nonce": "n",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }


def test_missing_state_is_rejected_by_validation(client: TestClient) -> None:
    r = client.get("/oauth/linkedin/callback?code=abc")
    assert r.status_code == 422


def test_tampered_state_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    state = _state_from_authorize_url(client, auth_headers)
    header, payload, signature = state.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={header}.{payload}.{flipped}", follow_redirects=False)
    assert r.status_code == 400
    assert r.json() == INVALID


def test_state_with_a_forged_user_claim_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Someone who edits the payload (sub = another user) cannot re-sign it without the server key.
    state = _state_from_authorize_url(client, auth_headers)
    header, payload_b64, signature = state.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = "999"
    forged_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={header}.{forged_payload}.{signature}")
    assert r.status_code == 400


def test_expired_state_is_rejected(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "exp@example.com", "password": "pw-1234567890"})
    expired = _valid_claims(_user_id("exp@example.com"))
    expired["iat"] = utcnow() - timedelta(hours=1)
    expired["exp"] = utcnow() - timedelta(minutes=30)
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={_signed(expired)}", follow_redirects=False)
    assert r.status_code == 400
    assert r.json() == INVALID


def test_state_for_a_user_that_does_not_exist_is_rejected(client: TestClient) -> None:
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={_signed(_valid_claims(999_999))}", follow_redirects=False)
    assert r.status_code == 400
    assert r.json() == INVALID


def test_github_state_cannot_be_used_on_the_linkedin_callback(client: TestClient, auth_headers: dict[str, str]) -> None:
    github_state = _state_from_authorize_url(client, auth_headers, provider="github")
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={github_state}", follow_redirects=False)
    assert r.status_code == 400


def test_linkedin_state_cannot_be_used_on_the_github_callback(client: TestClient, auth_headers: dict[str, str]) -> None:
    linkedin_state = _state_from_authorize_url(client, auth_headers, provider="linkedin")
    r = client.get(f"/oauth/github/callback?code=abc&state={linkedin_state}", follow_redirects=False)
    assert r.status_code == 400


def test_state_issued_to_one_user_connects_only_that_user(
    client: TestClient, auth_headers: dict[str, str], mock_http
) -> None:
    """Alice's state, replayed by anyone, can only ever attach a token to Alice's account."""
    client.post("/auth/register", json={"email": "bob@example.com", "password": "bob-pass-123"})
    alice_state = _state_from_authorize_url(client, auth_headers)

    mock_http(lambda request: httpx.Response(200, json={"access_token": "linkedin-token", "expires_in": 3600}))
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={alice_state}", follow_redirects=False)
    assert r.status_code == 307  # redirect to the dashboard

    with SessionLocal() as db:
        alice_row = db.query(models.OAuthToken).filter_by(user_id=_user_id("alice@example.com")).one()
        assert decrypt_secret(alice_row.access_token) == "linkedin-token"
        assert db.query(models.OAuthToken).filter_by(user_id=_user_id("bob@example.com")).count() == 0


def test_valid_callback_stores_the_token_encrypted(client: TestClient, auth_headers: dict[str, str], mock_http) -> None:
    state = _state_from_authorize_url(client, auth_headers)
    mock_http(lambda request: httpx.Response(200, json={"access_token": "plain-value", "expires_in": 60}))
    client.get(f"/oauth/linkedin/callback?code=abc&state={state}", follow_redirects=False)

    with SessionLocal() as db:
        row = db.query(models.OAuthToken).filter_by(user_id=_user_id("alice@example.com")).one()
        assert row.access_token != "plain-value"  # never stored in clear
        assert decrypt_secret(row.access_token) == "plain-value"
        assert row.expires_at is not None
