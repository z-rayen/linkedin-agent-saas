from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..core.settings import get_settings
from ..database import get_db
from ..services.search_service import build_cert_queries, build_job_queries, search_web
from ..services.watch_service import generate_cover_letter, score_item

router = APIRouter(prefix="/watch", tags=["watch"])


@router.post("/run")
async def run_watch(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    settings = get_settings()
    profile_row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == current_user.id).first()

    profile = {
        "field": profile_row.field if profile_row else settings.default_field,
        "skills": profile_row.skills if profile_row else settings.default_skills,
        "level": profile_row.level if profile_row else settings.default_level,
    }
    min_score = profile_row.min_match_score if profile_row else settings.default_min_match_score

    results = []

    # Recherche d'offres via plusieurs requêtes générées dynamiquement
    job_queries = build_job_queries(profile["field"], profile["level"])
    jobs = []
    for q in job_queries:
        jobs.extend(await search_web(q, num_results=8))

    for job in jobs:
        existing = (
            db.query(models.WatchResult)
            .filter(models.WatchResult.user_id == current_user.id, models.WatchResult.link == job["link"])
            .first()
        )
        if existing:
            continue

        scored = await score_item(job, profile)
        if scored["score"] >= min_score:
            letter = await generate_cover_letter(job, profile)
            wr = models.WatchResult(
                user_id=current_user.id,
                type="job",
                title=job["title"],
                link=job["link"],
                match_score=scored["score"],
                justification=scored["justification"],
                cover_letter=letter,
            )
            db.add(wr)
            results.append(job["title"])

    # Recherche de certifications via plusieurs requêtes générées dynamiquement
    cert_queries = build_cert_queries(profile["field"], profile["skills"])
    certs = []
    for q in cert_queries:
        certs.extend(await search_web(q, num_results=5))

    for cert in certs:
        existing = (
            db.query(models.WatchResult)
            .filter(models.WatchResult.user_id == current_user.id, models.WatchResult.link == cert["link"])
            .first()
        )
        if existing:
            continue
        wr = models.WatchResult(user_id=current_user.id, type="cert", title=cert["title"], link=cert["link"])
        db.add(wr)
        results.append(cert["title"])

    db.commit()
    return {"new_items": len(results), "titles": results}


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
