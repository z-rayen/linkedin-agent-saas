"""Background preparations on the test database: the lock, visible status, restart recovery, the review hand-off,
and approval that does not depend on the checkpoint. The LLM, the PDF and the search are fakes; the checkpointer
is the real Postgres one.
"""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.exceptions import AlreadyRunningError
from linkedin_agent.core.settings import Settings, get_settings
from linkedin_agent.database import SessionLocal
from linkedin_agent.domain.outputs import JobAnalysis
from linkedin_agent.graphs.application_graph import GraphDeps, build_graph
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.checkpoint import active_saver, checkpointer_lifespan
from linkedin_agent.services import preparations
from tests.helpers import prepare_via_api

SAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json").read_text(encoding="utf-8")
)
GROUNDED = {
    "lines": [{"section": "experience", "text": "Built a document question-answering service", "source_ids": ["exp-1"]}]
}
JOB = {"job_title": "ML Intern", "job_text": "We need Python and FastAPI.", "recipient_email": "hr@acme.com"}


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answers each prompt kind. Analysis is valid JSON, so the graph takes its normal path."""
    answers = {
        "analyze": json.dumps({"language": "en", "requirements": ["Python"], "company": None}),
        "cv": json.dumps(GROUNDED),
        "email": json.dumps({"subject": "Application for ML Intern", "body": "Hello, I am interested."}),
    }

    def fake(prompt: str) -> str:
        if "You read one job offer and extract three things" in prompt:
            return answers["analyze"]
        if "You tailor a CV" in prompt:
            return answers["cv"]
        if "job application email" in prompt:
            return answers["email"]
        return "unused"

    async def complete(self: object, prompt: str) -> str:
        return fake(prompt)

    monkeypatch.setattr(llm_module.GroqClient, "complete", complete)


@pytest.fixture(autouse=True)
def pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("linkedin_agent.services.applications.build_cv_pdf", lambda master, cv: b"%PDF-1.4 test")


def _user_with_master(client: TestClient) -> tuple[int, dict[str, str]]:
    client.post("/auth/register", json={"email": "run@example.com", "password": "run-pass-123"})
    token = client.post("/auth/login", json={"email": "run@example.com", "password": "run-pass-123"}).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/applications/master-cv", json=SAMPLE, headers=headers).status_code == 200
    with SessionLocal() as db:
        user_id = db.query(models.User).filter(models.User.email == "run@example.com").one().id
    return user_id, headers


def _run_row(run_id: int) -> models.PreparationRun:
    with SessionLocal() as db:
        return db.get(models.PreparationRun, run_id)  # type: ignore[return-value]


def _set_run(run_id: int, **values: Any) -> None:
    with SessionLocal() as db:
        db.query(models.PreparationRun).filter(models.PreparationRun.id == run_id).update(values)
        db.commit()


# --- the lock -----------------------------------------------------------------------


def test_a_second_active_preparation_for_the_same_application_is_refused(client: TestClient, llm: None) -> None:
    user_id, _ = _user_with_master(client)
    with SessionLocal() as db:
        first = preparations.start(db, user_id, dict(JOB))
        _set_run(first.id, status="running")  # the first one is in progress
    with SessionLocal() as db, pytest.raises(AlreadyRunningError):
        preparations.start(db, user_id, dict(JOB))


def test_the_lock_is_released_when_the_review_starts(client: TestClient, llm: None) -> None:
    user_id, headers = _user_with_master(client)
    prepare_via_api(client, headers, dict(JOB))  # reaches awaiting_review
    with SessionLocal() as db:
        again = preparations.start(db, user_id, dict(JOB))  # allowed: the first run no longer holds the lock
    assert again.status == "queued"


def test_a_different_recipient_is_not_locked(client: TestClient, llm: None) -> None:
    user_id, _ = _user_with_master(client)
    with SessionLocal() as db:
        first = preparations.start(db, user_id, dict(JOB))
        _set_run(first.id, status="running")
    with SessionLocal() as db:
        other = preparations.start(db, user_id, {**JOB, "recipient_email": "jobs@other.example"})
    assert other.status == "queued"


# --- status is visible --------------------------------------------------------------


def test_the_status_endpoint_shows_the_step_and_the_application(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    response = client.post("/applications", json=dict(JOB), headers=headers)
    run = client.get(f"/applications/preparations/{response.json()['run_id']}", headers=headers).json()
    assert run["status"] == "awaiting_review"
    assert run["step"] == "await_human"
    assert run["application_id"] is not None
    assert run["stalled"] is False


def test_a_run_of_another_user_is_not_visible(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    run_id = client.post("/applications", json=dict(JOB), headers=headers).json()["run_id"]
    client.post("/auth/register", json={"email": "other@example.com", "password": "other-pass-123"})
    token = client.post("/auth/login", json={"email": "other@example.com", "password": "other-pass-123"}).json()[
        "access_token"
    ]
    assert (
        client.get(f"/applications/preparations/{run_id}", headers={"Authorization": f"Bearer {token}"}).status_code
        == 404
    )


# --- run ids are unguessable, and only the owner can read a run ----------------------------


def test_the_run_id_is_a_random_token_not_the_database_number(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    body = client.post("/applications", json=dict(JOB), headers=headers).json()
    run_id = body["run_id"]
    assert isinstance(run_id, str)
    assert len(run_id) >= 32  # 192 random bits, URL-safe
    assert not run_id.isdigit()
    database_number = _run_row_by_public_id(run_id).id
    assert run_id != str(database_number)


def test_consecutive_runs_get_distinct_full_length_tokens(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    first = client.post("/applications", json=dict(JOB), headers=headers).json()["run_id"]
    second = client.post(
        "/applications", json={**JOB, "recipient_email": "jobs@other.example"}, headers=headers
    ).json()["run_id"]
    assert first != second
    assert len(first) >= 32 and len(second) >= 32


def test_the_database_number_does_not_open_a_run(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    body = client.post("/applications", json=dict(JOB), headers=headers).json()
    number = _run_row_by_public_id(body["run_id"]).id
    assert client.get(f"/applications/preparations/{number}", headers=headers).status_code == 404
    assert client.get("/applications/preparations/1", headers=headers).status_code == 404


def test_the_status_of_a_run_requires_a_login(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    run_id = client.post("/applications", json=dict(JOB), headers=headers).json()["run_id"]
    response = client.get(f"/applications/preparations/{run_id}")
    assert response.status_code in (401, 403)


def test_a_guessed_run_id_of_another_user_is_refused_the_same_as_an_unknown_one(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    run_id = client.post("/applications", json=dict(JOB), headers=headers).json()["run_id"]
    client.post("/auth/register", json={"email": "mallory@example.com", "password": "mallory-pass-123"})
    token = client.post("/auth/login", json={"email": "mallory@example.com", "password": "mallory-pass-123"}).json()[
        "access_token"
    ]
    mallory = {"Authorization": f"Bearer {token}"}
    other = client.get(f"/applications/preparations/{run_id}", headers=mallory)
    unknown = client.get("/applications/preparations/not-a-real-run-token", headers=mallory)
    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()  # no difference an attacker could use to tell them apart


def test_the_async_flow_is_documented_in_the_api_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    create = schema["paths"]["/applications"]["post"]
    status = schema["paths"]["/applications/preparations/{run_id}"]["get"]
    assert "202" in create["responses"]
    assert "awaiting_review" in create["description"] and "run_id" in create["description"]
    assert "random token" in status["description"]
    assert "404" in status["description"]


def _run_row_by_public_id(public_id: str) -> models.PreparationRun:
    with SessionLocal() as db:
        return db.query(models.PreparationRun).filter(models.PreparationRun.public_id == public_id).one()


# --- restart recovery: never stuck -----------------------------------------------------


def test_a_stale_run_with_a_checkpoint_is_resumed_and_reaches_review(client: TestClient, llm: None) -> None:
    user_id, headers = _user_with_master(client)
    public_id = client.post("/applications", json=dict(JOB), headers=headers).json()["run_id"]  # leaves a checkpoint
    run_id = _run_row_by_public_id(public_id).id
    _set_run(run_id, status="running", step="tailor_cv", heartbeat_at=utcnow() - timedelta(hours=1))
    handled = asyncio.run(preparations.recover_interrupted(now=utcnow()))
    assert handled == 1
    assert _run_row(run_id).status == "awaiting_review"  # resumed from the checkpoint and paused again for review


def test_a_stale_run_without_a_checkpoint_is_failed_with_an_instruction(client: TestClient, llm: None) -> None:
    user_id, _ = _user_with_master(client)
    with SessionLocal() as db:
        run = preparations.start(db, user_id, dict(JOB))
    _set_run(run.id, status="running", heartbeat_at=utcnow() - timedelta(hours=1))
    asyncio.run(preparations.recover_interrupted(now=utcnow()))
    assert _run_row(run.id).status == "failed"
    assert "submit the request again" in _run_row(run.id).error


def test_a_fresh_run_is_left_alone_by_recovery(client: TestClient, llm: None) -> None:
    user_id, _ = _user_with_master(client)
    with SessionLocal() as db:
        run = preparations.start(db, user_id, dict(JOB))
    _set_run(run.id, status="running", heartbeat_at=utcnow())
    assert asyncio.run(preparations.recover_interrupted(now=utcnow())) == 0
    assert _run_row(run.id).status == "running"


def test_a_stalled_run_is_reported_as_stalled(client: TestClient, llm: None) -> None:
    user_id, _ = _user_with_master(client)
    with SessionLocal() as db:
        run = preparations.start(db, user_id, dict(JOB))
    _set_run(run.id, status="running", heartbeat_at=utcnow() - timedelta(hours=1))
    with SessionLocal() as db:
        fresh = db.get(models.PreparationRun, run.id)
        assert preparations.status_of(fresh, utcnow(), 300.0)["stalled"] is True  # type: ignore[arg-type]


# --- the review hand-off and approval --------------------------------------------------


def test_approval_closes_the_workflow_record(client: TestClient, llm: None) -> None:
    _, headers = _user_with_master(client)
    application = prepare_via_api(client, headers, dict(JOB))
    assert client.post(f"/applications/{application['id']}/approve", headers=headers).status_code == 200
    run_id = _run_for(application["id"])
    assert _run_row(run_id).status == "closed"
    assert client.get(f"/applications/{application['id']}", headers=headers).json()["status"] == "approved"


def _forget_checkpoint(run_id: int) -> None:
    """Drop the run's checkpoint from the checkpointer this process runs (memory in tests)."""
    saver = active_saver()
    assert isinstance(saver, MemorySaver), "this helper only knows the in-memory checkpointer"
    saver.storage.pop(str(run_id), None)


