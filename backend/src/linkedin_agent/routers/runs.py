"""Any background run of the user, by its public id: status (polling) and a progress stream (server-sent events).

The stream is opened with a ticket, never with the login token: POST /runs/{run_id}/stream-ticket issues a short-lived,
single-use ticket bound to the user and the run; GET /runs/{run_id}/events?ticket=... opens the stream with it. See
services/stream_tickets.py for the rules and the tests. The events carry step names and status only.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..clock import utcnow
from ..core.exceptions import AppError, NotFoundError, http_error
from ..core.settings import get_settings
from ..database import SessionLocal, get_db
from ..services.stream_tickets import TICKETS, TicketError, ensure_owner, stream_event

router = APIRouter(prefix="/runs", tags=["runs"])
FINISHED = ("done", "closed", "failed", "awaiting_review")
KEEPALIVE = ": keep-alive\n\n"  # a comment line: keeps proxies from closing an idle stream, carries no data


class TicketRefused(AppError):
    """The stream ticket was refused. The reason is a fixed word, never a secret."""

    status_code = 401
    code = "ticket_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(f"stream ticket refused: {reason}")


def _find_run(db: Session, run_id: str) -> models.PreparationRun | None:
    return db.query(models.PreparationRun).filter(models.PreparationRun.public_id == run_id).first()


def _snapshot(run_id: str) -> dict[str, Any] | None:
    """The run's status and step, read in a short session. No prompt, answer or result text is read here."""
    with SessionLocal() as db:
        run = _find_run(db, run_id)
        if run is None:
            return None
        return {
            "status": run.status,
            "step": run.step or "",
            "stalled": bool(
                run.status in ("queued", "running")
                and run.heartbeat_at is not None
                and (utcnow() - run.heartbeat_at).total_seconds() >= get_settings().preparation_stale_seconds
            ),
        }


@router.get("/{run_id}", summary="Status of a background run (any kind)")
def get_run(run_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    run = _find_run(db, run_id)
    if run is None or run.user_id != current_user.id:
        raise http_error(NotFoundError("Run not found"))  # the same answer for another user's run and an unknown id
    return {"run_id": run.public_id, "kind": run.kind, "status": run.status, "step": run.step, "error": run.error}


@router.post(
    "/{run_id}/stream-ticket",
    summary="A single-use ticket to open the progress stream of a run",
    description=(
        "Returns a ticket valid for `stream_ticket_seconds`, for this run and this user only. Open the stream with "
        "`GET /runs/{run_id}/events?ticket=...`. The ticket works once. The login token is never put in a URL."
    ),
)
def issue_stream_ticket(
    run_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
) -> dict[str, Any]:
    run = _find_run(db, run_id)
    if run is None or run.user_id != current_user.id:
        raise http_error(NotFoundError("Run not found"))
    seconds = get_settings().stream_ticket_seconds
    ticket = TICKETS.issue(current_user.id, run_id, seconds)
    return {"ticket": ticket, "expires_in": seconds, "events": f"/runs/{run_id}/events?ticket={ticket}"}


async def _progress(run_id: str) -> AsyncIterator[str]:
    settings = get_settings()
    last: tuple[str, str] | None = None
    waited = 0.0
    while waited < settings.stream_max_seconds:
        snapshot = await asyncio.to_thread(_snapshot, run_id)
        if snapshot is None:
            yield stream_event("error", {"status": "not_found"})
            return
        current = (snapshot["status"], snapshot["step"])
        if current != last:
            yield stream_event("progress", snapshot)
            last = current
        if snapshot["status"] in FINISHED:
            yield stream_event("done", {"status": snapshot["status"]})
            return
        yield KEEPALIVE
        await asyncio.sleep(settings.stream_poll_seconds)
        waited += settings.stream_poll_seconds
    yield stream_event("timeout", {"status": "still running"})


@router.get(
    "/{run_id}/events",
    summary="Progress of a run as server-sent events (authenticated by a ticket)",
    description=(
        "Events: `progress` (status and step, on every change), `done` (final status, then the stream ends), "
        "`timeout`. Carries no prompt, answer or page text. Open it with a ticket from "
        "POST /runs/{run_id}/stream-ticket."
    ),
)
async def stream_run_events(run_id: str, ticket: str = Query(min_length=8, max_length=512)) -> StreamingResponse:
    try:
        user_id = TICKETS.consume(ticket, run_id)
    except TicketError as exc:
        raise TicketRefused(exc.reason) from exc
    owner = await asyncio.to_thread(_owner_of, run_id)
    if owner is None:
        raise http_error(NotFoundError("Run not found"))
    try:
        ensure_owner(user_id, owner)
    except TicketError as exc:
        raise TicketRefused(exc.reason) from exc
    return StreamingResponse(
        _progress(run_id),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _owner_of(run_id: str) -> int | None:
    with SessionLocal() as db:
        run = _find_run(db, run_id)
        return int(run.user_id) if run is not None else None
