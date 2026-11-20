"""Background runs of the application graph: start, progress, lock, restart recovery, and the review hand-off.

Lifecycle of a run:
    queued -> running -> awaiting_review -> closed           (normal)
    queued|running -> failed                                  (the graph failed, or was interrupted before a checkpoint)
    queued|running -> running                                 (restart: resumed from its checkpoint)
    awaiting_review -> closed                                 (approval happened: the graph is resumed, then closed)

The status is always visible, and a run is never left dangling: every path ends in awaiting_review, closed or failed.
The application is the source of truth. The run and its checkpoint only drive the workflow, so losing them never
blocks an approval.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from langgraph.types import Command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..clock import utcnow
from ..core.exceptions import AlreadyRunningError, AppError, ValidationFailedError
from ..core.settings import Settings, get_settings
from ..cv.grounding import check_output
from ..cv.models import MasterCV, TailoredCV
from ..cv.pipeline import tailor_cv
from ..database import SessionLocal
from ..domain.outputs import JobAnalysis
from ..graphs.application_graph import GraphDeps, PreparationError, build_graph
from ..infra.checkpoint import open_checkpointer
from ..infra.llm import complete_structured
from ..prompts import render
from ..services.rag_service import search_knowledge
from ..services.search_service import search_web
from .applications import _profile_for, persist_prepared
from .apply_service import fetch_job_page, generate_application_email

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "running")
RESEARCH_RESULTS = 3
KNOWLEDGE_TOP_K = 4
Mode = Literal["start", "continue", "approved"]


def lock_key_for(user_id: int, recipient_email: str, job_title: str) -> str:
    """The same user, recipient and job title cannot have two active runs."""
    raw = f"{user_id}|{recipient_email.strip().lower()}|{job_title.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def start(db: Session, user_id: int, payload: dict[str, Any]) -> models.PreparationRun:
    """Create a queued run. The database refuses a second active run for the same key."""
    if not payload.get("job_text") and not payload.get("job_url"):
        raise ValidationFailedError("give the job text or a job URL")
    _, master_json = _profile_for(db, user_id)
    if not master_json:
        raise ValidationFailedError("Upload your master CV first (PUT /applications/master-cv)")
    run = models.PreparationRun(
        user_id=user_id,
        lock_key=lock_key_for(user_id, str(payload["recipient_email"]), str(payload["job_title"])),
        status="queued",
        payload_json=json.dumps(payload),
        heartbeat_at=utcnow(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AlreadyRunningError("this application is already being prepared") from exc
    db.refresh(run)
    return run


def status_of(run: models.PreparationRun, now: datetime, stale_seconds: float) -> dict[str, Any]:
    stalled = (
        run.status in ACTIVE
        and run.heartbeat_at is not None
        and (now - run.heartbeat_at) >= timedelta(seconds=stale_seconds)
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "step": run.step,
        "application_id": run.application_id,
        "error": run.error,
        "stalled": stalled,
    }


def _set(run_id: int, **fields: Any) -> None:
    """Update a run in its own short transaction, and refresh its heartbeat."""
    values: dict[str, Any] = {**fields, "heartbeat_at": utcnow()}
    with SessionLocal() as db:
        db.query(models.PreparationRun).filter(models.PreparationRun.id == run_id).update(
            values, synchronize_session=False
        )
        db.commit()


def _initial_state(payload: dict[str, Any], user_id: int, master_json: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "job_title": str(payload["job_title"]),
        "job_text": str(payload.get("job_text") or ""),
        "job_url": payload.get("job_url"),
        "recipient_email": str(payload["recipient_email"]),
        "company": payload.get("company"),
        "master_json": master_json,
    }


def _deps(db: Session, run_id: int, user_id: int, profile: dict[str, str], settings: Settings) -> GraphDeps:
    async def fetch_page(url: str) -> str:
        _, text = await fetch_job_page(url)
        return text

    async def analyze(job_title: str, job_text: str) -> JobAnalysis:
        prompt = render("analyze_job.v1", job_title=job_title, job_text=job_text)
        return await complete_structured(prompt, JobAnalysis)

    async def research(company: str) -> list[dict[str, str]]:
        results = await search_web(f"{company} company", num_results=RESEARCH_RESULTS)
        return [{"title": str(r["title"])[:200], "link": str(r["link"])[:500]} for r in results]

    def retrieve(uid: int, query: str) -> list[str]:
        return [str(row) for row in search_knowledge(db, uid, query, top_k=KNOWLEDGE_TOP_K)]

    async def tailor(title: str, text: str, master: MasterCV, feedback: str) -> TailoredCV:
        return (await tailor_cv(title, text, master, feedback=feedback)).cv

    async def draft(title: str, summary: str, feedback: str) -> dict[str, str]:
        return await generate_application_email(title, summary, profile, feedback=feedback)

    async def persist(state: Any) -> int:
        return persist_prepared(db, state)

    async def on_step(name: str) -> None:
        _set(run_id, status="running", step=name)

    return GraphDeps(
        fetch_page=fetch_page,
        analyze=analyze,
        research=research,
        retrieve=retrieve,
        tailor=tailor,
        draft=draft,
        verify=check_output,
        persist=persist,
        on_step=on_step,
        max_revisions=settings.graph_max_revisions,
    )


async def _run(run_id: int, mode: Mode) -> None:
    """Drive the graph for one run. Every outcome writes a final status; nothing is left running silently."""
    settings = get_settings()
    with SessionLocal() as db:
        run = db.get(models.PreparationRun, run_id)
        if run is None:
            return
        allowed = {"start": ACTIVE, "continue": ACTIVE, "approved": ("awaiting_review",)}[mode]
        if run.status not in allowed:
            return
        profile, master_json = _profile_for(db, run.user_id)
        if mode != "approved" and not master_json:
            _set(run_id, status="failed", error="master CV missing: upload it and submit the request again")
            return
        config = {"configurable": {"thread_id": str(run_id)}}
        try:
            deps = _deps(db, run_id, run.user_id, profile, settings)
            async with open_checkpointer() as saver:
                if mode == "approved":
                    # Without a checkpoint there is no pause to resume. Re-running the graph would start it again
                    # from the beginning, so the workflow is closed instead. The approval is already complete.
                    if await saver.aget_tuple(config) is None:
                        _set(run_id, status="closed", error="review closed: no checkpoint, approval unaffected")
                        return
                    graph = build_graph(deps, checkpointer=saver)
                    await graph.ainvoke(Command(resume="approved"), config)
                    _set(run_id, status="closed", step="finalize")
                    return
                graph = build_graph(deps, checkpointer=saver)
                payload_input = (
                    None
                    if mode == "continue"
                    else _initial_state(json.loads(run.payload_json), run.user_id, str(master_json))
                )
                result = await graph.ainvoke(payload_input, config)
            pending = result.get("__interrupt__") or []
            if pending:
                value = pending[0].value
                _set(run_id, status="awaiting_review", step="await_human", application_id=value["application_id"])
            else:
                _set(run_id, status="closed", step="finalize")
        except Exception as exc:
            if mode == "approved":
                # The approval already happened and is correct. Only the workflow record is closed.
                _set(run_id, status="closed", error="review closed: workflow not resumed, approval unaffected")
            elif isinstance(exc, PreparationError):
                _set(run_id, status="failed", error=str(exc))
            else:
                logger.error("preparation run=%d failed error_type=%s", run_id, type(exc).__name__)
                message = exc.message if isinstance(exc, AppError) else f"preparation failed ({type(exc).__name__})"
                _set(run_id, status="failed", error=message)


async def execute(run_id: int) -> None:
    """Background entry point for a new run."""
    await _run(run_id, "start")


async def resume_after_approval(run_id: int) -> None:
    """Background entry point after the application was approved. Approval never depends on this succeeding."""
    await _run(run_id, "approved")


async def recover_interrupted(now: datetime | None = None) -> int:
    """At startup: every active run whose heartbeat went stale is resumed from its checkpoint, or failed.

    A run without a checkpoint cannot be resumed, so it is marked failed with an instruction: nothing stays stuck.
    Returns the number of runs handled.
    """
    settings = get_settings()
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=settings.preparation_stale_seconds)
    with SessionLocal() as db:
        stale = (
            db.query(models.PreparationRun)
            .filter(models.PreparationRun.status.in_(ACTIVE), models.PreparationRun.heartbeat_at < cutoff)
            .all()
        )
        candidates = [run.id for run in stale]
    handled = 0
    for run_id in candidates:
        async with open_checkpointer() as saver:
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": str(run_id)}})
        if checkpoint is None:
            _set(run_id, status="failed", error="interrupted before any checkpoint: submit the request again")
        else:
            _set(run_id, status="running", step="resuming")
            await _run(run_id, "continue")
        handled += 1
    return handled
