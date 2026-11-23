"""Runtime proof: no preparation path can move an application past `prepared`, and no path calls a mailer.

Each scenario runs the real service and graph (background run through the API, the review pause, resume after
approval, restart recovery). Only the LLM, the CV PDF and the mail transports are fakes. After every step, the
application rows must all be `prepared` and the mailer spies must be untouched.
"""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.exceptions import ValidationFailedError
from linkedin_agent.database import SessionLocal
from linkedin_agent.domain.applications import ApplicationStatus
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra import mail
from linkedin_agent.services import preparations
from tests.helpers import prepare_via_api

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json").read_text("utf-8"))
GROUNDED = {
    "lines": [{"section": "experience", "text": "Built a document question-answering service", "source_ids": ["exp-1"]}]
}
UNGROUNDED = {"lines": [{"section": "experience", "text": "Led a team of 40 engineers", "source_ids": ["exp-1"]}]}
JOB = {"job_title": "ML Intern", "job_text": "We need Python and FastAPI.", "recipient_email": "hr@acme.example"}
PREPARED = ApplicationStatus.PREPARED.value


@pytest.fixture
def cv_answers(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The CV answers the model gives, in order. The last one repeats. Tests set this before they start a run."""
    answers: list[dict[str, Any]] = [GROUNDED]
    analysis = json.dumps({"language": "en", "requirements": ["Python"], "company": None})
    email = json.dumps({"subject": "Application for ML Intern", "body": "Hello, I am interested."})

    def fake(prompt: str) -> str:
        if "You read one job offer and extract three things" in prompt:
            return analysis
        if "You tailor a CV" in prompt:
            return json.dumps(answers.pop(0) if len(answers) > 1 else answers[0])
        if "job application email" in prompt:
            return email
        return "unused"

    async def complete(self: object, prompt: str, **options: object) -> str:
        return fake(prompt)

    monkeypatch.setattr(llm_module.GroqClient, "complete", complete)
    return answers


@pytest.fixture(autouse=True)
def pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("linkedin_agent.services.applications.build_cv_pdf", lambda master, cv: b"%PDF-1.4 test")


@pytest.fixture
def mailer_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every call to any mailer, dry-run or SMTP. The preparation path must leave this list empty."""
    calls: list[str] = []

    async def dry_run(self: object, message: object) -> object:
        calls.append("DryRunMailer.send")
        raise AssertionError("preparation must never call a mailer")

    async def smtp(self: object, message: object) -> object:
        calls.append("SmtpMailer.send")
        raise AssertionError("preparation must never call a mailer")

    monkeypatch.setattr(mail.DryRunMailer, "send", dry_run)
    monkeypatch.setattr(mail.SmtpMailer, "send", smtp)
    return calls


@pytest.fixture
def user(client: TestClient) -> dict[str, str]:
    client.post("/auth/register", json={"email": "runtime@example.com", "password": "runtime-pass-123"})
    token = client.post("/auth/login", json={"email": "runtime@example.com", "password": "runtime-pass-123"}).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/applications/master-cv", json=SAMPLE, headers=headers).status_code == 200
    return headers


def _statuses() -> list[str]:
    with SessionLocal() as db:
        return [row.status for row in db.query(models.Application).all()]


def _run_for(application_id: int) -> models.PreparationRun:
    with SessionLocal() as db:
        return db.query(models.PreparationRun).filter(models.PreparationRun.application_id == application_id).one()


def _assert_never_past_prepared(mailer_calls: list[str]) -> None:
    statuses = _statuses()
    assert statuses, "the scenario should have stored an application"
    assert set(statuses) == {PREPARED}, f"an application moved past prepared: {statuses}"
    assert mailer_calls == [], f"a mailer was called: {mailer_calls}"


# --- the scenarios ---------------------------------------------------------------------


def test_success_pauses_for_review_as_prepared(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    application = prepare_via_api(client, user, dict(JOB))
    assert application["status"] == PREPARED
    assert application["grounding_problems"] == []
    _assert_never_past_prepared(mailer_calls)


def test_one_revision_that_fixes_the_problem_stays_prepared(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    cv_answers[:] = [UNGROUNDED, GROUNDED]
    application = prepare_via_api(client, user, dict(JOB))
    assert application["status"] == PREPARED
    assert application["grounding_problems"] == []
    _assert_never_past_prepared(mailer_calls)


def test_exhausted_revisions_keep_the_problems_and_stay_prepared(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    cv_answers[:] = [UNGROUNDED]  # the model never fixes the claim
    application = prepare_via_api(client, user, dict(JOB))
    assert application["status"] == PREPARED
    assert application["grounding_problems"], "the problems travel with the application"
    _assert_never_past_prepared(mailer_calls)


def test_resuming_the_review_closes_the_workflow_and_approves_nothing(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    application = prepare_via_api(client, user, dict(JOB))
    run = _run_for(application["id"])
    assert run.status == "awaiting_review"
    asyncio.run(preparations.resume_after_approval(run.id))  # the graph is resumed from its pause
    assert _run_for(application["id"]).status == "closed"
    _assert_never_past_prepared(mailer_calls)


def test_restart_resumes_from_the_checkpoint_without_duplicating_or_advancing(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    application = prepare_via_api(client, user, dict(JOB))
    run = _run_for(application["id"])
    _set_run(run.id, status="running", heartbeat_at=utcnow() - timedelta(hours=1))  # the process died mid-flight
    assert asyncio.run(preparations.recover_interrupted(now=utcnow())) == 1
    assert _run_for(application["id"]).status == "awaiting_review"
    with SessionLocal() as db:
        assert db.query(models.Application).count() == 1  # the recovery did not store a second application
    _assert_never_past_prepared(mailer_calls)


def test_restart_without_a_checkpoint_fails_the_run_and_stores_nothing(
    client: TestClient, user: dict, cv_answers: list, mailer_calls: list
) -> None:
    user_id = _user_id("runtime@example.com")
    with SessionLocal() as db:
        run = preparations.start(db, user_id, dict(JOB))
    _set_run(run.id, status="running", heartbeat_at=utcnow() - timedelta(hours=1))
    asyncio.run(preparations.recover_interrupted(now=utcnow()))
    with SessionLocal() as db:
        failed = db.get(models.PreparationRun, run.id)
    assert failed is not None and failed.status == "failed"
    assert _statuses() == []  # nothing was stored, so nothing can be approved or sent
    assert mailer_calls == []


def test_a_blank_recipient_never_creates_a_run(client: TestClient, user: dict, cv_answers: list) -> None:
    with SessionLocal() as db, pytest.raises(ValidationFailedError):
        preparations.start(db, _user_id("runtime@example.com"), {**JOB, "recipient_email": "   "})
    with SessionLocal() as db:
        assert db.query(models.PreparationRun).count() == 0


# --- helpers ---------------------------------------------------------------------------


def _user_id(email: str) -> int:
    with SessionLocal() as db:
        return int(db.query(models.User).filter(models.User.email == email).one().id)


def _set_run(run_id: int, **values: Any) -> None:
    with SessionLocal() as db:
        db.query(models.PreparationRun).filter(models.PreparationRun.id == run_id).update(values)
        db.commit()
