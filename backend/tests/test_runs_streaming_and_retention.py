"""Progress streams over tickets, the retention purge, and the deletion of an account. Real test database; the model and the
network are not used (runs are created in a finished state).
"""

import json
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.database import SessionLocal
from linkedin_agent.services import research_runs, retention
from linkedin_agent.services.stream_tickets import TICKETS

SECRET_ANSWER = "SECRET-ANSWER-TEXT-THAT-MUST-NEVER-STREAM"
PROFILE = {"field": "ML", "skills": "Python", "level": "Junior"}


def _user_id(email: str) -> int:
    with SessionLocal() as db:
        return int(db.query(models.User).filter(models.User.email == email).one().id)


def _finished_research_run(user_id: int, request: str, *, status: str = "done") -> str:
    """A research run already in a final state, with a result that holds a secret: it must never reach a stream."""
    with SessionLocal() as db:
        run = research_runs.start(db, user_id, request, draft_post=False, profile=PROFILE, history="")
        run.status = status
        run.result_json = json.dumps({"answer": SECRET_ANSWER, "claims": []})
        db.commit()
        return str(run.public_id)


def _second_user(client: TestClient) -> dict[str, str]:
    client.post("/auth/register", json={"email": "bob@example.com", "password": "bob-pass-123"})
    token = client.post("/auth/login", json={"email": "bob@example.com", "password": "bob-pass-123"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _ticket(client: TestClient, headers: dict[str, str], run_id: str) -> str:
    response = client.post(f"/runs/{run_id}/stream-ticket", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["ticket"])


# --- progress streams ----------------------------------------------------------------------------------


def test_a_ticket_opens_the_progress_stream_of_its_run(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    issued = client.post(f"/runs/{run_id}/stream-ticket", headers=auth_headers).json()
    assert "events" in issued and issued["events"].startswith(f"/runs/{run_id}/events?ticket=")

    response = client.get(f"/runs/{run_id}/events", params={"ticket": issued["ticket"]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    body = response.text
    assert "event: progress" in body and "event: done" in body


def test_the_stream_carries_no_answer_text_and_only_allowlisted_keys(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    response = client.get(f"/runs/{run_id}/events", params={"ticket": _ticket(client, auth_headers, run_id)})
    assert SECRET_ANSWER not in response.text
    for line in response.text.splitlines():
        if line.startswith("data: "):
            assert set(json.loads(line[6:])) <= {"step", "status", "stalled"}


def test_the_stream_url_never_contains_the_login_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    issued = client.post(f"/runs/{run_id}/stream-ticket", headers=auth_headers).json()
    login_token = auth_headers["Authorization"].split(" ", 1)[1]
    assert login_token not in issued["events"]


def test_an_expired_ticket_is_refused(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    token = _ticket(client, auth_headers, run_id)
    TICKETS._tickets[token].expires_at = utcnow() - timedelta(seconds=1)  # the lifetime is over
    response = client.get(f"/runs/{run_id}/events", params={"ticket": token})
    assert response.status_code == 401
    assert response.json()["detail"] == "stream ticket refused: expired"


def test_a_used_ticket_is_refused_the_second_time(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    token = _ticket(client, auth_headers, run_id)
    assert client.get(f"/runs/{run_id}/events", params={"ticket": token}).status_code == 200
    response = client.get(f"/runs/{run_id}/events", params={"ticket": token})
    assert response.status_code == 401
    assert response.json()["detail"] == "stream ticket refused: reused"


def test_a_ticket_for_one_run_does_not_open_another_run(client: TestClient, auth_headers: dict[str, str]) -> None:
    user = _user_id("alice@example.com")
    first = _finished_research_run(user, "What are the ML jobs?")
    second = _finished_research_run(user, "What are the NLP jobs?")
    token = _ticket(client, auth_headers, first)
    response = client.get(f"/runs/{second}/events", params={"ticket": token})
    assert response.status_code == 401
    assert response.json()["detail"] == "stream ticket refused: wrong_run"


def test_a_ticket_of_another_user_does_not_open_this_users_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.post("/auth/register", json={"email": "bob@example.com", "password": "bob-pass-123"})
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    bobs_ticket = TICKETS.issue(_user_id("bob@example.com"), run_id, 60)  # a ticket issued to bob for alice's run
    response = client.get(f"/runs/{run_id}/events", params={"ticket": bobs_ticket})
    assert response.status_code == 401
    assert response.json()["detail"] == "stream ticket refused: wrong_user"


def test_an_unknown_ticket_is_refused(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    response = client.get(f"/runs/{run_id}/events", params={"ticket": "a-ticket-that-was-never-issued-0000"})
    assert response.status_code == 401
    assert response.json()["detail"] == "stream ticket refused: unknown"


def test_another_user_cannot_get_a_ticket_or_read_a_run(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?")
    other = _second_user(client)
    assert client.post(f"/runs/{run_id}/stream-ticket", headers=other).status_code == 404
    not_mine = client.get(f"/runs/{run_id}", headers=other)
    unknown = client.get("/runs/not-a-run-id-000000000000000000000000000", headers=other)
    assert not_mine.status_code == unknown.status_code == 404
    assert not_mine.json() == unknown.json()


def test_polling_stays_available_next_to_the_stream(client: TestClient, auth_headers: dict[str, str]) -> None:
    run_id = _finished_research_run(_user_id("alice@example.com"), "What are the ML jobs?", status="running")
    response = client.get(f"/runs/{run_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert "result" not in response.json()  # the polling answer stays on the research endpoint


# --- retention -----------------------------------------------------------------------------------------


def _age_run(run_id: str, days: int, status: str) -> int:
    with SessionLocal() as db:
        run = db.query(models.PreparationRun).filter(models.PreparationRun.public_id == run_id).one()
        run.status = status
        run.updated_at = utcnow() - timedelta(days=days)
        db.commit()
        return int(run.id)


def _watch_result(user_id: int, link: str, days_old: int) -> None:
    with SessionLocal() as db:
        db.add(
            models.WatchResult(
                user_id=user_id,
                type="job",
                title="ML internship",
                link=link,
                match_score=80,
                created_at=utcnow() - timedelta(days=days_old),
            )
        )
        db.commit()


def test_the_purge_removes_expired_finished_results_and_keeps_the_rest(clean_database: None) -> None:
    user = _user_id_or_create()
    old_run = _finished_research_run(user, "old research")
    recent_run = _finished_research_run(user, "recent research")
    active_run = _finished_research_run(user, "active research", status="running")
    old_id = _age_run(old_run, 400, "done")
    _age_run(recent_run, 2, "done")
    _age_run(active_run, 400, "running")  # old but still running: never purged
    _watch_result(user, "https://old.example/job", 400)
    _watch_result(user, "https://new.example/job", 2)
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES (:thread, '', 'c1', '{}', '{}')"
            ),
            {"thread": str(old_id)},
        )
        db.commit()

    with SessionLocal() as db:
        counts = retention.purge_expired(db, now=utcnow(), days=365)

    assert counts["runs"] == 1 and counts["watch_results"] == 1 and counts["checkpoints"] == 1
    with SessionLocal() as db:
        remaining = {run.public_id for run in db.query(models.PreparationRun).all()}
        assert old_run not in remaining and recent_run in remaining and active_run in remaining
        links = {row.link for row in db.query(models.WatchResult).all()}
        assert links == {"https://new.example/job"}
        assert (
            db.execute(text("SELECT COUNT(*) FROM checkpoints WHERE thread_id = :t"), {"t": str(old_id)}).scalar() == 0
        )


def test_the_purge_never_touches_application_runs(clean_database: None) -> None:
    user = _user_id_or_create()
    with SessionLocal() as db:
        run = models.PreparationRun(
            user_id=user,
            public_id="application-run-for-retention-0000000000000000",
            lock_key="lock-app-retention",
            status="done",
            kind="application",
            payload_json="{}",
            updated_at=utcnow() - timedelta(days=900),
        )
        db.add(run)
        db.commit()
    with SessionLocal() as db:
        counts = retention.purge_expired(db, now=utcnow(), days=365)
        assert counts["runs"] == 0
        assert db.query(models.PreparationRun).filter_by(kind="application").count() == 1


def _user_id_or_create() -> int:
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == "retention@example.com").first()
        if user is None:
            user = models.User(email="retention@example.com", hashed_password="x")
            db.add(user)
            db.commit()
            db.refresh(user)
        return int(user.id)


# --- deletion of an account ----------------------------------------------------------------------------


def _application_of(user_id: int) -> None:
    with SessionLocal() as db:
        db.add(
            models.Application(
                user_id=user_id,
                job_title="ML intern",
                recipient_email="hr@acme.example",
                subject="Application",
                body="Hello.",
                status="prepared",
                company_key="acme.example",
            )
        )
        db.commit()


def _count(model: Any, user_id: int) -> int:
    with SessionLocal() as db:
        return int(db.query(model).filter(model.user_id == user_id).count())


def test_deleting_the_account_removes_every_row_of_the_user_and_nothing_of_the_others(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    alice = _user_id("alice@example.com")
    bob_headers = _second_user(client)
    bob = _user_id("bob@example.com")
    _finished_research_run(alice, "alice research")
    _finished_research_run(bob, "bob research")
    _watch_result(alice, "https://alice.example/job", 1)
    _watch_result(bob, "https://bob.example/job", 1)
    _application_of(alice)
    _application_of(bob)
    with SessionLocal() as db:
        db.add(models.AgentMessage(user_id=alice, role="user", content="alice message"))
        db.commit()

    response = client.request("DELETE", "/auth/account", json={"password": "wrong-password"}, headers=auth_headers)
    assert response.status_code == 401
    assert _count(models.PreparationRun, alice) == 1  # a wrong password deletes nothing

    response = client.request("DELETE", "/auth/account", json={"password": "s3cret-pass"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deleted"
    for model in (models.PreparationRun, models.WatchResult, models.AgentMessage, models.Application):
        assert _count(model, alice) == 0
    with SessionLocal() as db:
        assert db.query(models.User).filter(models.User.id == alice).count() == 0
        assert db.query(models.User).filter(models.User.id == bob).count() == 1
    assert _count(models.PreparationRun, bob) == 1
    assert _count(models.WatchResult, bob) == 1
    assert _count(models.Application, bob) == 1
    assert client.get("/agent/history", headers=auth_headers).status_code == 401  # the token no longer works
    assert client.get("/agent/history", headers=bob_headers).status_code == 200
