"""The run's runtime behaviour: UTF-8 JSON that round-trips accents through the API and the database, and a heartbeat that
keeps moving during a slow model call. The model, the search and the page fetch are fakes.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.database import SessionLocal
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.services import research_runs
from linkedin_agent.services import research_service as rs

ACCENTED = "Offres à Montréal : « Ingénieur ML » côté Côte d'Ivoire, déjà vu ✓"
PLAN = '{"intent": "search", "queries": ["machine learning jobs france"]}'
REFLECT = '{"sufficient": true, "queries": []}'


def _user_id(email: str) -> int:
    with SessionLocal() as db:
        return int(db.query(models.User).filter(models.User.email == email).one().id)


# --- 5. UTF-8 ----------------------------------------------------------------------------------------


def test_json_declares_utf8_and_accents_round_trip_through_the_api_and_the_database(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    with SessionLocal() as db:
        db.add(models.AgentMessage(user_id=_user_id("alice@example.com"), role="assistant", content=ACCENTED))
        db.commit()

    response = client.get("/agent/history", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.json() == [{"role": "assistant", "content": ACCENTED}]

    with SessionLocal() as db:
        assert db.query(models.AgentMessage).one().content == ACCENTED  # stored unchanged


def test_error_responses_are_utf8_json_too(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/applications/preparations/identifiant-inconnu-é", headers=auth_headers)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.json() == {"detail": "Preparation not found"}  # the route raises HTTPException: detail only


# --- 6. the heartbeat during a long model call -------------------------------------------------------


def _latest_research_heartbeat() -> object:
    with SessionLocal() as db:
        run = db.query(models.PreparationRun).filter_by(kind="research").order_by(models.PreparationRun.id.desc()).one()
        return run.heartbeat_at


def test_the_heartbeat_keeps_moving_during_a_slow_model_call(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fast_heartbeat = research_runs.get_settings().model_copy(update={"heartbeat_interval_seconds": 0.02})
    monkeypatch.setattr(research_runs, "get_settings", lambda: fast_heartbeat)
    readings: list[tuple[object, object]] = []

    async def complete(self: object, prompt: str, **options: object) -> str:
        if "planner of a research agent" in prompt:
            return PLAN
        if "You judge whether the evidence" in prompt:
            return REFLECT
        if "You are a research assistant" in prompt:
            before = _latest_research_heartbeat()
            await asyncio.sleep(0.4)  # a slow provider: no other sign of life for 0.4 seconds
            readings.append((before, _latest_research_heartbeat()))
            return "Offres à Montréal [1]."
        raise AssertionError("unexpected prompt")

    async def search(query: str, num_results: int = 10) -> list[dict[str, str]]:
        return [{"title": "Machine learning jobs France", "link": "https://a.example/1", "summary": "Roles."}]

    async def fetch(url: str, **_: object) -> str:
        raise FetchError("unreachable")

    monkeypatch.setattr(llm_module.GroqClient, "complete", complete)
    monkeypatch.setattr(rs, "search_web", search)
    monkeypatch.setattr(rs, "fetch_text", fetch)

    response = client.post("/agent/research", json={"request": "What are the ML jobs in France?"}, headers=auth_headers)
    assert response.status_code == 202
    run = client.get(f"/agent/research/{response.json()['run_id']}", headers=auth_headers).json()
    assert run["status"] == "done", run
    assert readings, "the slow call was not reached"
    for before, after in readings:
        assert after > before, "the heartbeat did not move while the model was answering"
