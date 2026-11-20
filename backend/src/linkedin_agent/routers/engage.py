from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..database import get_db
from ..services.engage_service import suggest_engagement
from .agent import _load_profile

router = APIRouter(prefix="/engage", tags=["engage"])


class EngageRequest(BaseModel):
    topic: str


@router.post("/suggest")
async def suggest(
    payload: EngageRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    profile = _load_profile(db, current_user.id)
    return await suggest_engagement(db, current_user.id, payload.topic, profile)
