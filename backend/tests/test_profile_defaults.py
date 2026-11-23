"""Profile defaults: a new account, a row with empty base fields, and blank default settings must never produce a 500."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.core.settings import Settings
from linkedin_agent.database import SessionLocal

PASSWORD = "s3cret-passphrase-2"


def _register_and_login(client: TestClient, email: str) -> tuple[int, dict[str, str]]:
    created = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert created.status_code == 200, created.text
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return int(created.json()["id"]), {"Authorization": f"Bearer {token}"}


def test_new_account_gets_profile_defaults(client: TestClient) -> None:
    _, headers = _register_and_login(client, "newcomer@example.com")

    response = client.get("/profile", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["field"]
    assert body["skills"]
    assert body["level"]
    assert body["min_match_score"] == 70


def test_new_account_can_save_then_read_back_the_profile(client: TestClient) -> None:
    _, headers = _register_and_login(client, "saver@example.com")
    payload = {
        "field": "NLP",
        "skills": "Python, RAG",
        "level": "Junior",
        "min_match_score": 80,
        "job_feed_url": None,
        "cert_feed_url": None,
    }

    saved = client.post("/profile", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["field"] == "NLP"

    read = client.get("/profile", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["skills"] == "Python, RAG"
    assert read.json()["min_match_score"] == 80


def test_row_with_null_base_fields_reads_defaults_not_a_server_error(client: TestClient) -> None:
    # A row created by another path (for example a master CV saved before any profile) has only the user id.
    user_id, headers = _register_and_login(client, "bare-row@example.com")
    with SessionLocal() as db:
        db.add(models.ProfileConfig(user_id=user_id))
        db.commit()

    response = client.get("/profile", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["field"]
    assert body["skills"]
    assert body["level"]
    assert body["min_match_score"] == 70


def test_row_with_null_base_fields_then_save_restores_values(client: TestClient) -> None:
    user_id, headers = _register_and_login(client, "bare-save@example.com")
    with SessionLocal() as db:
        db.add(models.ProfileConfig(user_id=user_id))
        db.commit()
    payload = {"field": "Vision", "skills": "PyTorch", "level": "Student", "min_match_score": 60}

    assert client.post("/profile", json=payload, headers=headers).status_code == 200
    body = client.get("/profile", headers=headers).json()

    assert body["field"] == "Vision"
    assert body["min_match_score"] == 60


def test_blank_default_settings_fall_back_to_built_in_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty variable in backend/.env (DEFAULT_MIN_MATCH_SCORE=) must not stop the profile from loading.
    for name in ("DEFAULT_FIELD", "DEFAULT_SKILLS", "DEFAULT_LEVEL", "DEFAULT_MIN_MATCH_SCORE"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LINKEDIN_AGENT_NO_ENV_FILE", "1")
    blank_settings = Settings()
    monkeypatch.setattr("linkedin_agent.routers.profile.get_settings", lambda: blank_settings)
    _, headers = _register_and_login(client, "blank-settings@example.com")

    response = client.get("/profile", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["field"] == blank_settings.default_field
    assert body["min_match_score"] == 70
