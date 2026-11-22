"""Structured LLM output: what is logged when an answer is rejected, and what the retry is told. No network."""

import asyncio
import logging

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, ValidationError

from linkedin_agent.core.exceptions import LLMOutputError
from linkedin_agent.core.settings import Settings
from linkedin_agent.cv.models import TailoredCV
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.llm import (
    GroqClient,
    complete_structured,
    correction_for,
    describe_validation_errors,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def _errors(model: type[BaseModel], raw: str) -> ValidationError:
    try:
        model.model_validate_json(raw)
    except ValidationError as exc:
        return exc
    raise AssertionError("the input was expected to be rejected")


def test_problems_are_field_paths_and_error_types() -> None:
    raw = '{"lines": [{"section": "education", "text": "x", "source_ids": []}]}'
    problems = describe_validation_errors(_errors(TailoredCV, raw), TailoredCV)
    assert "lines.0.section: literal_error" in problems
    assert "lines.0.source_ids: too_short" in problems


def test_malformed_json_is_reported_as_json_invalid_at_the_root() -> None:
    problems = describe_validation_errors(_errors(TailoredCV, "Here is your CV, enjoy!"), TailoredCV)
    assert problems == ["(root): json_invalid"]


def test_missing_field_is_reported_as_missing() -> None:
    problems = describe_validation_errors(_errors(TailoredCV, '{"lines": [{"section": "skill"}]}'), TailoredCV)
    assert "lines.0.text: missing" in problems
    assert "lines.0.source_ids: missing" in problems


def test_model_supplied_keys_and_values_never_reach_the_description() -> None:
    secret_key = "my-secret-candidate-key"
    problems = describe_validation_errors(
        _errors(_Strict, f'{{"value": "a secret answer", "{secret_key}": 1}}'), _Strict
    )
    joined = "\n".join(problems)
    assert secret_key not in joined
    assert "a secret answer" not in joined
    assert "<unknown>: extra_forbidden" in joined
    assert "value: int_parsing" in joined


def test_the_correction_lists_problems_and_not_the_rejected_answer() -> None:
    text = correction_for(["lines.0.section: literal_error"])
    assert "- lines.0.section: literal_error" in text
    assert "Return the complete answer again" in text


class _Replies:
    """A fake LLM that answers with the next reply and records every prompt it was sent."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._replies[min(len(self.prompts), len(self._replies)) - 1]


def test_the_retry_carries_the_problems_back_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = '{"lines": [{"section": "Experience", "text": "SECRET-REJECTED-TEXT", "source_ids": ["exp-1"]}]}'
    accepted = '{"lines": [{"section": "experience", "text": "Built a service", "source_ids": ["exp-1"]}]}'
    fake = _Replies([rejected, accepted])
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: fake)

    result = asyncio.run(complete_structured("ORIGINAL PROMPT", TailoredCV, retries=1))

    assert result.lines[0].section == "experience"
    assert len(fake.prompts) == 2
    assert fake.prompts[0] == "ORIGINAL PROMPT"
    assert fake.prompts[1].startswith("ORIGINAL PROMPT")
    assert "lines.0.section: literal_error" in fake.prompts[1]
    assert "SECRET-REJECTED-TEXT" not in fake.prompts[1]  # the rejected answer is never sent back


def test_rejections_are_logged_with_paths_and_types_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rejected = '{"lines": [{"section": "education", "text": "SECRET-CV-LINE", "source_ids": ["edu-1"]}]}'
    fake = _Replies([rejected, rejected])
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: fake)

    with caplog.at_level(logging.WARNING, logger="linkedin_agent.infra.llm"), pytest.raises(LLMOutputError):
        asyncio.run(complete_structured("prompt", TailoredCV, retries=1))

    assert "lines.0.section: literal_error" in caplog.text
    assert "attempt=2" in caplog.text
    assert "SECRET-CV-LINE" not in caplog.text
    assert "edu-1" not in caplog.text


def test_a_cut_answer_is_logged_as_truncated(caplog: pytest.LogCaptureFixture) -> None:
    def cut_answer(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"finish_reason": "length", "message": {"content": '{"lines": ['}}]}
        return httpx.Response(200, json=body)

    settings = Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
    )
    client = GroqClient(settings, transport=httpx.MockTransport(cut_answer))
    with caplog.at_level(logging.WARNING, logger="linkedin_agent.infra.llm"):
        assert asyncio.run(client.complete("prompt")) == '{"lines": ['
    assert "cut at max_tokens" in caplog.text
