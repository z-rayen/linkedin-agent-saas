"""Outgoing mail behind a provider interface: message building, address validation, and SMTP delivery.

Rules enforced here, whatever the caller does:
- headers cannot carry CR or LF (header injection), and addresses must be valid;
- TLS is mandatory: plaintext SMTP is refused, certificates are verified;
- every network operation has a timeout;
- credentials are SecretStr and are never logged; addresses are not logged either.
"""

import asyncio
import logging
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from email_validator import EmailNotValidError, validate_email

from ..core.exceptions import ExternalServiceError, ValidationFailedError
from ..core.settings import Settings, get_settings

logger = logging.getLogger(__name__)
IMPLICIT_TLS_PORT = 465
_CONTROL_CHARS = re.compile(r"[\r\n\x00]")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class OutgoingMessage:
    to: str
    subject: str
    body: str
    attachment: bytes | None = None
    attachment_name: str = "cv.pdf"
    original_to: str | None = None  # set when the message was redirected; the intended recipient
    # Generated and stored before the send starts, so a send that dies midway can still be traced.
    message_id: str | None = None


@dataclass(frozen=True)
class SendResult:
    dry_run: bool
    message_id: str | None


class Mailer(Protocol):
    async def send(self, message: OutgoingMessage) -> SendResult: ...


class DryRunMailer:
    """Checks the message and sends nothing. Logs no address, only that a dry run happened."""

    async def send(self, message: OutgoingMessage) -> SendResult:
        validated_header(message.subject, "subject")
        validate_address(message.to)
        logger.info("dry-run: message validated, nothing sent")
        return SendResult(dry_run=True, message_id=None)


def validated_header(value: str, field: str) -> str:
    """A header value must be one line. CR, LF or NUL would let a caller add headers of its own."""
    if _CONTROL_CHARS.search(value):
        raise ValidationFailedError(f"{field} contains a line break or control character")
    return value.strip()


def validate_address(address: str) -> str:
    """A syntactically valid, single-line address. Returns the normalised form."""
    validated_header(address, "address")
    try:
        return validate_email(address, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValidationFailedError("invalid email address") from exc


def new_message_id(sender: str) -> str:
    """A fresh RFC 5322 Message-ID for `sender`'s domain. Create it before sending, store it, then use it."""
    return make_msgid(domain=sender.rsplit("@", 1)[-1])


def build_message(sender: str, message: OutgoingMessage) -> EmailMessage:
    """The RFC 5322 message. Every header value is validated before it is set."""
    email = EmailMessage()
    email["From"] = validate_address(sender)
    email["To"] = validate_address(message.to)
    email["Subject"] = validated_header(message.subject, "subject")
    email["Date"] = formatdate(localtime=False, usegmt=True)
    email["Message-ID"] = message.message_id or new_message_id(sender)
    if message.original_to:
        email["X-Original-Recipient"] = validate_address(message.original_to)
    email.set_content(message.body, charset="utf-8")
    if message.attachment is not None:
        filename = _SAFE_FILENAME.sub("_", message.attachment_name) or "attachment.pdf"
        email.add_attachment(message.attachment, maintype="application", subtype="pdf", filename=filename)
    return email


class SmtpMailer:
    """Real delivery over SMTP with mandatory TLS. Each send opens and closes one connection."""

    def __init__(self, settings: Settings, connect: Callable[..., smtplib.SMTP] | None = None) -> None:
        self._settings = settings
        self._connect = connect

    async def send(self, message: OutgoingMessage) -> SendResult:
        email = build_message(self._sender(), message)
        message_id = str(email["Message-ID"])
        await asyncio.to_thread(self._deliver, email)
        return SendResult(dry_run=False, message_id=message_id)

    def _sender(self) -> str:
        sender = self._settings.smtp_from_address or self._settings.smtp_username
        if not sender:
            raise ExternalServiceError("SMTP sender address is not configured")
        return sender

    def _open(self) -> smtplib.SMTP:
        s = self._settings
        if not s.smtp_host:
            raise ExternalServiceError("SMTP host is not configured")
        if self._connect is not None:
            return self._connect(s.smtp_host, s.smtp_port, s.smtp_timeout_seconds)
        context = ssl.create_default_context()
        if s.smtp_port == IMPLICIT_TLS_PORT:
            return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=context)
        return smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)

    def _deliver(self, email: EmailMessage) -> None:
        s = self._settings
        implicit_tls = s.smtp_port == IMPLICIT_TLS_PORT
        if not implicit_tls and not s.smtp_use_starttls:
            raise ExternalServiceError("refusing plaintext SMTP: enable STARTTLS or use port 465")
        try:
            client = self._open()
        except (OSError, smtplib.SMTPException) as exc:
            logger.error("smtp connection failed error_type=%s", type(exc).__name__)
            raise ExternalServiceError("SMTP connection failed") from exc
        try:
            client.ehlo()
            if not implicit_tls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if s.smtp_username:
                client.login(s.smtp_username, s.smtp_password.get_secret_value())
            client.send_message(email)
        except (OSError, smtplib.SMTPException) as exc:
            # The server's own text is not logged: it can echo fragments of the exchange.
            logger.error("smtp delivery failed error_type=%s", type(exc).__name__)
            raise ExternalServiceError("SMTP delivery failed") from exc
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()


def get_mailer() -> Mailer:
    """The mailer for this process. Dry-run unless MAIL_DRY_RUN is explicitly set to false."""
    settings = get_settings()
    if settings.mail_dry_run:
        return DryRunMailer()
    return SmtpMailer(settings)
