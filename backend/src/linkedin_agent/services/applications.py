"""Application use cases: prepare, edit, approve, send, resolve. Every rule here is enforced server-side."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models
from ..clock import utcnow
from ..core.exceptions import (
    AppError,
    ContentChangedError,
    CVNotGroundedError,
    InvalidStateError,
    SendPolicyViolationError,
    ValidationFailedError,
)
from ..core.settings import get_settings
from ..cv.build import build_cv_pdf
from ..cv.email import with_signature
from ..cv.grounding import GroundingProblem, verify_cv, verify_email
from ..cv.models import MasterCV, TailoredCV, TailoredLine
from ..domain.applications import (
    SENDING_OR_SENT,
    ApplicationStatus,
    InvalidTransitionError,
    SendContext,
    company_key,
    content_hash,
    ensure_transition,
    is_editable,
    local_day_start_utc,
    send_violations,
)
from ..infra.mail import DryRunMailer, Mailer, OutgoingMessage, new_message_id, validate_address

DisplayStatus = Literal["prepared", "approved", "sending", "needs_verification", "sent", "failed", "replied"]


# --- profile and master CV ----------------------------------------------------


def _profile_for(db: Session, user_id: int) -> tuple[dict[str, str], str | None]:
    """Profile fields (with defaults) and the stored master CV JSON, if any."""
    settings = get_settings()
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == user_id).first()
    profile = {
        "field": str(row.field) if row and row.field else settings.default_field,
        "skills": str(row.skills) if row and row.skills else settings.default_skills,
        "level": str(row.level) if row and row.level else settings.default_level,
    }
    master_json = str(row.master_cv_json) if row and row.master_cv_json else None
    return profile, master_json


def save_master_cv(db: Session, user_id: int, master: MasterCV) -> None:
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == user_id).first()
    if row is None:
        row = models.ProfileConfig(user_id=user_id)
        db.add(row)
    row.master_cv_json = master.model_dump_json()
    db.commit()


def _master_for(db: Session, user_id: int) -> MasterCV:
    _, master_json = _profile_for(db, user_id)
    if not master_json:
        raise ValidationFailedError("Upload your master CV first (PUT /applications/master-cv)")
    return MasterCV.model_validate_json(master_json)


# --- content, grounding and the frozen PDF ------------------------------------------


LEGACY_SECTION = "summary"  # CVs stored before the model stopped writing a summary: the code now builds the header


def tailored_cv_of(application: models.Application) -> TailoredCV | None:
    if not application.tailored_cv_json:
        return None
    raw = json.loads(application.tailored_cv_json)
    raw["lines"] = [line for line in raw.get("lines", []) if line.get("section") != LEGACY_SECTION]
    return TailoredCV.model_validate(raw)


def problems_of(application: models.Application) -> list[dict[str, str]]:
    """The stored problems, each one a dict with kind, location, term and message. Problems stored as plain text by an
    earlier version are returned with kind "legacy", never dropped."""
    stored = json.loads(application.grounding_problems or "[]")
    problems: list[dict[str, str]] = []
    for item in stored:
        if isinstance(item, dict):
            problems.append({key: str(item.get(key, "")) for key in ("kind", "location", "term", "message")})
        else:
            problems.append({"kind": "legacy", "location": "", "term": "", "message": str(item)})
    return problems


def _cv_lines_payload(cv: TailoredCV | None) -> list[dict[str, object]]:
    return [line.model_dump() for line in cv.lines] if cv else []


def _job_context(application: models.Application) -> str:
    return " ".join(part for part in (application.job_title, application.company or "") if part)


def _current_hash(application: models.Application) -> str:
    return content_hash(
        application.subject,
        application.body,
        application.recipient_email,
        _cv_lines_payload(tailored_cv_of(application)),
        application.cv_pdf_sha256,
    )


def _grounding_problems(application: models.Application, master: MasterCV) -> list[GroundingProblem]:
    """Every problem in the CV, its mandatory parts, and the email body, checked against the facts the CV cites."""
    cv = tailored_cv_of(application)
    if cv is None:
        return [GroundingProblem("CV: no tailored CV stored", kind="missing_cv", location="CV")]
    cv_problems = verify_cv(cv, master)
    email_problems = verify_email(application.subject, application.body, cv, master, _job_context(application))
    return cv_problems + email_problems


def _refresh_grounding(application: models.Application, master: MasterCV) -> None:
    application.grounding_problems = json.dumps(
        [problem.as_dict() for problem in _grounding_problems(application, master)]
    )


def _freeze_pdf(application: models.Application, master: MasterCV) -> None:
    """Compile the CV now and keep exactly these bytes. A broken LaTeX template fails here, loudly."""
    cv = tailored_cv_of(application)
    if cv is None:
        raise ValidationFailedError("no tailored CV stored")
    pdf = build_cv_pdf(master, cv)
    application.cv_pdf = pdf
    application.cv_pdf_sha256 = hashlib.sha256(pdf).hexdigest()


def frozen_pdf(application: models.Application) -> bytes | None:
    """The PDF the preview shows. After approval it is the file that will be attached."""
    return bytes(application.cv_pdf) if application.cv_pdf is not None else None


def _reset_approval(application: models.Application) -> None:
    """Content changed after approval: the application goes back to prepared and must be approved again."""
    if application.status == ApplicationStatus.APPROVED.value:
        application.status = ApplicationStatus.PREPARED.value
    application.approved_at = None
    application.approved_content_hash = None


# --- prepare and edit -------------------------------------------------------------


def persist_prepared(db: Session, state: dict[str, object]) -> int:
    """Store what the application graph prepared, as a prepared application, and freeze its PDF.

    This is storage only: it never approves, never sends. The graph calls it once a draft exists, whatever its
    grounding result, so a refused CV is kept with its problems instead of being thrown away.
    """
    settings = get_settings()
    recipient = validate_address(str(state["recipient_email"]))
    master = MasterCV.model_validate_json(str(state["master_json"]))
    application = models.Application(
        user_id=int(str(state["user_id"])),
        company=str(state["company"]) if state.get("company") else None,
        job_title=str(state["job_title"]),
        recipient_email=recipient,
        subject=str(state["subject"]),
        body=with_signature(str(state["body"]), master),
        status=ApplicationStatus.PREPARED.value,
        tailored_cv_json=str(state["cv_json"]),
        grounding_problems=json.dumps(list(state.get("problems", []))),  # type: ignore[call-overload]
        company_key=company_key(recipient, settings.free_mail_domains),
    )
    _freeze_pdf(application, master)
    db.add(application)
    db.commit()
    db.refresh(application)
    return int(application.id)


def edit(
    db: Session,
    application: models.Application,
    *,
    subject: str | None = None,
    body: str | None = None,
    recipient_email: str | None = None,
    cv_lines: list[TailoredLine] | None = None,
) -> models.Application:
    """Change the email or the CV. Grounding and the PDF are redone. An approved application goes back to prepared."""
    if not is_editable(ApplicationStatus(application.status)):
        raise InvalidStateError(f"application is {application.status} and cannot be edited")
    master = _master_for(db, application.user_id)
    settings = get_settings()

    if subject is not None:
        application.subject = subject
    if body is not None:
        application.body = with_signature(body, master)  # the signature is added by the code, never typed by the user
    if recipient_email is not None:
        application.recipient_email = validate_address(recipient_email)
        application.company_key = company_key(application.recipient_email, settings.free_mail_domains)
    if cv_lines is not None:
        application.tailored_cv_json = TailoredCV(lines=cv_lines).model_dump_json()

    try:
        _refresh_grounding(application, master)
        _freeze_pdf(application, master)
    except Exception:
        db.rollback()  # a half-applied edit must never be committed later
        raise
    _reset_approval(application)
    db.commit()
    db.refresh(application)
    return application


# --- approval -------------------------------------------------------------------


def _transition(application: models.Application, target: ApplicationStatus) -> None:
    try:
        ensure_transition(ApplicationStatus(application.status), target)
    except InvalidTransitionError as exc:
        raise InvalidStateError(f"application is {application.status}: {exc}") from exc


def _refusal_message(problems: list[dict[str, str]]) -> str:
    """A count, never a truncated list: the complete list travels in the error's `problems` field."""
    count = len(problems)
    noun = "problem" if count == 1 else "problems"
    return f"not approved: {count} grounding {noun}; each one is listed in problems"


