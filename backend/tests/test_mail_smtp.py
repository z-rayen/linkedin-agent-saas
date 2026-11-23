"""SMTP delivery and message building. The SMTP server is a fake injected by the test: no socket is opened."""

import logging
import smtplib
from typing import Any, ClassVar

import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.exceptions import ExternalServiceError, ValidationFailedError
from linkedin_agent.core.settings import Settings
from linkedin_agent.infra.mail import (
    DryRunMailer,
    OutgoingMessage,
    SmtpMailer,
    build_message,
    get_mailer,
    validate_address,
)

SECRET = "pw-SECRET-do-not-log-123"
SENDER = "sender@example.com"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://unused/unused_test",
        "secret_key": "a-long-enough-secret-key",
        "token_encryption_key": Fernet.generate_key().decode(),
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "sender",
        "smtp_password": SECRET,
        "smtp_from_address": SENDER,
        "smtp_use_starttls": True,
    }
    base.update(overrides)
    return Settings(**base)


class FakeSMTP:
    """Records every call. Behaves like smtplib.SMTP for the methods the mailer uses."""

    instances: ClassVar[list["FakeSMTP"]] = []
    fail_with: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.starttls_context: object = None
        self.login_args: tuple[str, str] | None = None
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context: object = None) -> None:
        self.calls.append("starttls")
        self.starttls_context = context

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)

    def send_message(self, email: Any) -> None:
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        self.calls.append("send_message")
        self.sent.append(email)

    def quit(self) -> None:
        self.calls.append("quit")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None


def fake_connect(host: str, port: int, timeout: float) -> FakeSMTP:
    return FakeSMTP()


def message(**overrides: Any) -> OutgoingMessage:
    base: dict[str, Any] = {
        "to": "hr@acme.example",
        "subject": "Application",
        "body": "Hello.",
        "attachment": b"%PDF-1.4",
    }
    base.update(overrides)
    return OutgoingMessage(**base)


# --- message building and header safety ----------------------------------------


def test_message_has_the_expected_headers_and_pdf_attachment() -> None:
    email = build_message(SENDER, message())
    assert email["From"] == SENDER
    assert email["To"] == "hr@acme.example"
    assert email["Subject"] == "Application"
    assert email["Message-ID"]
    attachments = list(email.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"


@pytest.mark.parametrize("subject", ["Hi\r\nBcc: evil@example.com", "Hi\nBcc: evil@example.com", "Hi\x00"])
def test_subject_with_line_break_is_refused(subject: str) -> None:
    with pytest.raises(ValidationFailedError):
        build_message(SENDER, message(subject=subject))


@pytest.mark.parametrize("address", ["hr@acme.example\r\nBcc: evil@example.com", "hr@acme.example\nRCPT"])
def test_address_with_line_break_is_refused(address: str) -> None:
    with pytest.raises(ValidationFailedError):
        build_message(SENDER, message(to=address))


@pytest.mark.parametrize("address", ["not-an-email", "user@@acme.example", "a b@acme.example", ""])
def test_invalid_recipient_is_refused(address: str) -> None:
    with pytest.raises(ValidationFailedError):
        validate_address(address)


def test_attachment_name_cannot_escape_the_folder() -> None:
    email = build_message(SENDER, message(attachment_name="../../etc/evil.pdf"))
    filename = next(email.iter_attachments()).get_filename()
    assert filename is not None
    assert "/" not in filename and "\\" not in filename


def test_original_recipient_header_is_added_when_redirected() -> None:
    email = build_message(SENDER, message(to="me@example.com", original_to="hr@acme.example"))
    assert email["X-Original-Recipient"] == "hr@acme.example"


def test_body_is_utf8_for_french_text() -> None:
    email = build_message(SENDER, message(body="Bonjour, je suis disponible à partir du 1er décembre."))
    assert "disponible" in email.get_body(preferencelist=("plain",)).get_content()


# --- delivery ---------------------------------------------------------------------


async def _send(settings: Settings, msg: OutgoingMessage, connect=fake_connect):  # type: ignore[no-untyped-def]
    return await SmtpMailer(settings, connect=connect).send(msg)


def test_delivery_upgrades_to_tls_then_logs_in_then_sends_once() -> None:
    import asyncio

    result = asyncio.run(_send(make_settings(), message()))
    fake = FakeSMTP.instances[0]
    assert fake.calls == ["ehlo", "starttls", "ehlo", "login", "send_message", "quit"]
    assert fake.starttls_context is not None  # certificate verification context is used
    assert fake.login_args == ("sender", SECRET)
    assert result.dry_run is False
    assert result.message_id


def test_plaintext_smtp_is_refused_and_no_connection_is_made() -> None:
    import asyncio

    opened: list[bool] = []

    def spy(host: str, port: int, timeout: float) -> FakeSMTP:
        opened.append(True)
        return FakeSMTP()

    with pytest.raises(ExternalServiceError, match="plaintext"):
        asyncio.run(_send(make_settings(smtp_use_starttls=False), message(), connect=spy))
    assert opened == []


def test_implicit_tls_port_does_not_issue_starttls() -> None:
    import asyncio

    asyncio.run(_send(make_settings(smtp_port=465), message()))
    assert "starttls" not in FakeSMTP.instances[0].calls


def test_smtp_error_is_reported_without_server_text_or_secrets(caplog: pytest.LogCaptureFixture) -> None:
    import asyncio

    FakeSMTP.fail_with = smtplib.SMTPRecipientsRefused({"hr@acme.example": (550, b"echo: " + SECRET.encode())})
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ExternalServiceError) as raised:
        asyncio.run(_send(make_settings(), message()))
    assert raised.value.message == "SMTP delivery failed"
    assert SECRET not in raised.value.message
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    assert all("hr@acme.example" not in record.getMessage() for record in caplog.records)


