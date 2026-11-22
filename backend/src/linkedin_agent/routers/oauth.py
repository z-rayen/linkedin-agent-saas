from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from jose import JWTError
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import create_oauth_state, get_current_user, verify_oauth_state
from ..clock import utcnow
from ..core.crypto import encrypt_secret
from ..core.exceptions import InvalidOAuthStateError, http_error
from ..core.settings import get_settings
from ..database import get_db

router = APIRouter(prefix="/oauth", tags=["oauth"])

LINKEDIN_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_SCOPES = "openid profile email w_member_social"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_SCOPES = "read:user repo"
# The React page that shows the connections: the callbacks return the user there, with the outcome in the query.
CONNECTIONS_PAGE = "/connections"
PROVIDERS = ("linkedin", "github")
CONNECTED = "connected"
EXPIRED = "expired"
NOT_CONNECTED = "not_connected"


@router.get("/connections", summary="Connection state of each provider: connected, expired or not_connected")
def connections(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
) -> list[dict[str, object]]:
    rows: dict[str, models.OAuthToken] = {
        str(row.provider): row
        for row in db.query(models.OAuthToken).filter(models.OAuthToken.user_id == current_user.id).all()
    }
    now = utcnow()
    result: list[dict[str, object]] = []
    for provider in PROVIDERS:
        row = rows.get(provider)
        if row is None:
            result.append({"provider": provider, "status": NOT_CONNECTED, "expires_at": None})
        elif row.expires_at is not None and row.expires_at < now:
            result.append({"provider": provider, "status": EXPIRED, "expires_at": row.expires_at.isoformat()})
        else:
            expires = row.expires_at.isoformat() if row.expires_at is not None else None
            result.append({"provider": provider, "status": CONNECTED, "expires_at": expires})
    return result


def _bound_user_id(state: str, provider: str, db: Session) -> int:
    """Validate the state and return the user it was issued to."""
    try:
        user_id = verify_oauth_state(state, provider)
    except (JWTError, ValueError, KeyError) as exc:
        raise http_error(InvalidOAuthStateError("Invalid or expired OAuth state")) from exc
    if db.get(models.User, user_id) is None:
        raise http_error(InvalidOAuthStateError("Invalid or expired OAuth state"))
    return user_id


@router.get("/linkedin/authorize-url")
def linkedin_authorize_url(current_user: models.User = Depends(get_current_user)):
    """Return the LinkedIn consent URL for the authenticated user. The frontend redirects to it."""
    settings = get_settings()
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "scope": LINKEDIN_SCOPES,
        "state": create_oauth_state(current_user.id, "linkedin"),
    }
    return {"url": f"{LINKEDIN_AUTHORIZE_URL}?{urlencode(params)}"}


@router.get("/linkedin/callback")
async def linkedin_callback(code: str, state: str, db: Session = Depends(get_db)):
    settings = get_settings()
    user_id = _bound_user_id(state, "linkedin", db)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="LinkedIn token exchange failed")

    token_data = token_resp.json()
    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in")

    existing = (
        db.query(models.OAuthToken)
        .filter(models.OAuthToken.user_id == user_id, models.OAuthToken.provider == "linkedin")
        .first()
    )
    expires_at = utcnow() + timedelta(seconds=expires_in) if expires_in else None

    if existing:
        existing.access_token = encrypt_secret(access_token)
        existing.expires_at = expires_at
    else:
        db.add(
            models.OAuthToken(
                user_id=user_id,
                provider="linkedin",
                access_token=encrypt_secret(access_token),
                expires_at=expires_at,
            )
        )
    db.commit()

    return RedirectResponse(f"{settings.frontend_origin}{CONNECTIONS_PAGE}?provider=linkedin&status=connected")


@router.get("/github/authorize-url")
def github_authorize_url(current_user: models.User = Depends(get_current_user)):
    """Return the GitHub consent URL for the authenticated user. The frontend redirects to it."""
    settings = get_settings()
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": GITHUB_SCOPES,
        "state": create_oauth_state(current_user.id, "github"),
    }
    return {"url": f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"}


@router.get("/github/callback")
async def github_callback(code: str, state: str, db: Session = Depends(get_db)):
    settings = get_settings()
    user_id = _bound_user_id(state, "github", db)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub token exchange failed")

    existing = (
        db.query(models.OAuthToken)
        .filter(models.OAuthToken.user_id == user_id, models.OAuthToken.provider == "github")
        .first()
    )
    if existing:
        existing.access_token = encrypt_secret(access_token)
    else:
        db.add(models.OAuthToken(user_id=user_id, provider="github", access_token=encrypt_secret(access_token)))
    db.commit()

    return RedirectResponse(f"{settings.frontend_origin}{CONNECTIONS_PAGE}?provider=github&status=connected")
