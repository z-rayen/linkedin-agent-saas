import secrets
from datetime import timedelta
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from sqlalchemy.orm import Session

from . import models
from .clock import utcnow
from .core.settings import get_settings
from .database import get_db

OAUTH_STATE_PURPOSE = "oauth_state"
# bcrypt reads at most 72 bytes of a password. Longer passwords are refused rather than silently cut.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """A bcrypt hash in the $2b$ format, the same format as the hashes stored before passlib was removed."""
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison. A password over bcrypt's byte limit never matches: bcrypt would read only its first
    72 bytes, so two different long passwords would share a hash. Registration refuses such passwords anyway."""
    encoded = plain.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, hashed.encode("ascii"))
    except ValueError:
        return False


def create_access_token(data: dict[str, Any]) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    token: str = jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    # An OAuth state token is signed with the same key: it must never work as a session token.
    if payload.get("purpose") == OAUTH_STATE_PURPOSE:
        raise ValueError("OAuth state token used as access token")
    return payload


def create_oauth_state(user_id: int, provider: str) -> str:
    """Signed, short-lived, single-purpose state bound to one user and one provider."""
    settings = get_settings()
    now = utcnow()
    claims = {
        "sub": str(user_id),
        "purpose": OAUTH_STATE_PURPOSE,
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(minutes=settings.oauth_state_expire_minutes),
    }
    state: str = jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)
    return state


def verify_oauth_state(state: str, provider: str) -> int:
    """Return the user id bound to a state. Raises ValueError or JWTError if it is not valid."""
    settings = get_settings()
    payload = jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("purpose") != OAUTH_STATE_PURPOSE or payload.get("provider") != provider:
        raise ValueError("OAuth state does not match this provider")
    return int(payload["sub"])


security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)
) -> models.User:
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(str(payload.get("sub")))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
