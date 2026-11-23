"""The watch: a background run that finds offers and certifications for the profile, and the stored results.

The run is started with POST /watch/runs (202, with an unguessable run id) and followed with GET /watch/runs/{run_id}
or the progress stream of the runs router. Results are listed with filters and pagination. Nothing here submits an
application or sends a message: a result is a suggestion, and the user decides what to prepare.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..clock import utcnow
from ..core.exceptions import NotFoundError, http_error
from ..core.settings import get_settings
from ..database import get_db
from ..services import watch_runs

router = APIRouter(prefix="/watch", tags=["watch"])
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SCORE = 100
TYPE_OF_KIND = {"job": "job", "cert": "cert"}


def _result_out(item: models.WatchResult) -> dict[str, Any]:
    created: datetime | None = item.created_at
    return {
        "id": item.id,
        "type": item.type,
        "title": item.title,
        "link": item.link,
        "match_score": item.match_score,
        "justification": item.justification,
        "cover_letter": item.cover_letter,
        "company": item.company,
        "job_title": item.job_title,
        "fetched": bool(item.fetched),
        "unverified": bool(item.unverified),
        "created_at": created.isoformat() if created else None,
    }


@router.post(
    "/runs",
    status_code=202,
    summary="Start a watch run (asynchronous)",
    description=(
        "Accepts no body. The search queries come from the profile (regions, languages, offer type) and the configured "
        "templates. Returns 202 with a `run_id` and `status: queued` at once. Follow it with "
        "`GET /watch/runs/{run_id}` "
        "or the progress stream. A 409 means a watch is already running for this account."
    ),
)
async def start_watch(
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    settings = get_settings()
    fields = watch_runs.profile_fields_of(db, current_user.id, settings)
    run = watch_runs.start(db, current_user.id, profile_fields=fields)
    background.add_task(watch_runs.execute, run.id)
    return watch_runs.status_of(run, utcnow(), settings.preparation_stale_seconds)


@router.get("/runs/{run_id}", summary="Status and result of a watch run")
def get_watch_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    run = watch_runs.find_run(db, current_user.id, run_id)
    if run is None:
        raise http_error(NotFoundError("Watch run not found"))
    return watch_runs.status_of(run, utcnow(), get_settings().preparation_stale_seconds)


@router.get(
    "/results",
    summary="The stored results, newest first, with filters and pagination",
    description=(
        "`kind`: job or cert. `min_score`: 0 to 100. `verified`: true keeps only results whose full page was read. "
        "Results judged on a search snippet alone are unverified. The response carries `total` for paging."
    ),
)
def get_results(
    kind: Literal["job", "cert"] | None = None,
    min_score: int | None = Query(default=None, ge=0, le=MAX_SCORE),
    verified: bool | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    query = db.query(models.WatchResult).filter(models.WatchResult.user_id == current_user.id)
    if kind is not None:
        query = query.filter(models.WatchResult.type == TYPE_OF_KIND[kind])
    if min_score is not None:
        query = query.filter(models.WatchResult.match_score >= min_score)
    if verified is True:
        query = query.filter(models.WatchResult.unverified.is_(False))
    elif verified is False:
        query = query.filter(models.WatchResult.unverified.is_(True))
    total = query.count()
    items = (
        query.order_by(models.WatchResult.created_at.desc(), models.WatchResult.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"items": [_result_out(item) for item in items], "total": total, "limit": limit, "offset": offset}


@router.delete("/results/{result_id}", summary="Delete one stored result of the caller")
def delete_result(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> Response:
    item = (
        db.query(models.WatchResult)
        .filter(models.WatchResult.id == result_id, models.WatchResult.user_id == current_user.id)
        .first()
    )
    if item is None:
        raise http_error(NotFoundError("Watch result not found"))
    db.delete(item)
    db.commit()
    return Response(status_code=204)
