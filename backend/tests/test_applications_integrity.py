"""Approval integrity and send safety: the email is grounded, edits revoke approval, the send is atomic,
never retried, confirmed, and redirectable. The SMTP layer is replaced by a recorder: no network."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.core.exceptions import ExternalServiceError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.database import SessionLocal
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.mail import OutgoingMessage, SendResult, get_mailer
from linkedin_agent.main import app
from tests.helpers import prepare_via_api

SAMPLE_MASTER = json.loads((Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json").read_text())
JOB = {"job_title": "ML Intern", "job_text": "We need Python and FastAPI."}
GROUNDED_CV = {
    "lines": [
        {"section": "experience", "text": "Built a document question-answering service", "source_ids": ["exp-1"]},
        {"section": "skill", "text": "Python", "source_ids": ["skill-py"]},
    ]
}
HONEST_EMAIL = {"subject": "Application for ML Intern", "body": "Hello, I am interested in the role."}
PDF = b"%PDF-1.4 integrity test"


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage) -> SendResult:
        self.sent.append(message)
        return SendResult(dry_run=False, message_id=f"msg-{len(self.sent)}")


class FailingMailer:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, message: OutgoingMessage) -> SendResult:
        self.calls += 1
        raise ExternalServiceError("SMTP delivery failed")


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    state: dict[str, dict] = {"cv": GROUNDED_CV, "email": HONEST_EMAIL}

    def fake(prompt: str) -> str:
        if "You tailor a CV" in prompt:
            return json.dumps(state["cv"])
        if "job application email" in prompt:
            return json.dumps(state["email"])
        return "unused"

    async def complete(self: object, prompt: str) -> str:
        return fake(prompt)

    monkeypatch.setattr(llm_module.GroqClient, "complete", complete)
    return state


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("linkedin_agent.services.applications.build_cv_pdf", lambda master, cv: PDF)


@pytest.fixture
def recorder() -> RecordingMailer:
    mailer = RecordingMailer()
    app.dependency_overrides[get_mailer] = lambda: mailer
    yield mailer  # type: ignore[misc]
    app.dependency_overrides.pop(get_mailer, None)


def _setup(client: TestClient, headers: dict[str, str]) -> None:
    assert client.put("/applications/master-cv", json=SAMPLE_MASTER, headers=headers).status_code == 200


def _prepare(client: TestClient, headers: dict[str, str], recipient: str = "hr@acme.com") -> dict:
    return prepare_via_api(client, headers, {**JOB, "recipient_email": recipient})


def _approve(client: TestClient, headers: dict[str, str], app_id: int):  # type: ignore[no-untyped-def]
    return client.post(f"/applications/{app_id}/approve", headers=headers)


def _send(client: TestClient, headers: dict[str, str], app_id: int, confirm: str | None = "auto"):  # type: ignore[no-untyped-def]
    if confirm == "auto":
        confirm = client.get(f"/applications/{app_id}", headers=headers).json()["recipient_email"]
    body = {"confirm_recipient": confirm} if confirm else {}
    return client.post(f"/applications/{app_id}/send", json=body, headers=headers)


def _status(client: TestClient, headers: dict[str, str], app_id: int) -> str:
    return client.get(f"/applications/{app_id}", headers=headers).json()["status"]


def _approved(client: TestClient, headers: dict[str, str]) -> dict:
    row = _prepare(client, headers)
    assert _approve(client, headers, row["id"]).status_code == 200
    return row


# --- 1. the email body is grounded, like the CV -------------------------------------


def test_email_body_with_an_untraceable_claim_blocks_approval(
    client: TestClient, auth_headers: dict[str, str], answers: dict
) -> None:
    _setup(client, auth_headers)
    answers["email"] = {"subject": "Application for ML Intern", "body": "I led a team of 40 engineers."}
    row = _prepare(client, auth_headers)
    assert any(problem.startswith("email body:") for problem in row["grounding_problems"])
    r = _approve(client, auth_headers, row["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "cv_not_grounded"


def test_honest_email_body_is_not_flagged(client: TestClient, auth_headers: dict[str, str], answers: dict) -> None:
    _setup(client, auth_headers)
    row = _prepare(client, auth_headers)
    assert row["grounding_problems"] == []


# --- 2. any edit after approval sends the application back to prepared ----------------


def test_editing_the_email_after_approval_revokes_it_and_regrounds(
    client: TestClient, auth_headers: dict[str, str], answers: dict
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    r = client.patch(
        f"/applications/{row['id']}",
        json={"body": "Hello, I led a team of 40 engineers."},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "prepared"
    assert any(problem.startswith("email body:") for problem in r.json()["grounding_problems"])
    assert _approve(client, auth_headers, row["id"]).status_code == 409


def test_editing_the_cv_after_approval_revokes_it(
    client: TestClient, auth_headers: dict[str, str], answers: dict
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    new_lines = [{"section": "experience", "text": "Led a team of 40 engineers", "source_ids": ["exp-1"]}]
    r = client.patch(f"/applications/{row['id']}", json={"cv_lines": new_lines}, headers=auth_headers)
    assert r.json()["status"] == "prepared"
    assert any("led" in problem for problem in r.json()["grounding_problems"])


def test_edit_is_refused_while_the_send_is_in_progress(
    client: TestClient, auth_headers: dict[str, str], answers: dict
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    with SessionLocal() as db:
        db.query(models.Application).filter(models.Application.id == row["id"]).update({"status": "sending"})
        db.commit()
    r = client.patch(f"/applications/{row['id']}", json={"subject": "Other"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


# --- 3. the content is fingerprinted at approval and checked at send -----------------


def test_content_changed_directly_in_the_database_is_refused_at_send(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    with SessionLocal() as db:
        db.query(models.Application).filter(models.Application.id == row["id"]).update({"body": "Tampered."})
        db.commit()
    r = _send(client, auth_headers, row["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "content_changed"
    assert recorder.sent == []
    assert _status(client, auth_headers, row["id"]) == "prepared"


# --- 4. double send, concurrent claim, no automatic retry -----------------------------


def test_a_second_send_of_the_same_application_is_refused(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    assert _send(client, auth_headers, row["id"]).status_code == 200
    again = _send(client, auth_headers, row["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_state"
    assert len(recorder.sent) == 1


def test_an_application_already_being_sent_is_never_sent_again(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    """Simulates the second of two concurrent requests: the first has already claimed the row."""
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    with SessionLocal() as db:
        db.query(models.Application).filter(models.Application.id == row["id"]).update({"status": "sending"})
        db.commit()
    r = _send(client, auth_headers, row["id"])
    assert r.status_code == 409
    assert recorder.sent == []


def test_failure_is_recorded_and_never_retried(client: TestClient, auth_headers: dict[str, str], answers: dict) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    failing = FailingMailer()
    app.dependency_overrides[get_mailer] = lambda: failing
    try:
        first = _send(client, auth_headers, row["id"])
        assert first.status_code == 502
        assert _status(client, auth_headers, row["id"]) == "failed"
        second = _send(client, auth_headers, row["id"])  # the user must decide: no automatic retry
        assert second.status_code == 409
    finally:
        app.dependency_overrides.pop(get_mailer, None)
    assert failing.calls == 1


# --- 5. confirmation for a real send -------------------------------------------------


def test_real_send_without_confirmation_is_refused(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    r = _send(client, auth_headers, row["id"], confirm=None)
    assert r.status_code == 400
    assert recorder.sent == []


def test_real_send_with_the_wrong_confirmation_is_refused(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    r = _send(client, auth_headers, row["id"], confirm="someone-else@acme.com")
    assert r.status_code == 400
    assert recorder.sent == []


def test_confirmation_is_case_insensitive(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    assert _send(client, auth_headers, row["id"], confirm="HR@ACME.COM").status_code == 200


# --- 6. the attachment and the redirection ------------------------------------------


def test_attachment_is_the_pdf_built_from_the_approved_content(
    client: TestClient, auth_headers: dict[str, str], answers: dict, recorder: RecordingMailer
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    _send(client, auth_headers, row["id"])
    assert recorder.sent[0].attachment == PDF
    assert recorder.sent[0].to == "hr@acme.com"


def test_redirect_sends_every_message_to_the_test_address(
    client: TestClient,
    auth_headers: dict[str, str],
    answers: dict,
    recorder: RecordingMailer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "mail_redirect_to", "me@example.com")
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    r = _send(client, auth_headers, row["id"])
    assert r.status_code == 200
    message = recorder.sent[0]
    assert message.to == "me@example.com"
    assert message.original_to == "hr@acme.com"
    assert message.subject.startswith("[redirected]")


def test_dry_run_is_still_the_default_and_sends_nothing(
    client: TestClient, auth_headers: dict[str, str], answers: dict
) -> None:
    _setup(client, auth_headers)
    row = _approved(client, auth_headers)
    r = _send(client, auth_headers, row["id"])  # default mailer: dry-run
    assert r.json()["dry_run"] is True
    assert _status(client, auth_headers, row["id"]) == "approved"
