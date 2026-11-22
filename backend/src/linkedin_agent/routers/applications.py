"""Applications API. Each step (prepare, approve, send) is checked server-side; the UI cannot skip one."""

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..clock import utcnow
from ..core.exceptions import NotFoundError, http_error
from ..core.settings import get_settings
from ..cv.models import MasterCV, TailoredLine
from ..database import get_db
from ..infra.mail import Mailer, get_mailer
from ..services import applications as service
from ..services import preparations

router = APIRouter(prefix="/applications", tags=["applications"])


class ApplicationCreate(BaseModel):
    job_title: str = Field(min_length=1, max_length=200)
    job_text: str | None = Field(default=None, max_length=20000)
    job_url: str | None = Field(default=None, max_length=2000)
    recipient_email: EmailStr
    company: str | None = Field(default=None, max_length=200)


class ApplicationEdit(BaseModel):
    """Any field can change. Changing anything after approval sends the application back to prepared."""

    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    recipient_email: EmailStr | None = None
    cv_lines: list[TailoredLine] | None = Field(default=None, min_length=1)


class SendRequest(BaseModel):
    """Required for a real send: the recipient address typed again, so a stray click cannot send."""

    confirm_recipient: EmailStr | None = None


class ResolveRequest(BaseModel):
    """Manual decision on a send stuck in "sending". Check the mailbox and the SMTP log before choosing."""

    outcome: Literal["sent", "failed"]
    note: str = Field(min_length=1, max_length=500)


def _out(application: models.Application) -> dict[str, Any]:
    cv = service.tailored_cv_of(application)
    return {
        "id": application.id,
        "status": service.display_status(application, utcnow()),
        "company": application.company,
        "company_key": application.company_key,
        "job_title": application.job_title,
        "recipient_email": application.recipient_email,
        "subject": application.subject,
        "body": application.body,
        "grounding_problems": service.problems_of(application),
        "cv_lines": [line.model_dump() for line in cv.lines] if cv else [],
        "has_pdf": application.cv_pdf is not None,
        "pdf_sha256": application.cv_pdf_sha256,
        "smtp_message_id": application.smtp_message_id,
        "last_error": application.last_error,
        "created_at": application.created_at,
        "approved_at": application.approved_at,
        "sending_started_at": application.sending_started_at,
        "sent_at": application.sent_at,
    }


def _owned(db: Session, user: models.User, application_id: int) -> models.Application:
    application = (
        db.query(models.Application)
        .filter(models.Application.id == application_id, models.Application.user_id == user.id)
        .first()
    )
    if application is None:
        raise http_error(NotFoundError("Application not found"))
    return application


