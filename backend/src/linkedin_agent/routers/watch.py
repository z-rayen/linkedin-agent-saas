from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..database import get_db

router = APIRouter(prefix="/watch", tags=["watch"])


@router.get("/results")
def get_results(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    items = (
        db.query(models.WatchResult)
        .filter(models.WatchResult.user_id == current_user.id)
        .order_by(models.WatchResult.created_at.desc())
        .all()
    )
    return [
        {
            "id": i.id,
            "type": i.type,
            "title": i.title,
            "link": i.link,
            "match_score": i.match_score,
            "justification": i.justification,
            "cover_letter": i.cover_letter,
        }
        for i in items
    ]