def test_credentials_are_never_logged_on_success(caplog: pytest.LogCaptureFixture) -> None:
    import asyncio

    caplog.set_level(logging.DEBUG)
    asyncio.run(_send(make_settings(), message()))
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    assert all(SECRET not in str(record.args) for record in caplog.records)


def test_password_is_a_secret_type() -> None:
    settings = make_settings()
    assert SECRET not in repr(settings.smtp_password)
    assert SECRET not in str(settings.smtp_password)
    assert settings.smtp_password.get_secret_value() == SECRET


def test_connection_failure_is_reported_cleanly() -> None:
    import asyncio

    def refused(host: str, port: int, timeout: float) -> FakeSMTP:
        raise OSError("connection refused by 10.0.0.1")

    with pytest.raises(ExternalServiceError, match="connection failed") as raised:
        asyncio.run(_send(make_settings(), message(), connect=refused))
    assert "10.0.0.1" not in raised.value.message


def test_missing_host_is_a_configuration_error() -> None:
    import asyncio

    with pytest.raises(ExternalServiceError, match="host"):
        asyncio.run(_send(make_settings(smtp_host=""), message()))


# --- mailer selection ----------------------------------------------------------------


def test_default_mailer_is_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from linkedin_agent.core import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("MAIL_DRY_RUN", "true")
    try:
        assert isinstance(get_mailer(), DryRunMailer)
    finally:
        settings_module.get_settings.cache_clear()


def test_real_mailer_only_when_dry_run_is_explicitly_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from linkedin_agent.core import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("MAIL_DRY_RUN", "false")
    monkeypatch.setenv("MAIL_REDIRECT_TO", "me@example.com")  # the safety gate needs a redirect or an opt-in
    try:
        assert isinstance(get_mailer(), SmtpMailer)
    finally:
        settings_module.get_settings.cache_clear()


def test_dry_run_mailer_sends_nothing() -> None:
    import asyncio

    result = asyncio.run(DryRunMailer().send(message()))
    assert result.dry_run is True
    assert FakeSMTP.instances == []
