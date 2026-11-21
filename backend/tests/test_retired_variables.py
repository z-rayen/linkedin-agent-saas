"""Retired variables: settings ignores them, so the application must say so. Pure, except the logging check."""

import logging

import pytest

from linkedin_agent import main
from linkedin_agent.core.settings import retired_variable_warnings


def test_no_warning_when_no_retired_variable_is_present() -> None:
    assert retired_variable_warnings({"FRONTEND_ORIGIN": "http://localhost:5500"}, {}) == []


def test_frontend_url_in_the_environment_names_its_replacement() -> None:
    (message,) = retired_variable_warnings({"FRONTEND_URL": "http://localhost:5500"}, {})
    assert "FRONTEND_URL" in message
    assert "FRONTEND_ORIGIN" in message
    assert "environment" in message


def test_cors_origins_in_the_env_file_names_its_replacement() -> None:
    (message,) = retired_variable_warnings({}, {"CORS_ORIGINS": '["http://localhost:5500"]'})
    assert "CORS_ORIGINS" in message
    assert "FRONTEND_ORIGIN" in message
    assert ".env file" in message


def test_a_variable_in_both_places_is_reported_once_with_both_sources() -> None:
    (message,) = retired_variable_warnings({"FRONTEND_URL": "x"}, {"FRONTEND_URL": "x"})
    assert "environment and .env file" in message


def test_an_empty_retired_variable_is_still_reported() -> None:
    assert len(retired_variable_warnings({"FRONTEND_URL": ""}, {})) == 1


def test_the_startup_check_logs_a_warning_for_a_retired_variable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5500")
    monkeypatch.setenv("LINKEDIN_AGENT_NO_ENV_FILE", "1")
    caplog.set_level(logging.WARNING, logger=main.logger.name)
    main.warn_about_retired_variables()
    assert any("FRONTEND_URL" in record.getMessage() and record.levelno == logging.WARNING for record in caplog.records)


def test_the_startup_check_is_silent_when_nothing_is_retired(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("LINKEDIN_AGENT_NO_ENV_FILE", "1")
    caplog.set_level(logging.WARNING, logger=main.logger.name)
    main.warn_about_retired_variables()
    assert not [record for record in caplog.records if "no longer read" in record.getMessage()]
