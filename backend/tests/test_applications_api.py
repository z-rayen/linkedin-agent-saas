"""Applications end to end through the API: the approval gate, the state machine and the send policy.

The LLM is a scripted fake and the mailer is a recorder. No network is used.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkedin_agent.core.exceptions import ExternalServiceError
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
UNGROUNDED_CV = {
    "lines": [
        {
            "section": "experience",
            "text": "Led a team of 40 engineers on a question-answering service",
            "source_ids": ["exp-1"],
        },
    ]
}
EMAIL = {"subject": "Application for ML Intern", "body": "Hello, I am interested."}


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage) -> SendResult:
        self.sent.append(message)
        return SendResult(dry_run=False, message_id=f"msg-{len(self.sent)}")


class FailingMailer:
    async def send(self, message: OutgoingMessage) -> SendResult:
        raise ExternalServiceError("smtp is down")


@pytest.fixture
def cv_answer(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | dict]:
    """Script what the model answers for the tailoring and the email prompts."""
    state: dict[str, str | dict] = {"cv": GROUNDED_CV}

    def fake(prompt: str) -> str:
        if "You tailor a CV" in prompt:
            return json.dumps(state["cv"])
        if "job application email" in prompt:
            return json.dumps(EMAIL)
        return "unused"

    monkeypatch.setattr(llm_module.GroqClient, "complete", lambda self, prompt, **options: _async(fake(prompt)))
    return state


async def _async(value: str) -> str:
    return value


@pytest.fixture
def mailer() -> RecordingMailer:
    recorder = RecordingMailer()
    app.dependency_overrides[get_mailer] = lambda: recorder
    yield recorder  # type: ignore[misc]
    app.dependency_overrides.pop(get_mailer, None)


def _with_master(client: TestClient, headers: dict[str, str]) -> None:
    r = client.put("/applications/master-cv", json=SAMPLE_MASTER, headers=headers)
    assert r.status_code == 200, r.text


def _prepare(client: TestClient, headers: dict[str, str], recipient: str, company: str | None = None) -> dict:
    body = {**JOB, "recipient_email": recipient, "company": company}
    return prepare_via_api(client, headers, body)


def _approve(client: TestClient, headers: dict[str, str], application_id: int):  # type: ignore[no-untyped-def]
    return client.post(f"/applications/{application_id}/approve", headers=headers)


def _send(client: TestClient, headers: dict[str, str], application_id: int):  # type: ignore[no-untyped-def]
    """Send with the recipient typed again, as the real-send confirmation requires."""
    recipient = client.get(f"/applications/{application_id}", headers=headers).json()["recipient_email"]
    return client.post(f"/applications/{application_id}/send", json={"confirm_recipient": recipient}, headers=headers)


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PDF is compiled from the approved CV. Tests do not run Tectonic."""
    monkeypatch.setattr(
        "linkedin_agent.services.applications.build_cv_pdf", lambda master, cv: b"%PDF-1.4 test attachment"
    )


# --- preparation ------------------------------------------------------------


