from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth_utils, models, schemas
from ..auth_utils import get_current_user
from ..database import get_db
from ..services import retention

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserOut)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = models.User(email=user.email, hashed_password=auth_utils.hash_password(user.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=schemas.Token)
def login(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not auth_utils.verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

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
    if not auth_utils.verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    removed = retention.delete_user_data(db, current_user.id)
    return {"status": "deleted", "removed": removed}
