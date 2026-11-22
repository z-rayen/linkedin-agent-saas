"""Connection state of LinkedIn and GitHub, and editing and publishing of queued posts, through the API."""

from datetime import timedelta

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.database import SessionLocal

ONE_DAY = timedelta(days=1)


def _user_id(email: str) -> int:
    with SessionLocal() as db:
        return int(db.query(models.User).filter(models.User.email == email).one().id)


def _states(client, headers) -> dict[str, str]:  # type: ignore[no-untyped-def]
    response = client.get("/oauth/connections", headers=headers)
    assert response.status_code == 200
    return {item["provider"]: item["status"] for item in response.json()}


def test_connections_are_not_connected_until_a_token_is_stored(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    assert _states(client, auth_headers) == {"linkedin": "not_connected", "github": "not_connected"}


def test_a_stored_token_is_connected_and_an_old_one_is_expired(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    user_id = _user_id("alice@example.com")
    with SessionLocal() as db:
        db.add(models.OAuthToken(user_id=user_id, provider="linkedin", access_token="x", expires_at=utcnow() + ONE_DAY))
        db.add(models.OAuthToken(user_id=user_id, provider="github", access_token="y", expires_at=utcnow() - ONE_DAY))
        db.commit()
    assert _states(client, auth_headers) == {"linkedin": "connected", "github": "expired"}


def _queued_post(status: str, text: str = "Original text") -> int:
    user_id = _user_id("alice@example.com")
    with SessionLocal() as db:
        item = models.ContentQueue(user_id=user_id, source="manual", title="Title", post_text=text, status=status)
        db.add(item)
        db.commit()
        return int(item.id)


def test_a_draft_can_be_edited(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    post_id = _queued_post("draft")
    response = client.patch(f"/content/{post_id}", json={"post_text": "Edited text"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["post_text"] == "Edited text"
    assert response.json()["title"] == "Title"


def test_a_published_post_cannot_be_edited_or_published_again(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    post_id = _queued_post("published")
    edit = client.patch(f"/content/{post_id}", json={"title": "New"}, headers=auth_headers)
    assert edit.status_code == 409
    assert "published" in edit.json()["detail"]

    publish = client.post(f"/content/{post_id}/publish", headers=auth_headers)
    assert publish.status_code == 409
    assert "already published" in publish.json()["detail"]


def test_an_edit_cannot_leave_the_text_empty_or_too_long(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    post_id = _queued_post("draft")
    assert client.patch(f"/content/{post_id}", json={"post_text": ""}, headers=auth_headers).status_code == 422
    too_long = "x" * 3001
    assert client.patch(f"/content/{post_id}", json={"post_text": too_long}, headers=auth_headers).status_code == 422
