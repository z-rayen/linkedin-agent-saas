from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth_utils, models, schemas
from ..auth_utils import get_current_user
from ..core.exceptions import PasswordTooLongError, PasswordTooShortError
from ..core.settings import get_settings
from ..database import get_db
from ..services import retention
from ..services.client_address import client_address
from ..services.login_guard import LOGIN_GUARD

router = APIRouter(prefix="/auth", tags=["auth"])


def _check_password_policy(password: str) -> None:
    """At least the configured length, and within bcrypt's limit in bytes (not characters)."""
    if len(password) < get_settings().password_min_length:
        raise PasswordTooShortError(get_settings().password_min_length)
    if len(password.encode("utf-8")) > auth_utils.BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(auth_utils.BCRYPT_MAX_BYTES)


@router.get(
    "/password-policy",
    summary="The password rules of registration, for the registration screen (public)",
)
def password_policy() -> dict[str, int]:
    return {"min_length": get_settings().password_min_length, "max_bytes": auth_utils.BCRYPT_MAX_BYTES}


@router.post("/register", response_model=schemas.UserOut)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    _check_password_policy(user.password)
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = models.User(email=user.email, hashed_password=auth_utils.hash_password(user.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=schemas.Token)
def login(user: schemas.UserCreate, request: Request, db: Session = Depends(get_db)):
    # The email is already normalised by the schema, so the account key is the same for every spelling of it.
    address = client_address(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        get_settings().trusted_proxies,
    )
    LOGIN_GUARD.check(user.email, address)
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not auth_utils.verify_password(user.password, db_user.hashed_password):
        LOGIN_GUARD.record_failure(user.email, address)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    LOGIN_GUARD.record_success(user.email)
    token = auth_utils.create_access_token({"sub": str(db_user.id)})
    return {"access_token": token}


class AccountDeletion(BaseModel):
    password: str  # typed again: a stray request cannot delete an account


@router.delete("/account", summary="Delete the account and every row that belongs to it")
def delete_account(
    payload: AccountDeletion,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Removes the account, its profile, knowledge, runs, results, applications, messages, tokens and checkpoints.
    Other users' data is untouched. The removal is one transaction: it either happens completely or not at all."""
    # 403, not 401: a 401 on an authenticated call means the session ended, and the screen would log the user out.
    if not auth_utils.verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(status_code=403, detail="The password is incorrect; nothing was deleted.")
    removed = retention.delete_user_data(db, current_user.id)
    return {"status": "deleted", "removed": removed}
