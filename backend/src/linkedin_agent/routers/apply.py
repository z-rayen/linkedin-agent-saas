import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..core.settings import get_settings
from ..database import get_db
from ..services.apply_service import (
    fetch_job_page,
    find_application_email,
    generate_application_email,
    generate_tailored_cv_text,
)
from ..services.rag_service import search_knowledge
from .agent import _load_profile

router = APIRouter(prefix="/apply", tags=["apply"])


@router.post("/prepare/{watch_result_id}")
async def prepare(
    watch_result_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    job = (
        db.query(models.WatchResult)
        .filter(
            models.WatchResult.id == watch_result_id,
            models.WatchResult.user_id == current_user.id,
            models.WatchResult.type == "job",
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")

    profile = _load_profile(db, current_user.id)
    html, page_text = await fetch_job_page(job.link)
    knowledge = search_knowledge(db, current_user.id, f"{job.title} {page_text[:500]}", top_k=4)
    knowledge_text = "\n".join(f"- {k}" for k in knowledge)

    cv_summary = await generate_tailored_cv_text(job.title, page_text, profile, knowledge_text)
    await asyncio.sleep(get_settings().llm_min_interval_seconds)
    mail = await generate_application_email(job.title, cv_summary, profile)

    return {
        "recipient_email": find_application_email(html),
        "subject": mail["subject"],
        "body": mail["body"],
        "cv_summary": cv_summary,
        "warning": None
        if knowledge
        else "Ta base de connaissances est vide : le CV reste générique. Ajoute tes projets dans la page profil.",
    }
