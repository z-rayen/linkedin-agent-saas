"""Application lifecycle, content integrity and sending policy. Pure rules: no I/O, no database, no clock.

The lifecycle is explicit. Nothing reaches `sent` without passing through `approved` and then `sending`,
and `sending` is claimed atomically in the database so a double click cannot send twice.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo


class ApplicationStatus(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    REPLIED = "replied"


TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.PREPARED: frozenset({ApplicationStatus.APPROVED}),
    # Approval can be withdrawn, or the content changed, which sends the application back to prepared.
    ApplicationStatus.APPROVED: frozenset({ApplicationStatus.SENDING, ApplicationStatus.PREPARED}),
    ApplicationStatus.SENDING: frozenset({ApplicationStatus.SENT, ApplicationStatus.FAILED}),
    # No automatic retry: a failure is a decision for the user (fix and re-approve, or give up).
    ApplicationStatus.FAILED: frozenset({ApplicationStatus.PREPARED, ApplicationStatus.APPROVED}),
    ApplicationStatus.SENT: frozenset({ApplicationStatus.REPLIED}),
    ApplicationStatus.REPLIED: frozenset(),
}

EDITABLE_STATUSES = frozenset({ApplicationStatus.PREPARED, ApplicationStatus.APPROVED, ApplicationStatus.FAILED})
# Statuses that count against the sending policy: a claimed application is a send in progress.
SENDING_OR_SENT = frozenset({ApplicationStatus.SENDING, ApplicationStatus.SENT})


class InvalidTransitionError(ValueError):
    """Raised when a status change is not allowed by TRANSITIONS."""


def can_transition(current: ApplicationStatus, target: ApplicationStatus) -> bool:
    return target in TRANSITIONS[current]


def ensure_transition(current: ApplicationStatus, target: ApplicationStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(f"cannot go from {current} to {target}")


def is_editable(status: ApplicationStatus) -> bool:
    return status in EDITABLE_STATUSES


def content_hash(
    subject: str,
    body: str,
    recipient_email: str,
    cv_lines: list[dict[str, object]],
    pdf_sha256: str | None = None,
) -> str:
    """Fingerprint of everything that is sent: the email, the CV lines, and the frozen PDF's sha256.

    Any change to one of them changes the hash. The hash is stored at approval and checked at send time.
    """
    canonical = json.dumps(
        {
            "subject": subject,
            "body": body,
            "recipient": recipient_email.strip().lower(),
            "cv": cv_lines,
            "pdf": pdf_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def local_day_start_utc(now_utc: datetime, tz_name: str) -> datetime:
    """Start of the current day in the configured timezone, expressed as a naive UTC datetime.

    Daily caps follow the user's calendar day (Africa/Tunis by default), not the server's clock.
    `now_utc` is naive UTC, as stored everywhere in the database.
    """
    aware = now_utc.replace(tzinfo=UTC)
    local = aware.astimezone(ZoneInfo(tz_name))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC).replace(tzinfo=None)


def company_key(recipient_email: str, free_mail_domains: Iterable[str]) -> str:
    """Identifier used to detect 'same company' for the sending policy.

    The recipient's domain identifies the employer (jobs@acme.com -> acme.com). Free-mail
    domains identify a person, not a company, so for them the full address is used instead.
    Company names typed by users or scraped from pages are deliberately NOT used: they are unreliable.
    """
    address = recipient_email.strip().lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    if not domain or _is_free_mail(domain, free_mail_domains):
        return address
    return domain


def _is_free_mail(domain: str, free_mail_domains: Iterable[str]) -> bool:
    for pattern in free_mail_domains:
        if pattern.endswith(".*"):
            base = pattern[:-2]
            if domain == base or domain.startswith(base + "."):
                return True
        elif domain == pattern:
            return True
    return False


@dataclass(frozen=True)
class SendContext:
    """Facts the sending policy needs. The service layer computes them from the database and the clock."""

    sent_today: int
    daily_cap: int
    seconds_since_last_send: float | None
    min_delay_seconds: float
    recipient_already_sent: bool
    company_sent_recently: bool


def send_violations(context: SendContext) -> list[str]:
    """Reasons why a send must not happen now. An empty list means the send is allowed."""
    violations: list[str] = []
    if context.sent_today >= context.daily_cap:
        violations.append(f"daily cap of {context.daily_cap} sends reached")
    if context.seconds_since_last_send is not None and context.seconds_since_last_send < context.min_delay_seconds:
        violations.append(f"wait {context.min_delay_seconds:.0f}s between two sends")
    if context.recipient_already_sent:
        violations.append("this recipient already received an application")
    if context.company_sent_recently:
        violations.append("an application was already sent to this company recently")
    return violations
