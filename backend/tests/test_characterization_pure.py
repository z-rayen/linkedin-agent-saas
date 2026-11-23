"""Characterization tests: pin the current behaviour of pure helper functions.

These tests do not need PostgreSQL or any external API. They document what the
prototype does today so the refactoring in later steps can be checked against it.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from linkedin_agent.services import agent_service
from linkedin_agent.services.apply_service import find_application_email

# The planner now calls the LLM through the provider class; patching its method keeps the test provider-agnostic.
LLM_COMPLETE = "linkedin_agent.infra.llm.GroqClient.complete"


# --- apply_service.find_application_email -----------------------------------


def test_email_prefers_recruitment_addresses() -> None:
    html = '<a href="mailto:ceo@acme.example">x</a> <a>jobs@acme.example</a>'
    assert find_application_email(html) == "jobs@acme.example"


def test_email_ignores_noreply_and_asset_like_matches() -> None:
    html = "noreply@acme.example logo@2x.png sentry@acme.example"
    assert find_application_email(html) is None


def test_email_is_lowercased_and_deduplicated() -> None:
    html = "HR@acme.example hr@acme.example"
    assert find_application_email(html) == "hr@acme.example"


def test_email_returns_none_when_page_has_no_address() -> None:
    assert find_application_email("<p>No contact here</p>") is None


# --- agent_service ----------------------------------------------------------


def test_format_history_keeps_last_messages_and_truncates() -> None:
    class Msg:
        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    history = [Msg("user", "a" * 400)] + [Msg("assistant", f"m{i}") for i in range(4)]
    formatted = agent_service._format_history(history)
    lines = formatted.split("\n")
    assert len(lines) == 4
    assert lines[0] == "assistant: m0"
    assert "a" * 400 not in formatted


def test_plan_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = AsyncMock(return_value='{"intent": "search_and_post", "queries": ["q1", "q2"]}')
    monkeypatch.setattr(LLM_COMPLETE, fake)
    result = asyncio.run(agent_service.plan("req", {"field": "F", "level": "L"}, []))
    assert result == {"intent": "search_and_post", "queries": ["q1", "q2"]}


def test_plan_falls_back_to_raw_request_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LLM_COMPLETE, AsyncMock(return_value="not json"))
    result = asyncio.run(agent_service.plan("my request", {"field": "F", "level": "L"}, []))
    assert result == {"intent": "search", "queries": ["my request"]}


def test_plan_normalises_unknown_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = AsyncMock(return_value='{"intent": "delete_everything", "queries": ["q"]}')
    monkeypatch.setattr(LLM_COMPLETE, fake)
    result = asyncio.run(agent_service.plan("req", {"field": "F", "level": "L"}, []))
    assert result["intent"] == "search"
