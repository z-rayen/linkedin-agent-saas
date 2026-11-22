"""Stuck sends, manual resolution, the Message-ID recorded before sending, and the frozen PDF.

The mailer is a recorder and Tectonic is replaced: no network, no LaTeX.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.exceptions import CVCompilationError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.database import SessionLocal
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.mail import OutgoingMessage, SendResult, get_mailer
from linkedin_agent.main import app
from tests.helpers import prepare_via_api

PDF = b"%PDF-1.4 frozen attachment"
SAMPLE = {
    "full_name": "Jane Doe",
    "headline": "ML",
    "contact": {"email": "jane@example.com", "phone": "", "location": "", "links": []},
    "facts": [
        {"id": "exp-1", "kind": "experience", "text": "Built a document question-answering service"},
        {"id": "edu-1", "kind": "education", "text": "MSc in Computer Science"},
        {"id": "skill-py", "kind": "skill", "text": "Python"},
    ],
}
CV = {
    "lines": [
        {"section": "experience", "text": "Built a document question-answering service", "source_ids": ["exp-1"]},
        {"section": "skill", "text": "Python", "source_ids": ["skill-py"]},
    ]
}
EMAIL = {"subject": "Application for ML Intern", "body": "Hello, I am interested in the role."}


class Recorder:
    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []
        self.calls = 0

    async def send(self, message: OutgoingMessage) -> SendResult:
        self.calls += 1
        self.sent.append(message)
        return SendResult(dry_run=False, message_id=message.message_id)


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    def fake(prompt: str) -> str:
        if "You tailor a CV" in prompt:
            return json.dumps(CV)
        if "job application email" in prompt:
            return json.dumps(EMAIL)
        return "unused"

    async def complete(self: object, prompt: str, **options: object) -> str:
        return fake(prompt)

    monkeypatch.setattr(llm_module.GroqClient, "complete", complete)


@pytest.fixture
def pdf_builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts the compilations. Every compile returns the same bytes unless a test changes it."""
    calls: list[int] = []

    def build(master: object, cv: object) -> bytes:
        calls.append(1)
        return PDF

    monkeypatch.setattr("linkedin_agent.services.applications.build_cv_pdf", build)
    return calls


@pytest.fixture
def recorder() -> Recorder:
    mailer = Recorder()
    app.dependency_overrides[get_mailer] = lambda: mailer
    yield mailer  # type: ignore[misc]
    app.dependency_overrides.pop(get_mailer, None)


def _setup(client: TestClient, headers: dict[str, str]) -> int:
    assert client.put("/applications/master-cv", json=SAMPLE, headers=headers).status_code == 200
    app_id = prepare_via_api(
        client, headers, {"job_title": "ML Intern", "job_text": "Python", "recipient_email": "hr@acme.com"}
    )["id"]
    assert client.post(f"/applications/{app_id}/approve", headers=headers).status_code == 200
    return app_id


def _set_row(app_id: int, **values: object) -> None:
    with SessionLocal() as db:
        db.query(models.Application).filter(models.Application.id == app_id).update(values)
        db.commit()


def _detail(client: TestClient, headers: dict[str, str], app_id: int) -> dict:
    return client.get(f"/applications/{app_id}", headers=headers).json()


def _confirm(client: TestClient, headers: dict[str, str], app_id: int) -> dict:
    return {"confirm_recipient": _detail(client, headers, app_id)["recipient_email"]}


# --- Message-ID is recorded before the send starts ------------------------------------


def test_message_id_is_stored_before_the_network_is_touched(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    seen_during_send: dict[str, object] = {}

    class Inspecting(Recorder):
        async def send(self, message: OutgoingMessage) -> SendResult:
            with SessionLocal() as db:
                row = db.query(models.Application).filter(models.Application.id == app_id).one()
                seen_during_send["status"] = row.status
                seen_during_send["stored_id"] = row.smtp_message_id
            return await super().send(message)

    mailer = Inspecting()
    app.dependency_overrides[get_mailer] = lambda: mailer
    try:
        r = client.post(
            f"/applications/{app_id}/send", json=_confirm(client, auth_headers, app_id), headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_mailer, None)
    assert r.status_code == 200, r.text
    assert seen_during_send["status"] == "sending"
    assert seen_during_send["stored_id"] == mailer.sent[0].message_id
    assert r.json()["message_id"] == mailer.sent[0].message_id


# --- stuck sends ------------------------------------------------------------------------


def test_a_send_stuck_past_the_timeout_is_shown_as_needs_verification(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow() - timedelta(hours=2))
    assert _detail(client, auth_headers, app_id)["status"] == "needs_verification"


def test_a_recent_send_is_shown_as_sending_not_stuck(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow())
    assert _detail(client, auth_headers, app_id)["status"] == "sending"


def test_a_stuck_send_is_never_retried_automatically(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int], recorder: Recorder
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow() - timedelta(hours=2))
    r = client.post(f"/applications/{app_id}/send", json=_confirm(client, auth_headers, app_id), headers=auth_headers)
    assert r.status_code == 409
    assert recorder.calls == 0
    assert _detail(client, auth_headers, app_id)["status"] == "needs_verification"