def approve(db: Session, application: models.Application) -> models.Application:
    """The human approval gate: grounding is run again, the PDF is compiled and frozen, the content is fingerprinted."""
    _transition(application, ApplicationStatus.APPROVED)
    master = _master_for(db, application.user_id)
    _refresh_grounding(application, master)
    problems = problems_of(application)
    if problems:
        db.commit()
        raise CVNotGroundedError(_refusal_message(problems), problems)
    try:
        _freeze_pdf(application, master)
    except Exception:
        db.rollback()
        raise
    application.status = ApplicationStatus.APPROVED.value
    application.approved_at = utcnow()
    application.approved_content_hash = _current_hash(application)
    db.commit()
    return application


# --- sending: policy, claim, and recovery ----------------------------------------------


def send_context(db: Session, application: models.Application, now: datetime) -> SendContext:
    """The facts the sending policy needs. A claimed ('sending') application counts as sent."""
    settings = get_settings()
    counted = db.query(models.Application).filter(
        models.Application.user_id == application.user_id,
        models.Application.status.in_([status.value for status in SENDING_OR_SENT]),
    )
    day_start = local_day_start_utc(now, settings.mail_timezone)
    cooldown_start = now - timedelta(days=settings.mail_company_cooldown_days)
    sent_only = counted.filter(models.Application.sent_at.isnot(None))
    last = sent_only.order_by(models.Application.sent_at.desc()).first()
    seconds_since = (now - last.sent_at).total_seconds() if last and last.sent_at else None
    return SendContext(
        sent_today=counted.filter(
            or_(models.Application.sent_at >= day_start, models.Application.sent_at.is_(None))
        ).count(),
        daily_cap=settings.mail_daily_cap,
        seconds_since_last_send=seconds_since,
        min_delay_seconds=settings.mail_min_delay_seconds,
        recipient_already_sent=counted.filter(models.Application.recipient_email == application.recipient_email).count()
        > 0,
        company_sent_recently=counted.filter(
            models.Application.company_key == application.company_key,
            models.Application.sent_at.isnot(None),
            models.Application.sent_at >= cooldown_start,
        ).count()
        > 0,
    )


