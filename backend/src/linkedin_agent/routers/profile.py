from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth_utils import get_current_user
from ..core.settings import get_settings
from ..database import get_db
from ..services.rag_service import add_knowledge, delete_knowledge, list_knowledge

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=schemas.ProfileConfigOut)
def get_profile(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == current_user.id).first()
    if not row:
        # Defaults until the user saves a profile (see core.settings)
        settings = get_settings()
        return schemas.ProfileConfigOut(
            field=settings.default_field,
            skills=settings.default_skills,
            level=settings.default_level,
            min_match_score=settings.default_min_match_score,
            job_feed_url=settings.default_job_feed_url,
            cert_feed_url=settings.default_cert_feed_url,
        )
    return row


@router.post("", response_model=schemas.ProfileConfigOut)
def update_profile(
    payload: schemas.ProfileConfigIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == current_user.id).first()

    if row:
        row.field = payload.field
        row.skills = payload.skills
        row.level = payload.level
        row.min_match_score = payload.min_match_score
        row.job_feed_url = payload.job_feed_url
        row.cert_feed_url = payload.cert_feed_url
    else:
        row = models.ProfileConfig(
            user_id=current_user.id,
            field=payload.field,
            skills=payload.skills,
            level=payload.level,
            min_match_score=payload.min_match_score,
            job_feed_url=payload.job_feed_url,
            cert_feed_url=payload.cert_feed_url,
        )
        db.add(row)

    db.commit()
    db.refresh(row)
    return row


class KnowledgeIn(BaseModel):
    content: str


@router.post("/knowledge")
def add_knowledge_item(
    payload: KnowledgeIn, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Contenu vide")
    item = add_knowledge(db, current_user.id, payload.content.strip())
    return {"id": item.id, "content": item.content}


@router.get("/knowledge")
def get_knowledge_items(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return [{"id": k.id, "content": k.content} for k in list_knowledge(db, current_user.id)]


@router.delete("/knowledge/{item_id}")
def delete_knowledge_item(
    item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    if not delete_knowledge(db, current_user.id, item_id):
        raise HTTPException(status_code=404, detail="Introuvable")
    return {"status": "deleted"}
