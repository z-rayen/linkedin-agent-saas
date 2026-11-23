import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth_utils import get_current_user
from ..core.settings import get_settings
from ..database import get_db
from ..services.rag_service import add_knowledge, delete_knowledge, list_knowledge

router = APIRouter(prefix="/profile", tags=["profile"])


def _text_or(value: str | None, fallback: str) -> str:
    return value if value and value.strip() else fallback


@router.get("", response_model=schemas.ProfileConfigOut)
def get_profile(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    settings = get_settings()
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == current_user.id).first()
    if not row:
        # Defaults until the user saves a profile (see core.settings).
        return schemas.ProfileConfigOut(
            field=settings.default_field,
            skills=settings.default_skills,
            level=settings.default_level,
            min_match_score=settings.default_min_match_score,
            job_feed_url=settings.default_job_feed_url,
            cert_feed_url=settings.default_cert_feed_url,
        )
    # A row can exist with empty base fields (for example one created when a master CV was saved first).
    # The base fields then read as the defaults; the optional fields are returned as stored.
    return schemas.ProfileConfigOut(
        field=_text_or(row.field, settings.default_field),
        skills=_text_or(row.skills, settings.default_skills),
        level=_text_or(row.level, settings.default_level),
        min_match_score=row.min_match_score if row.min_match_score is not None else settings.default_min_match_score,
        job_feed_url=row.job_feed_url,
        cert_feed_url=row.cert_feed_url,
        regions=row.regions,
        languages=row.languages,
        offer_type=row.offer_type,
    )


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

    # The watch preferences change only when they are sent: an older client keeps what is stored.
    if payload.regions is not None:
        row.regions = json.dumps(payload.regions)
    if payload.languages is not None:
        row.languages = json.dumps(payload.languages)
    if payload.offer_type is not None:
        row.offer_type = payload.offer_type
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