def claim_for_sending(db: Session, application_id: int, message_id: str, now: datetime) -> bool:
    """Atomically move an approved application to sending, and record the Message-ID and the start time.

    One conditional UPDATE: of two concurrent callers, exactly one sees the row still approved.
    """
    claimed = (
        db.query(models.Application)
        .filter(
            models.Application.id == application_id,
            models.Application.status == ApplicationStatus.APPROVED.value,
        )
        .update(
            {
                "status": ApplicationStatus.SENDING.value,
                "smtp_message_id": message_id,
                "sending_started_at": now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return claimed == 1  # the UPDATE's rowcount: 1 for the winner, 0 for everyone else


def needs_verification(application: models.Application, now: datetime) -> bool:
    """A send that started and never finished. It is not retried and not marked sent: a human decides."""
    if application.status != ApplicationStatus.SENDING.value or application.sending_started_at is None:
        return False
    started: datetime = application.sending_started_at
    timeout = timedelta(seconds=get_settings().mail_sending_timeout_seconds)
    return now - started >= timeout


def display_status(application: models.Application, now: datetime) -> DisplayStatus:
    if needs_verification(application, now):
        return "needs_verification"
    return application.status  # type: ignore[return-value]


def _safe_error_text(exc: Exception) -> str:
    return exc.message if isinstance(exc, AppError) else "unexpected error while sending"


async def send(
    db: Session,
    application: models.Application,
    mailer: Mailer,
    now: datetime,
    *,
    confirm_recipient: str | None,
) -> dict[str, object]:
    """Send an approved application. Order: state, content fingerprint, grounding, confirmation, policy,
    then the atomic claim. The Message-ID is stored with the claim, before the network is touched.
    A failure is not retried: it is marked failed for the user to decide."""
    settings = get_settings()
    real = not isinstance(mailer, DryRunMailer)

    if application.status != ApplicationStatus.APPROVED.value:
        raise InvalidStateError(f"application is {application.status}, only an approved application can be sent")

    if application.approved_content_hash != _current_hash(application):
        _reset_approval(application)
        db.commit()
        raise ContentChangedError("the content changed after approval: it must be approved again")

    master = _master_for(db, application.user_id)
    _refresh_grounding(application, master)
    problems = problems_of(application)
    if problems:
        _reset_approval(application)
        db.commit()
        raise CVNotGroundedError(_refusal_message(problems), problems)

    if real and (confirm_recipient or "").strip().lower() != application.recipient_email.lower():
        raise ValidationFailedError("confirm_recipient must equal the recipient address to send for real")

    violations = send_violations(send_context(db, application, now))
    if violations:
        raise SendPolicyViolationError("; ".join(violations))

    if not real:
        result = await mailer.send(
            OutgoingMessage(to=application.recipient_email, subject=application.subject, body=application.body)
        )
        return {"status": application.status, "dry_run": result.dry_run, "would_send_to": application.recipient_email}

    if application.cv_pdf is None or application.cv_pdf_sha256 is None:
        raise ContentChangedError("no frozen PDF for this application: it must be approved again")
    pdf = bytes(application.cv_pdf)
    if hashlib.sha256(pdf).hexdigest() != application.cv_pdf_sha256:
        raise ContentChangedError("the stored PDF does not match its fingerprint: it must be approved again")

    sender = settings.smtp_from_address or settings.smtp_username or "agent@localhost"
    message_id = new_message_id(sender)
    if not claim_for_sending(db, application.id, message_id, now):
        raise InvalidStateError("this application is already being sent")
    db.refresh(application)

    redirect = settings.mail_redirect_to.strip() or None
    try:
        subject = f"[redirected] {application.subject}" if redirect else application.subject
        result = await mailer.send(
            OutgoingMessage(
                to=redirect or application.recipient_email,
                subject=subject,
                body=application.body,
                attachment=pdf,
                original_to=application.recipient_email if redirect else None,
                message_id=message_id,
            )
        )
    except Exception as exc:
        application.status = ApplicationStatus.FAILED.value
        application.last_error = _safe_error_text(exc)
        db.commit()
        raise

    application.status = ApplicationStatus.SENT.value
    application.sent_at = now
    db.commit()
    return {
        "status": application.status,
        "dry_run": False,
        "message_id": message_id,
        "redirected": bool(redirect),
    }


def resolve(
    db: Session,
    application: models.Application,
    *,
    outcome: Literal["sent", "failed"],
    note: str,
    now: datetime,
) -> models.Application:
    """The user's decision on a send that needs verification: did it reach the server, or not?

    Only a send that is past the timeout can be resolved, so an in-flight send is never touched.
    """
    if application.status != ApplicationStatus.SENDING.value:
        raise InvalidStateError(f"application is {application.status}: only a sending application can be resolved")
    if not needs_verification(application, now):
        raise InvalidStateError("the send is not stuck yet: wait for the timeout before resolving it")
    application.last_error = f"manual resolution ({outcome}): {note.strip()[:500]}"
    if outcome == "sent":
        application.status = ApplicationStatus.SENT.value
        application.sent_at = application.sending_started_at
    else:
        application.status = ApplicationStatus.FAILED.value
    db.commit()
    db.refresh(application)
    return application