@router.put("/master-cv")
def put_master_cv(
    master: MasterCV,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    service.save_master_cv(db, current_user.id, master)
    return {"status": "saved", "facts": len(master.facts)}


@router.post(
    "",
    status_code=202,
    summary="Start preparing an application (asynchronous)",
    description=(
        "Accepts the job and returns 202 at once with a `run_id` and `status: queued`. The preparation (job analysis, "
        "research, CV tailoring, email draft, grounding check) runs in the background and ends at `awaiting_review`, "
        "with the application stored as `prepared`.\n\n"
        "Flow: 1) POST here. 2) Poll `GET /applications/preparations/{run_id}` every few seconds until `status` is "
        "`awaiting_review` (read `application_id`, then `GET /applications/{application_id}`), `closed`, or `failed` "
        "(read `error`). 3) Review, then approve and send through their own endpoints. Preparing never approves or "
        "sends. A 409 means a run for the same recipient and job title is already active."
    ),
)
async def create_application(
    payload: ApplicationCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    run = preparations.start(db, current_user.id, payload.model_dump())
    background.add_task(preparations.execute, run.id)
    return preparations.status_of(run, utcnow(), get_settings().preparation_stale_seconds)


@router.get(
    "/preparations/{run_id}",
    summary="Status of a background preparation",
    description=(
        "`run_id` is the random token returned by `POST /applications`, not a sequential number. Only the user who "
        "started the run can read it: for anyone else the answer is 404, the same as for a run that does not exist.\n\n"
        "`status`: `queued`, `running`, `awaiting_review` (the application is ready to review), `closed` (the workflow "
        "record is finished), or `failed` (see `error`). `step` names the graph node in progress. `stalled` is true "
        "when the run has sent no heartbeat for `PREPARATION_STALE_SECONDS`. Stalled runs are recovered at startup.\n\n"
        "Poll every few seconds. Stop when the status is `awaiting_review`, `closed` or `failed`."
    ),
)
def get_preparation(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    run = preparations.find_run(db, current_user.id, run_id)
    if run is None:
        raise http_error(NotFoundError("Preparation not found"))
    status = preparations.status_of(run, utcnow(), get_settings().preparation_stale_seconds)
    if run.application_id is not None:
        application = db.get(models.Application, run.application_id)
        if application is not None:
            status["grounding"] = _grounding_status(run.status, service.problems_of(application))
    return status


def _grounding_status(run_status: str, problems: list[dict[str, str]]) -> dict[str, Any]:
    """What the review still has to fix. A prepared application with problems left has used its revision budget:
    the graph only stops with problems when the revisions are exhausted. Mandatory parts are never revised by
    the model."""
    revisable = [problem for problem in problems if problem["kind"] != "mandatory"]
    revision_limit_reached = run_status == "awaiting_review" and bool(revisable)
    if revision_limit_reached:
        message = (
            f"The model's revisions are used up and {len(revisable)} grounding "
            f"{'problem remains' if len(revisable) == 1 else 'problems remain'}: "
            "fix them in the review before approval."
        )
    elif problems:
        message = f"{len(problems)} grounding problem(s): the mandatory parts of the CV are missing from the master CV."
    else:
        message = "No grounding problem."
    return {
        "problems": len(problems),
        "revision_limit_reached": revision_limit_reached,
        "message": message,
    }


@router.get("")
def list_applications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    rows = (
        db.query(models.Application)
        .filter(models.Application.user_id == current_user.id)
        .order_by(models.Application.created_at.desc())
        .all()
    )
    return [_out(row) for row in rows]


@router.get("/{application_id}")
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    return _out(_owned(db, current_user, application_id))


@router.get("/{application_id}/cv.pdf")
def preview_pdf(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> Response:
    """The frozen PDF: the same bytes that are attached when the application is sent."""
    pdf = service.frozen_pdf(_owned(db, current_user, application_id))
    if pdf is None:
        raise http_error(NotFoundError("No PDF for this application"))
    return Response(content=pdf, media_type="application/pdf", headers={"Cache-Control": "no-store"})


@router.post("/{application_id}/approve")
def approve_application(
    application_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """The approval gate. It is complete on its own; resuming the graph afterwards only closes its workflow record."""
    application = service.approve(db, _owned(db, current_user, application_id))
    review = (
        db.query(models.PreparationRun)
        .filter(
            models.PreparationRun.application_id == application.id,
            models.PreparationRun.status == "awaiting_review",
        )
        .first()
    )
    if review is not None:
        background.add_task(preparations.resume_after_approval, review.id)
    return _out(application)


@router.patch("/{application_id}")
def edit_application(
    application_id: int,
    payload: ApplicationEdit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    application = service.edit(
        db,
        _owned(db, current_user, application_id),
        subject=payload.subject,
        body=payload.body,
        recipient_email=payload.recipient_email,
        cv_lines=payload.cv_lines,
    )
    return _out(application)


@router.post("/{application_id}/send")
async def send_application(
    application_id: int,
    payload: SendRequest | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    mailer: Mailer = Depends(get_mailer),
) -> dict[str, object]:
    application = _owned(db, current_user, application_id)
    confirm = str(payload.confirm_recipient) if payload and payload.confirm_recipient else None
    return await service.send(db, application, mailer, utcnow(), confirm_recipient=confirm)


@router.post("/{application_id}/resolve")
def resolve_application(
    application_id: int,
    payload: ResolveRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    application = service.resolve(
        db,
        _owned(db, current_user, application_id),
        outcome=payload.outcome,
        note=payload.note,
        now=utcnow(),
    )
    return _out(application)
