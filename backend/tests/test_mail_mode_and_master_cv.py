"""The send mode the frontend shows, the user's own master CV, and the PDF's declared type in the OpenAPI."""

import json
from pathlib import Path

from cryptography.fernet import Fernet

from linkedin_agent.core.settings import Settings
from linkedin_agent.main import app
from linkedin_agent.services.applications import mail_mode

SAMPLE_MASTER = json.loads((Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json").read_text())
NOT_AUTHENTICATED = (401, 403)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://unused/unused_test",
        "secret_key": "a-long-enough-secret-key",
        "token_encryption_key": Fernet.generate_key().decode(),
        "groq_api_key": "test-key",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def test_dry_run_is_reported_while_mail_dry_run_is_on() -> None:
    assert mail_mode(_settings(mail_dry_run=True, mail_redirect_to="")) == "dry_run"


def test_redirected_is_reported_when_a_test_address_is_configured() -> None:
    assert mail_mode(_settings(mail_dry_run=False, mail_redirect_to="qa@example.test")) == "redirected"


def test_real_is_reported_only_for_the_explicit_opt_in() -> None:
    assert mail_mode(_settings(mail_dry_run=False, mail_redirect_to="", mail_allow_real_recipients=True)) == "real"


def test_a_blank_redirect_address_is_not_a_redirect() -> None:
    assert mail_mode(_settings(mail_dry_run=False, mail_redirect_to="   ", mail_allow_real_recipients=True)) == "real"


def test_the_mode_endpoint_needs_a_login_and_reveals_no_address(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/applications/mail-mode").status_code in NOT_AUTHENTICATED
    response = client.get("/applications/mail-mode", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"mode": "dry_run"}  # the test configuration runs with MAIL_DRY_RUN=true


def test_the_master_cv_is_read_back_for_its_owner_only(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/applications/master-cv", headers=auth_headers).status_code == 404
    assert client.put("/applications/master-cv", json=SAMPLE_MASTER, headers=auth_headers).status_code == 200

    stored = client.get("/applications/master-cv", headers=auth_headers).json()
    assert stored["full_name"] == SAMPLE_MASTER["full_name"]
    assert len(stored["facts"]) == len(SAMPLE_MASTER["facts"])

    other = {"email": "other-cv@example.com", "password": "other-cv-password"}
    client.post("/auth/register", json=other)
    token = client.post("/auth/login", json=other).json()["access_token"]
    assert client.get("/applications/master-cv", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_the_pdf_is_declared_as_application_pdf() -> None:
    content = app.openapi()["paths"]["/applications/{application_id}/cv.pdf"]["get"]["responses"]["200"]["content"]
    assert "application/pdf" in content