def test_approval_works_when_the_checkpoint_is_gone(client: TestClient, llm: None) -> None:
    """The graph is never the source of truth: lose its checkpoint and the approval is still correct."""
    _, headers = _user_with_master(client)
    application = prepare_via_api(client, headers, dict(JOB))
    run_id = _run_for(application["id"])
    _forget_checkpoint(run_id)
    response = client.post(f"/applications/{application['id']}/approve", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    run = _run_row(run_id)
    assert run.status == "closed"  # the workflow could not be resumed, and the run is still closed, not stuck
    assert "approval unaffected" in (run.error or "")


def _run_for(application_id: int) -> int:
    with SessionLocal() as db:
        run = db.query(models.PreparationRun).filter(models.PreparationRun.application_id == application_id).one()
        return int(run.id)


# --- the checkpointer survives a restart --------------------------------------------------


class _Deps:
    @staticmethod
    def build(persisted: list[dict[str, Any]]) -> GraphDeps:
        async def nothing(*args: object) -> None:
            return None

        async def analyze(job_title: str, job_text: str) -> JobAnalysis:
            return JobAnalysis()

        async def tailor(title: str, text: str, master: Any, feedback: str) -> Any:
            from linkedin_agent.cv.models import TailoredCV

            return TailoredCV.model_validate(GROUNDED)

        async def draft(title: str, summary: str, feedback: str) -> dict[str, str]:
            return {"subject": "Application", "body": "Hello."}

        async def persist(state: Any) -> int:
            persisted.append(dict(state))
            return 7

        async def on_step(name: str) -> None:
            return None

        async def fetch(url: str) -> str:
            return ""

        async def research(company: str) -> list[dict[str, str]]:
            return []

        from linkedin_agent.cv.grounding import check_output

        return GraphDeps(
            fetch_page=fetch,
            analyze=analyze,
            research=research,
            retrieve=lambda uid, q: [],
            tailor=tailor,
            draft=draft,
            verify=check_output,
            persist=persist,
            on_step=on_step,
            max_revisions=2,
        )


def _postgres_settings() -> Settings:
    return get_settings().model_copy(update={"checkpoint_backend": "postgres"})


@pytest.mark.integration
def test_the_review_pause_survives_a_restart_of_the_checkpointer(client: TestClient, llm: None) -> None:
    """Pause on Postgres, close the pool (a restart), open a new one, resume: the workflow completes."""
    _user_with_master(client)
    persisted: list[dict[str, Any]] = []
    config = {"configurable": {"thread_id": "restart-test"}}
    state = {
        "user_id": 1,
        "job_title": "ML Intern",
        "job_text": "We need Python.",
        "recipient_email": "hr@acme.com",
        "company": None,
        "master_json": json.dumps(SAMPLE),
    }

    async def first_process() -> None:
        async with checkpointer_lifespan(_postgres_settings()) as saver:
            graph = build_graph(_Deps.build(persisted), checkpointer=saver)
            result = await graph.ainvoke(state, config)
            assert result.get("__interrupt__"), "the graph must pause for review"

    async def second_process() -> dict[str, Any]:
        from langgraph.types import Command

        async with checkpointer_lifespan(_postgres_settings()) as saver:
            graph = build_graph(_Deps.build(persisted), checkpointer=saver)
            return dict(await graph.ainvoke(Command(resume="approved"), config))

    asyncio.run(first_process())
    final = asyncio.run(second_process())
    assert final["review_decision"] == "approved"
    assert len(persisted) == 1


# --- configuration -----------------------------------------------------------------------


def test_the_checkpoint_url_falls_back_to_the_database_url_without_the_psycopg2_suffix() -> None:
    settings = get_settings()
    settings_copy = Settings(
        database_url="postgresql+psycopg2://u:secret@db:5432/app",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=settings.token_encryption_key,
    )
    assert settings_copy.checkpoint_url() == "postgresql://u:secret@db:5432/app"


def test_an_explicit_checkpoint_url_wins() -> None:
    settings = get_settings()
    explicit = Settings(
        database_url="postgresql://u:secret@db:5432/app",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=settings.token_encryption_key,
        checkpoint_database_url="postgresql://cp:other@cp-host:5432/checkpoints",
    )
    assert explicit.checkpoint_url() == "postgresql://cp:other@cp-host:5432/checkpoints"


def test_the_connection_string_is_never_logged(caplog: pytest.LogCaptureFixture, client: TestClient, llm: None) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    _, headers = _user_with_master(client)
    prepare_via_api(client, headers, dict(JOB))
    url = get_settings().checkpoint_url()
    text = " ".join(record.getMessage() for record in caplog.records)
    assert url not in text
    assert "postgresql://" not in text  # no connection string of any form, whatever the driver
