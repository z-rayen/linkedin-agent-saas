"""Background runs of the watch: start, status, ownership, one active run per user, restart recovery, and storage.

The runs share the table of background runs (preparation_runs) with kind "watch". The lifecycle and the discipline are
the ones of the research runs (see services/research_runs.py):

    queued -> running -> done                  (the kept results are stored, and listed in result_json)
    queued|running -> failed                   (the graph failed, or was interrupted before any checkpoint)
    running (stale heartbeat, checkpoint) -> running  (restart: resumed from its checkpoint, not started again)

A watch run stores results. It never submits an application, sends a message or publishes anything: the graph has no
dependency on those, and tests/test_watch_runs.py checks that no path reaches them.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..clock import utcnow
from ..core.exceptions import AlreadyRunningError
from ..core.settings import Settings, get_settings
from ..database import SessionLocal
from ..graphs.watch_graph import Candidate, build_graph, canonical_url
from ..infra import heartbeat
from ..infra.checkpoint import active_saver
from .watch_pipeline import build_deps, normalise_profile, watch_result_of

logger = logging.getLogger(__name__)
KIND = "watch"
ACTIVE = ("queued", "running")
DONE = "done"
NEW_PUBLIC_ID_BYTES = 24  # 192 random bits: a run id cannot be guessed or enumerated
CATEGORY_TO_TYPE = {"job": "job", "certification": "cert"}
INTERRUPTED = "interrupted before any checkpoint: start the watch again"


def start(db: Session, user_id: int, *, profile_fields: dict[str, Any]) -> models.PreparationRun:
    """Create a queued watch run. The database refuses a second active watch run for the same user."""
    run = models.PreparationRun(
        user_id=user_id,
        public_id=secrets.token_urlsafe(NEW_PUBLIC_ID_BYTES),
        lock_key=_lock_key(user_id),
        status="queued",
        kind=KIND,
        payload_json=json.dumps({"profile": profile_fields}),
        heartbeat_at=utcnow(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AlreadyRunningError("a watch is already running for this account") from exc
    db.refresh(run)
    return run


def _lock_key(user_id: int) -> str:
    """One active watch per user: the key depends on the user and the kind only, so a second start is refused."""
    return hashlib.sha256(json.dumps([user_id, KIND], separators=(",", ":")).encode("utf-8")).hexdigest()


def find_run(db: Session, user_id: int, public_id: str) -> models.PreparationRun | None:
    """The watch run with this public id, only if it belongs to this user. Anyone else gets None, which is a 404."""
    return (
        db.query(models.PreparationRun)
        .filter(
            models.PreparationRun.public_id == public_id,
            models.PreparationRun.user_id == user_id,
            models.PreparationRun.kind == KIND,
        )
        .first()
    )


def status_of(run: models.PreparationRun, now: datetime, stale_seconds: float) -> dict[str, Any]:
    stalled = (
        run.status in ACTIVE
        and run.heartbeat_at is not None
        and (now - run.heartbeat_at) >= timedelta(seconds=stale_seconds)
    )
    return {
        "run_id": run.public_id,
        "status": run.status,
        "step": run.step,
        "error": run.error,
        "stalled": stalled,
        "heartbeat_at": run.heartbeat_at,
        "result": json.loads(run.result_json) if run.result_json else None,
    }


def profile_fields_of(db: Session, user_id: int, settings: Settings) -> dict[str, Any]:
    """The profile the watch works with, read once when the run starts. Missing values come from the settings."""
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == user_id).first()
    if row is None:
        return normalise_profile({}, settings)
    return normalise_profile(
        {
            "field": row.field,
            "level": row.level,
            "skills": row.skills,
            "regions": _json_list(row.regions),
            "languages": _json_list(row.languages),
            "offer_type": row.offer_type,
            "min_match_score": row.min_match_score,
        },
        settings,
    )


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _set(run_id: int, **fields: Any) -> None:
    """Update a run in its own short transaction, and refresh its heartbeat."""
    values: dict[str, Any] = {**fields, "heartbeat_at": utcnow()}
    with SessionLocal() as db:
        db.query(models.PreparationRun).filter(models.PreparationRun.id == run_id).update(
            values, synchronize_session=False
        )
        db.commit()


async def _beat(run_id: int) -> None:
    _set(run_id)


async def _pulse(run_id: int, interval: float) -> None:
    """A heartbeat every `interval` seconds while the graph runs: a slow fetch or a model call is silent otherwise."""
    while True:
        await asyncio.sleep(interval)
        _set(run_id)


def known_links(user_id: int) -> list[str]:
    """The canonical links of the user's stored results. Rows written before the canonical column are canonicalised
    here, so the dedupe is the same for old and new rows."""
    with SessionLocal() as db:
        rows = db.query(models.WatchResult.link, models.WatchResult.canonical_link).filter(
            models.WatchResult.user_id == user_id
        )
        return [str(canonical or canonical_url(str(link or ""))) for link, canonical in rows.all() if link or canonical]


def save_kept(user_id: int, kept: list[Candidate]) -> int:
    """Stores the kept candidates as results of this user, with their provenance. Returns how many were stored."""
    with SessionLocal() as db:
        for candidate in kept:
            db.add(
                models.WatchResult(
                    user_id=user_id,
                    type=CATEGORY_TO_TYPE.get(candidate["category"], "other"),
                    title=candidate["title"],
                    link=candidate["link"],
                    match_score=candidate["relevance"],
                    justification=candidate["justification"] or None,
                    cover_letter=None,
                    canonical_link=candidate["canonical"],
                    fetched=candidate["fetched"],
                    unverified=candidate["unverified"],
                    company=candidate["company"] or None,
                    job_title=candidate["job_title"] or None,
                )
            )
        db.commit()
    return len(kept)


async def _run_graph(run_id: int, *, resume: bool) -> None:
    """Drive the graph of one run. Every outcome writes a final status: nothing is left running silently."""
    settings = get_settings()
    with SessionLocal() as db:
        run = db.get(models.PreparationRun, run_id)
        if run is None or run.kind != KIND:
            return
        payload = json.loads(run.payload_json)
        user_id = int(run.user_id)

    async def on_step(name: str) -> None:
        _set(run_id, status="running", step=name)

    async def known(owner: int) -> list[str]:
        return await asyncio.to_thread(known_links, owner)

    async def save(owner: int, kept: list[Candidate]) -> int:
        return await asyncio.to_thread(save_kept, owner, kept)

    deps = build_deps(
        profile=payload["profile"],
        settings=settings,
        known_links=known,
        save_kept=save,
        on_step=on_step,
    )
    graph = build_graph(deps, checkpointer=active_saver())
    config = {"configurable": {"thread_id": str(run_id)}}  # the integer id is unique across the run kinds
    initial = None if resume else {"user_id": user_id, "profile": payload["profile"]}
    pulse = asyncio.create_task(_pulse(run_id, settings.heartbeat_interval_seconds))
    try:
        with heartbeat.heartbeat_bound(lambda: _beat(run_id)):
            state = await graph.ainvoke(initial, config)
        result = watch_result_of(state)
        _set(run_id, status=DONE, step="finalize", result_json=json.dumps(result, default=str))
    except Exception as exc:
        logger.error("watch run=%d failed error_type=%s", run_id, type(exc).__name__)
        _set(run_id, status="failed", error=f"watch failed ({type(exc).__name__})")
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse


async def execute(run_id: int) -> None:
    """Background entry point for a new run."""
    with SessionLocal() as db:
        run = db.get(models.PreparationRun, run_id)
        if run is None or run.kind != KIND or run.status not in ACTIVE:
            return
    await _run_graph(run_id, resume=False)


async def recover_interrupted(now: datetime | None = None) -> int:
    """At startup: each watch run whose heartbeat went stale is resumed from its checkpoint, or failed when it has none.

    Returns the number of runs handled.
    """
    settings = get_settings()
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=settings.preparation_stale_seconds)
    with SessionLocal() as db:
        stale = [
            int(run.id)
            for run in db.query(models.PreparationRun)
            .filter(
                models.PreparationRun.kind == KIND,
                models.PreparationRun.status.in_(ACTIVE),
                models.PreparationRun.heartbeat_at < cutoff,
            )
            .all()
        ]
    saver = active_saver()
    handled = 0
    for run_id in stale:
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": str(run_id)}})
        if checkpoint is None:
            _set(run_id, status="failed", error=INTERRUPTED)
        else:
            _set(run_id, status="running", step="resuming")
            await _run_graph(run_id, resume=True)
        handled += 1
    return handled
