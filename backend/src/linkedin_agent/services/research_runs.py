"""Background runs of the research agent: start, status, ownership, one active run per request, restart recovery.

The runs share the table of application preparations (preparation_runs), with kind "research". The lifecycle is the
same, and so is the discipline (see services/preparations.py):

    queued -> running -> done                 (the answer is stored in result_json)
    queued|running -> failed                  (the graph failed, or was interrupted before any checkpoint)
    running (stale heartbeat, checkpoint) -> running  (restart: resumed from its checkpoint, not started again)

A research run never reads, resumes or reports an application run, and the reverse holds too (kind is checked).
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
from ..core.exceptions import AlreadyRunningError, AppError, ValidationFailedError
from ..core.settings import get_settings
from ..database import SessionLocal
from ..graphs.research_graph import build_graph
from ..infra import heartbeat
from ..infra.checkpoint import active_saver
from .research_service import build_deps, result_of

logger = logging.getLogger(__name__)
KIND = "research"
ACTIVE = ("queued", "running")
DONE = "done"
PUBLIC_ID_BYTES = 24  # 192 random bits: a run id cannot be guessed or enumerated
MAX_REQUEST_CHARS = 2000


def new_public_id() -> str:
    return secrets.token_urlsafe(PUBLIC_ID_BYTES)


def lock_key_for(user_id: int, request: str) -> str:
    """The same user and the same request (case and spacing aside) cannot have two active runs.

    The key is a hash of a JSON array, so a value containing a separator cannot collide with another key. The kind is
    part of it, so a research run and an application can never share a lock.
    """
    normalized = " ".join(request.split()).lower()
    raw = json.dumps([user_id, KIND, normalized], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def start(
    db: Session,
    user_id: int,
    request: str,
    *,
    draft_post: bool,
    profile: dict[str, str],
    history: str,
) -> models.PreparationRun:
    """Create a queued research run. The database refuses a second active run for the same request."""
    text = request.strip()
    if not text:
        raise ValidationFailedError("request must not be blank")
    payload = {"request": text, "draft_post": draft_post, "profile": profile, "history": history}
    run = models.PreparationRun(
        user_id=user_id,
        public_id=new_public_id(),
        lock_key=lock_key_for(user_id, text),
        status="queued",
        kind=KIND,
        payload_json=json.dumps(payload),
        heartbeat_at=utcnow(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AlreadyRunningError("this research request is already running") from exc
    db.refresh(run)
    return run


def find_run(db: Session, user_id: int, public_id: str) -> models.PreparationRun | None:
    """The research run with this public id, only if it belongs to this user. Anyone else gets None, which is a 404."""
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
    """A heartbeat every `interval` seconds for as long as the graph runs. A long model call or a slow page fetch is
    silent otherwise, and a silent run looks stalled."""
    while True:
        await asyncio.sleep(interval)
        _set(run_id)


async def _save_draft(user_id: int, title: str, text: str) -> int:
    """A post goes into the content queue as a draft. Nothing here publishes it."""
    with SessionLocal() as db:
        item = models.ContentQueue(user_id=user_id, source="agent", title=title, post_text=text, status="draft")
        db.add(item)
        db.commit()
        db.refresh(item)
        return int(item.id)


def _remember(user_id: int, request: str, answer: str) -> None:
    """The exchange joins the agent's conversation history, like the answers of POST /agent/ask."""
    with SessionLocal() as db:
        db.add(models.AgentMessage(user_id=user_id, role="user", content=request))
        db.add(models.AgentMessage(user_id=user_id, role="assistant", content=answer))
        db.commit()


async def _run_graph(run_id: int, *, resume: bool) -> None:
    """Drive the graph of one run. Every outcome writes a final status: nothing is left running silently."""
    settings = get_settings()
    with SessionLocal() as db:
        run = db.get(models.PreparationRun, run_id)
        if run is None or run.kind != KIND:
            return
        payload = json.loads(run.payload_json)
        user_id = run.user_id

    async def on_step(name: str) -> None:
        _set(run_id, status="running", step=name)

    deps = build_deps(
        user_id=user_id,
        profile=payload["profile"],
        settings=settings,
        save_draft=_save_draft,
        on_step=on_step,
    )
    graph = build_graph(deps, checkpointer=active_saver())
    config = {"configurable": {"thread_id": str(run_id)}}  # the integer id is unique across both kinds
    initial = (
        None
        if resume
        else {
            "user_id": user_id,
            "request": payload["request"],
            "profile": payload["profile"],
            "history": payload["history"],
            "draft_post": payload["draft_post"],
        }
    )
    pulse = asyncio.create_task(_pulse(run_id, settings.heartbeat_interval_seconds))
    try:
        with heartbeat.heartbeat_bound(lambda: _beat(run_id)):
            state = await graph.ainvoke(initial, config)
        result = result_of(state)
        _set(run_id, status=DONE, step="finalize", result_json=json.dumps(result, default=str))
        _remember(user_id, payload["request"], result["answer"])
    except Exception as exc:
        logger.error("research run=%d failed error_type=%s", run_id, type(exc).__name__)
        message = exc.message if isinstance(exc, AppError) else f"research failed ({type(exc).__name__})"
        _set(run_id, status="failed", error=message)
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
    """At startup: each research run whose heartbeat went stale is resumed from its checkpoint, or failed.

    A run without a checkpoint cannot be resumed without starting again from the beginning, so it is failed with an
    instruction. Returns the number of runs handled.
    """
    settings = get_settings()
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=settings.preparation_stale_seconds)
    with SessionLocal() as db:
        stale = [
            run.id
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
            _set(run_id, status="failed", error="interrupted before any checkpoint: send the request again")
        else:
            _set(run_id, status="running", step="resuming")
            await _run_graph(run_id, resume=True)
        handled += 1
    return handled