def test_preparing_without_a_master_cv_is_refused(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post("/applications", json={**JOB, "recipient_email": "hr@acme.example"}, headers=auth_headers)
    assert r.status_code == 400
    assert "master CV" in r.json()["detail"]


def test_grounded_cv_is_stored_with_no_problems(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict
) -> None:
    _with_master(client, auth_headers)
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    assert app_row["status"] == "prepared"
    assert app_row["grounding_problems"] == []
    assert app_row["cv_lines"][0]["source_ids"] == ["exp-1"]


# --- approval gate (point 3) ---------------------------------------------------


def test_ungrounded_cv_cannot_be_approved(client: TestClient, auth_headers: dict[str, str], cv_answer: dict) -> None:
    _with_master(client, auth_headers)
    cv_answer["cv"] = UNGROUNDED_CV
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    assert app_row["grounding_problems"]  # the check found claims it cannot trace

    r = _approve(client, auth_headers, app_row["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "cv_not_grounded"
    assert client.get(f"/applications/{app_row['id']}", headers=auth_headers).json()["status"] == "prepared"


def test_grounded_cv_can_be_approved(client: TestClient, auth_headers: dict[str, str], cv_answer: dict) -> None:
    _with_master(client, auth_headers)
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    r = _approve(client, auth_headers, app_row["id"])
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["approved_at"] is not None


def test_approval_cannot_be_repeated(client: TestClient, auth_headers: dict[str, str], cv_answer: dict) -> None:
    _with_master(client, auth_headers)
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    _approve(client, auth_headers, app_row["id"])
    r = _approve(client, auth_headers, app_row["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


# --- state machine ------------------------------------------------------------


def test_nothing_is_sent_without_approval(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    r = _send(client, auth_headers, app_row["id"])
    assert r.status_code == 409
    assert mailer.sent == []


def test_dry_run_checks_everything_and_changes_nothing(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict
) -> None:
    # No override here: the real default mailer is used, and dry-run is on by default.
    _with_master(client, auth_headers)
    app_row = _prepare(client, auth_headers, "hr@acme.example")
    _approve(client, auth_headers, app_row["id"])
    r = _send(client, auth_headers, app_row["id"])
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert client.get(f"/applications/{app_row['id']}", headers=auth_headers).json()["status"] == "approved"


def test_failed_transport_marks_the_application_failed(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict
) -> None:
    app.dependency_overrides[get_mailer] = lambda: FailingMailer()
    try:
        _with_master(client, auth_headers)
        app_row = _prepare(client, auth_headers, "hr@acme.example")
        _approve(client, auth_headers, app_row["id"])
        r = _send(client, auth_headers, app_row["id"])
        assert r.status_code == 502
        assert client.get(f"/applications/{app_row['id']}", headers=auth_headers).json()["status"] == "failed"
    finally:
        app.dependency_overrides.pop(get_mailer, None)


# --- send policy: same company (point 4) ----------------------------------------


def _send_approved(client: TestClient, headers: dict[str, str], recipient: str, cv_answer: dict) -> dict:
    row = _prepare(client, headers, recipient)
    assert _approve(client, headers, row["id"]).status_code == 200
    return {"id": row["id"], "response": _send(client, headers, row["id"])}


def test_second_application_to_the_same_company_is_refused(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    first = _send_approved(client, auth_headers, "jobs@acme.example", cv_answer)
    assert first["response"].status_code == 200
    assert first["response"].json()["dry_run"] is False

    second = _send_approved(client, auth_headers, "hr@acme.example", cv_answer)  # same domain, different person
    assert second["response"].status_code == 409
    assert second["response"].json()["code"] == "send_policy_violation"
    assert "company" in second["response"].json()["detail"]
    assert len(mailer.sent) == 1  # the refused send never reached the mailer


def test_same_recipient_is_refused(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    _send_approved(client, auth_headers, "jobs@acme.example", cv_answer)
    again = _send_approved(client, auth_headers, "jobs@acme.example", cv_answer)
    assert again["response"].status_code == 409
    assert "recipient" in again["response"].json()["detail"]


def test_free_mail_addresses_are_not_treated_as_one_company(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    _send_approved(client, auth_headers, "alice.one@gmail.com", cv_answer)
    other = _send_approved(client, auth_headers, "bob.two@gmail.com", cv_answer)
    assert other["response"].status_code == 200
    assert len(mailer.sent) == 2


def test_company_match_is_per_user(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    _send_approved(client, auth_headers, "jobs@acme.example", cv_answer)

    client.post("/auth/register", json={"email": "carol@example.com", "password": "carol-pass-1"})
    token = client.post("/auth/login", json={"email": "carol@example.com", "password": "carol-pass-1"}).json()[
        "access_token"
    ]
    carol = {"Authorization": f"Bearer {token}"}
    _with_master(client, carol)
    carol_send = _send_approved(client, carol, "jobs@acme.example", cv_answer)
    assert carol_send["response"].status_code == 200  # another user may write to the same company


# --- ownership ------------------------------------------------------------------


def test_applications_are_private_to_their_owner(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict
) -> None:
    _with_master(client, auth_headers)
    row = _prepare(client, auth_headers, "hr@acme.example")

    client.post("/auth/register", json={"email": "dave@example.com", "password": "dave-pass-1234"})
    token = client.post("/auth/login", json={"email": "dave@example.com", "password": "dave-pass-1234"}).json()[
        "access_token"
    ]
    dave = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/applications/{row['id']}", headers=dave).status_code == 404
    assert _approve(client, dave, row["id"]).status_code == 404
    assert client.get("/applications", headers=dave).json() == []


def test_send_timestamp_is_recorded(
    client: TestClient, auth_headers: dict[str, str], cv_answer: dict, mailer: RecordingMailer
) -> None:
    _with_master(client, auth_headers)
    sent = _send_approved(client, auth_headers, "jobs@acme.example", cv_answer)
    detail = client.get(f"/applications/{sent['id']}", headers=auth_headers).json()
    assert detail["status"] == "sent"
    assert datetime.fromisoformat(detail["sent_at"])  # parses, so it was written