def test_manual_resolution_as_sent(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    started = utcnow() - timedelta(hours=2)
    _set_row(app_id, status="sending", sending_started_at=started)
    r = client.post(
        f"/applications/{app_id}/resolve",
        json={"outcome": "sent", "note": "found it in the Sent folder"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert "manual resolution (sent)" in r.json()["last_error"]


def test_manual_resolution_as_failed_allows_a_fresh_approval(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow() - timedelta(hours=2))
    r = client.post(
        f"/applications/{app_id}/resolve", json={"outcome": "failed", "note": "not delivered"}, headers=auth_headers
    )
    assert r.json()["status"] == "failed"
    assert client.post(f"/applications/{app_id}/approve", headers=auth_headers).status_code == 200


def test_resolution_is_refused_for_a_send_that_is_not_stuck_yet(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow())
    r = client.post(f"/applications/{app_id}/resolve", json={"outcome": "sent", "note": "x"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


def test_resolution_is_refused_for_an_application_that_is_not_sending(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    r = client.post(f"/applications/{app_id}/resolve", json={"outcome": "sent", "note": "x"}, headers=auth_headers)
    assert r.status_code == 409


def test_the_timeout_is_configurable(
    client: TestClient,
    auth_headers: dict[str, str],
    answers: None,
    pdf_builds: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, status="sending", sending_started_at=utcnow() - timedelta(minutes=5))
    assert _detail(client, auth_headers, app_id)["status"] == "sending"  # default 15 minutes
    monkeypatch.setattr(get_settings(), "mail_sending_timeout_seconds", 60.0)
    assert _detail(client, auth_headers, app_id)["status"] == "needs_verification"


# --- the frozen PDF -----------------------------------------------------------------------


def test_the_preview_serves_the_same_bytes_that_are_attached(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int], recorder: Recorder
) -> None:
    app_id = _setup(client, auth_headers)
    preview = client.get(f"/applications/{app_id}/cv.pdf", headers=auth_headers)
    assert preview.status_code == 200
    assert preview.content == PDF
    assert preview.headers["content-type"] == "application/pdf"
    client.post(f"/applications/{app_id}/send", json=_confirm(client, auth_headers, app_id), headers=auth_headers)
    assert recorder.sent[0].attachment == preview.content


def test_the_pdf_is_compiled_at_approval_and_its_hash_is_in_the_approval(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    assert len(pdf_builds) >= 2  # once at prepare, once at approval
    detail = _detail(client, auth_headers, app_id)
    assert detail["has_pdf"] is True
    assert len(detail["pdf_sha256"]) == 64


def test_a_broken_template_is_caught_at_preparation(
    client: TestClient, auth_headers: dict[str, str], answers: None
) -> None:
    """The preparation runs in the background: the failure is reported on the run, and nothing is stored."""
    assert client.put("/applications/master-cv", json=SAMPLE, headers=auth_headers).status_code == 200
    with patch(
        "linkedin_agent.services.applications.build_cv_pdf",
        side_effect=CVCompilationError("LaTeX compilation failed"),
    ):
        r = client.post(
            "/applications",
            json={"job_title": "ML Intern", "job_text": "Python", "recipient_email": "hr@acme.com"},
            headers=auth_headers,
        )
    assert r.status_code == 202
    run = client.get(f"/applications/preparations/{r.json()['run_id']}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "LaTeX compilation failed" in run["error"]
    assert client.get("/applications", headers=auth_headers).json() == []


def test_a_broken_template_blocks_approval_and_leaves_the_status_alone(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    assert client.put("/applications/master-cv", json=SAMPLE, headers=auth_headers).status_code == 200
    app_id = prepare_via_api(
        client, auth_headers, {"job_title": "ML Intern", "job_text": "Python", "recipient_email": "hr@acme.com"}
    )["id"]
    with patch(
        "linkedin_agent.services.applications.build_cv_pdf",
        side_effect=CVCompilationError("LaTeX compilation failed"),
    ):
        r = client.post(f"/applications/{app_id}/approve", headers=auth_headers)
    assert r.status_code == 500
    assert _detail(client, auth_headers, app_id)["status"] == "prepared"


def test_an_edit_recompiles_the_pdf(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    before = len(pdf_builds)
    client.patch(f"/applications/{app_id}", json={"subject": "Candidature stage"}, headers=auth_headers)
    assert len(pdf_builds) == before + 1


def test_a_pdf_replaced_in_the_database_is_refused_at_send(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int], recorder: Recorder
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, cv_pdf=b"%PDF-1.4 swapped")
    r = client.post(f"/applications/{app_id}/send", json=_confirm(client, auth_headers, app_id), headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "content_changed"
    assert recorder.calls == 0


def test_a_changed_pdf_fingerprint_is_refused_at_send(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int], recorder: Recorder
) -> None:
    app_id = _setup(client, auth_headers)
    _set_row(app_id, cv_pdf_sha256="0" * 64)
    r = client.post(f"/applications/{app_id}/send", json=_confirm(client, auth_headers, app_id), headers=auth_headers)
    assert r.json()["code"] == "content_changed"
    assert recorder.calls == 0


def test_preview_is_private_to_the_owner(
    client: TestClient, auth_headers: dict[str, str], answers: None, pdf_builds: list[int]
) -> None:
    app_id = _setup(client, auth_headers)
    client.post("/auth/register", json={"email": "eve@example.com", "password": "eve-pass-123"})
    token = client.post("/auth/login", json={"email": "eve@example.com", "password": "eve-pass-123"}).json()[
        "access_token"
    ]
    assert client.get(f"/applications/{app_id}/cv.pdf", headers={"Authorization": f"Bearer {token}"}).status_code == 404
