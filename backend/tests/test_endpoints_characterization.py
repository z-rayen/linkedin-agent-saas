"""Characterization tests for the HTTP endpoints of the current prototype.

They pin today's behaviour (status codes, payload shapes, persistence) so the
refactoring can be checked against it. External services (Groq, Serper, page
fetching) are replaced by fakes. Known prototype defects are marked xfail with
the phase that fixes them, instead of asserting the defective behaviour.
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from linkedin_agent import models
from linkedin_agent.database import SessionLocal

# Every LLM call goes through this single method, so one patch replaces all providers' answers.
GROQ_COMPLETE = "linkedin_agent.infra.llm.GroqClient.complete"


def fake_groq(prompt: str, **options: object) -> str:
    """Route fake LLM answers by the kind of prompt that was sent."""
    if "cover letter" in prompt:
        return "Dear hiring team, this is a fake cover letter."
    if "Respond ONLY with JSON" in prompt and '"score"' in prompt:
        return json.dumps({"score": 90, "justification": "Strong match."})
    if "planner" in prompt:
        return json.dumps({"intent": "search", "queries": ["fake query"]})
    if "Write one engaging LinkedIn post" in prompt:
        return "A fake LinkedIn post. #ai"
    if "job application email" in prompt:
        return json.dumps({"subject": "Application", "body": "Hello, fake body."})
    if "CV section" in prompt or "Professional Summary" in prompt:
        return "Professional Summary: fake summary."
    return "A fake answer [1]."


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GROQ_COMPLETE, AsyncMock(side_effect=fake_groq))


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(query: str, num_results: int = 10) -> list[dict[str, Any]]:
        if "certification" in query or "course" in query:
            return [{"title": "Free AWS ML course", "link": "https://example.com/cert", "summary": "cert"}]
        return [{"title": "NLP Engineer Job", "link": "https://example.com/job-1", "summary": "Remote job"}]

    for target in [
        "linkedin_agent.routers.watch.search_web",
        "linkedin_agent.services.agent_service.search_web",
        "linkedin_agent.services.engage_service.search_web",
    ]:
        monkeypatch.setattr(target, search)


# --- auth -------------------------------------------------------------------


def test_register_returns_user_without_password(client: TestClient) -> None:
    r = client.post("/auth/register", json={"email": "bob@example.com", "password": "pw-1234567890"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "bob@example.com"
    assert "id" in body and "created_at" in body
    assert "password" not in body and "hashed_password" not in body


def test_register_duplicate_email_is_rejected(client: TestClient) -> None:
    payload = {"email": "bob@example.com", "password": "pw-1234567890"}
    client.post("/auth/register", json=payload)
    r = client.post("/auth/register", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"] == "Email already registered"


def test_login_returns_bearer_token(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "bob@example.com", "password": "pw-1234567890"})
    r = client.post("/auth/login", json={"email": "bob@example.com", "password": "pw-1234567890"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_wrong_password_is_401(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "bob@example.com", "password": "pw-1234567890"})
    r = client.post("/auth/login", json={"email": "bob@example.com", "password": "nope"})
    assert r.status_code == 401


def test_protected_route_rejects_invalid_token(client: TestClient) -> None:
    r = client.get("/profile", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired token"


# --- profile and knowledge base ---------------------------------------------


def test_profile_defaults_when_nothing_saved(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/profile", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "Student / Junior"
    assert body["min_match_score"] == 70


def test_profile_save_then_read(client: TestClient, auth_headers: dict) -> None:
    payload = {
        "field": "NLP",
        "skills": "Python, RAG",
        "level": "Junior",
        "min_match_score": 80,
        "job_feed_url": None,
        "cert_feed_url": None,
    }
    assert client.post("/profile", json=payload, headers=auth_headers).status_code == 200
    body = client.get("/profile", headers=auth_headers).json()
    assert body["field"] == "NLP"
    assert body["min_match_score"] == 80


def test_knowledge_add_list_delete(client: TestClient, auth_headers: dict) -> None:
    created = client.post("/profile/knowledge", json={"content": "Built a RAG chatbot"}, headers=auth_headers)
    assert created.status_code == 200
    item_id = created.json()["id"]

    listed = client.get("/profile/knowledge", headers=auth_headers).json()
    assert [k["content"] for k in listed] == ["Built a RAG chatbot"]

    assert client.delete(f"/profile/knowledge/{item_id}", headers=auth_headers).status_code == 200
    assert client.get("/profile/knowledge", headers=auth_headers).json() == []


def test_knowledge_rejects_blank_content(client: TestClient, auth_headers: dict) -> None:
    r = client.post("/profile/knowledge", json={"content": "   "}, headers=auth_headers)
    assert r.status_code == 400


def test_knowledge_is_isolated_between_users(client: TestClient, auth_headers: dict) -> None:
    item_id = client.post("/profile/knowledge", json={"content": "secret project"}, headers=auth_headers).json()["id"]

    client.post("/auth/register", json={"email": "eve@example.com", "password": "eve-pass-1234"})
    token = client.post("/auth/login", json={"email": "eve@example.com", "password": "eve-pass-1234"}).json()[
        "access_token"
    ]
    other = {"Authorization": f"Bearer {token}"}

    assert client.delete(f"/profile/knowledge/{item_id}", headers=other).status_code == 404
    assert client.get("/profile/knowledge", headers=other).json() == []


# --- watch -------------------------------------------------------------------


def test_watch_run_stores_scored_jobs_and_certs(
    client: TestClient, auth_headers: dict, fake_llm: None, fake_search: None
) -> None:
    r = client.post("/watch/run", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["new_items"] >= 2

    results = client.get("/watch/results", headers=auth_headers).json()
    types = {item["type"] for item in results}
    assert types == {"job", "cert"}
    job = next(item for item in results if item["type"] == "job")
    assert job["match_score"] == 90
    assert job["cover_letter"].startswith("Dear hiring team")


def test_watch_run_is_idempotent_for_known_links(
    client: TestClient, auth_headers: dict, fake_llm: None, fake_search: None
) -> None:
    client.post("/watch/run", headers=auth_headers)
    second = client.post("/watch/run", headers=auth_headers).json()
    assert second["new_items"] == 0


# --- content -----------------------------------------------------------------


def test_content_generate_creates_draft(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("linkedin_agent.routers.content.generate_post", AsyncMock(return_value="Generated post text"))
    r = client.post("/content/generate", json={"title": "My RAG project", "context": "details"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft"
    assert body["post_text"] == "Generated post text"

    queue = client.get("/content/queue", headers=auth_headers).json()
    assert len(queue) == 1


def test_publish_without_linkedin_connection_is_400(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("linkedin_agent.routers.content.generate_post", AsyncMock(return_value="text"))
    post_id = client.post("/content/generate", json={"title": "t", "context": "c"}, headers=auth_headers).json()["id"]
    r = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "LinkedIn not connected"


def test_github_repos_without_connection_is_400(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/content/github/repos", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "GitHub not connected"


# --- agent -------------------------------------------------------------------


def test_agent_ask_stores_history(client: TestClient, auth_headers: dict, fake_llm: None, fake_search: None) -> None:
    r = client.post("/agent/ask", json={"request": "What is RAG?"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "search"
    assert body["post_saved"] is False
    assert body["sources"] and body["sources"][0]["link"].startswith("https://")

    history = client.get("/agent/history", headers=auth_headers).json()
    assert [m["role"] for m in history] == ["user", "assistant"]

    assert client.delete("/agent/history", headers=auth_headers).json() == {"status": "cleared"}
    assert client.get("/agent/history", headers=auth_headers).json() == []


def test_agent_post_intent_saves_draft_to_queue(
    client: TestClient, auth_headers: dict, fake_search: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def groq(prompt: str, **options: object) -> str:
        if "planner" in prompt:
            return json.dumps({"intent": "search_and_post", "queries": ["q"]})
        return fake_groq(prompt)

    monkeypatch.setattr(GROQ_COMPLETE, AsyncMock(side_effect=groq))

    r = client.post("/agent/ask", json={"request": "Write a post about RAG"}, headers=auth_headers)
    assert r.json()["post_saved"] is True
    queue = client.get("/content/queue", headers=auth_headers).json()
    assert queue[0]["source"] == "agent"


# --- engage ------------------------------------------------------------------


def test_engage_suggest_returns_comment_per_post(
    client: TestClient, auth_headers: dict, fake_llm: None, fake_search: None
) -> None:
    r = client.post("/engage/suggest", json={"topic": "RAG"}, headers=auth_headers)
    assert r.status_code == 200
    suggestions = r.json()
    assert len(suggestions) == 1
    assert set(suggestions[0]) == {"post_title", "post_link", "suggested_comment"}


# --- apply -------------------------------------------------------------------


def test_apply_prepare_warns_when_knowledge_base_empty(
    client: TestClient, auth_headers: dict, fake_llm: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == "alice@example.com").one()
        job = models.WatchResult(user_id=user.id, type="job", title="ML Intern", link="https://example.com/job")
        db.add(job)
        db.commit()
        job_id = job.id

    monkeypatch.setattr(
        "linkedin_agent.routers.apply.fetch_job_page",
        AsyncMock(return_value=("<p>Apply: <a>jobs@acme.com</a></p>", "Job page text")),
    )
    r = client.post(f"/apply/prepare/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["recipient_email"] == "jobs@acme.com"
    assert body["subject"] == "Application"
    assert body["warning"] and "base de connaissances est vide" in body["warning"]


def test_apply_prepare_unknown_offer_is_404(client: TestClient, auth_headers: dict) -> None:
    r = client.post("/apply/prepare/999", headers=auth_headers)
    assert r.status_code == 404


# --- oauth (Phase 0 step 6: authenticated, signed, short-lived state) --------


def test_oauth_authorize_url_requires_authentication(client: TestClient) -> None:
    r = client.get("/oauth/linkedin/authorize-url")
    assert r.status_code in (401, 403)


def test_oauth_authorize_url_state_is_signed_and_bound_to_user(client: TestClient, auth_headers: dict) -> None:
    from urllib.parse import parse_qs, urlparse

    from linkedin_agent.auth_utils import verify_oauth_state

    url = client.get("/oauth/linkedin/authorize-url", headers=auth_headers).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    with SessionLocal() as db:
        alice_id = db.query(models.User).filter(models.User.email == "alice@example.com").one().id
    assert state != str(alice_id)
    assert verify_oauth_state(state, "linkedin") == alice_id


def test_oauth_state_cannot_be_reused_for_another_provider(client: TestClient, auth_headers: dict) -> None:
    from urllib.parse import parse_qs, urlparse

    from linkedin_agent.auth_utils import verify_oauth_state

    url = client.get("/oauth/linkedin/authorize-url", headers=auth_headers).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(ValueError):
        verify_oauth_state(state, "github")


def test_callback_rejects_forged_state(client: TestClient) -> None:
    r = client.get("/oauth/linkedin/callback?code=abc&state=7", follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired OAuth state"


def test_access_token_cannot_be_used_as_oauth_state(client: TestClient, auth_headers: dict) -> None:
    access_token = auth_headers["Authorization"].removeprefix("Bearer ")
    r = client.get(f"/oauth/linkedin/callback?code=abc&state={access_token}", follow_redirects=False)
    assert r.status_code == 400


def test_oauth_state_token_cannot_authenticate_api_calls(client: TestClient, auth_headers: dict) -> None:
    from urllib.parse import parse_qs, urlparse

    url = client.get("/oauth/linkedin/authorize-url", headers=auth_headers).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = client.get("/profile", headers={"Authorization": f"Bearer {state}"})
    assert r.status_code == 401
