import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth_utils import get_current_user
from ..clock import utcnow
from ..core.crypto import decrypt_secret
from ..core.exceptions import (
    ExternalServiceError,
    InvalidStateError,
    NotConnectedError,
    NotFoundError,
    http_error,
)
from ..database import get_db
from ..services.groq_service import generate_post

LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
GITHUB_REPOS_URL = "https://api.github.com/user/repos"
GITHUB_REPOS_PER_PAGE = 10

router = APIRouter(prefix="/content", tags=["content"])


def _provider_token(db: Session, user_id: int, provider: str, label: str) -> str:
    """Return the decrypted access token of a connected provider, or raise NotConnectedError."""
    row = (
        db.query(models.OAuthToken)
        .filter(
            models.OAuthToken.user_id == user_id,
            models.OAuthToken.provider == provider,
        )
        .first()
    )
    if row is None:
        raise http_error(NotConnectedError(f"{label} not connected"))
    if row.expires_at is not None and row.expires_at < utcnow():
        raise http_error(NotConnectedError(f"{label} token expired, reconnect"))
    token = decrypt_secret(row.access_token)
    if token is None:
        raise http_error(NotConnectedError(f"{label} connection is unreadable, reconnect"))
    return token


@router.post("/generate", response_model=schemas.ContentOut)
async def generate(
    payload: schemas.ContentCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    post_text = await generate_post(payload.title, payload.context)

    item = models.ContentQueue(
        user_id=current_user.id,
        source=payload.source,
        source_ref=None,
        title=payload.title,
        post_text=post_text,
        status="draft",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/queue", response_model=list[schemas.ContentOut])
def get_queue(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return (
        db.query(models.ContentQueue)
        .filter(models.ContentQueue.user_id == current_user.id)
        .order_by(models.ContentQueue.created_at.desc())
        .all()
    )


LINKEDIN_POST_MAX_CHARS = 3000
CONTENT_TITLE_MAX_CHARS = 200


class ContentEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=CONTENT_TITLE_MAX_CHARS)
    post_text: str | None = Field(default=None, min_length=1, max_length=LINKEDIN_POST_MAX_CHARS)


def _owned_content(db: Session, user_id: int, content_id: int) -> models.ContentQueue:
    item = (
        db.query(models.ContentQueue)
        .filter(models.ContentQueue.id == content_id, models.ContentQueue.user_id == user_id)
        .first()
    )
    if not item:
        raise http_error(NotFoundError("Post not found"))
    return item


@router.patch("/{content_id}", response_model=schemas.ContentOut)
def edit_content(
    content_id: int,
    payload: ContentEdit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Edits the title or the text of a post. A published post is history and cannot change."""
    item = _owned_content(db, current_user.id, content_id)
    if item.status == "published":
        raise http_error(InvalidStateError("A published post cannot be edited"))
    if payload.title is not None:
        item.title = payload.title
    if payload.post_text is not None:
        item.post_text = payload.post_text
    db.commit()
    db.refresh(item)
    return item


@router.post("/{content_id}/publish")
async def publish(
    content_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    item = _owned_content(db, current_user.id, content_id)
    if item.status == "published":
        raise http_error(InvalidStateError("This post is already published"))

    access_token = _provider_token(db, current_user.id, "linkedin", "LinkedIn")
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # The author URN is required to post on the member's behalf.
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(LINKEDIN_USERINFO_URL, headers=auth_headers)
    if userinfo_resp.status_code != 200:
        raise http_error(NotConnectedError("Failed to fetch LinkedIn user info"))

    person_urn = "urn:li:person:" + userinfo_resp.json()["sub"]

    async with httpx.AsyncClient() as client:
        post_resp = await client.post(
            LINKEDIN_UGC_POSTS_URL,
            headers={**auth_headers, "Content-Type": "application/json", "X-Restli-Protocol-Version": "2.0.0"},
            json={
                "author": person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": item.post_text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            },
        )

    if post_resp.status_code not in (200, 201):
        item.status = "error"
        item.last_error = post_resp.text
        db.commit()
        raise http_error(ExternalServiceError(f"LinkedIn publish failed: {post_resp.text}"))

    item.status = "published"
    db.commit()
    return {"status": "published"}


@router.get("/github/repos")
async def list_github_repos(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    access_token = _provider_token(db, current_user.id, "github", "GitHub")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            GITHUB_REPOS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"sort": "updated", "per_page": GITHUB_REPOS_PER_PAGE},
        )

    if resp.status_code != 200:
        raise http_error(ExternalServiceError("Failed to fetch repos"))

    return [
        {
            "name": r["name"],
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "url": r["html_url"],
        }
        for r in resp.json()
    ]
