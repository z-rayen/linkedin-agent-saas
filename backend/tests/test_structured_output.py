"""Structured LLM output: what is logged when an answer is rejected, and what the retry is told. No network."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, ValidationError

from linkedin_agent.core.exceptions import (
    CV_STRUCTURE_MESSAGE,
    CVStructureError,
    LLMOutputError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from linkedin_agent.core.settings import LLMStepOverride, Settings
from linkedin_agent.cv.models import TailoredCV
from linkedin_agent.cv.pipeline import load_master_cv, tailor_cv
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.llm import (
    GroqClient,
    complete_structured,
    correction_for,
    describe_validation_errors,
    resolve_step,
)
from linkedin_agent.infra.rate_limit import TokenBudget

SAMPLE_MASTER = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"
VALID_CV = '{"lines": [{"section": "skill", "text": "Python", "source_ids": ["skill-py"]}]}'


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

    async def complete(self, prompt: str, **options: object) -> str:
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


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://unused/unused_test",
        "secret_key": "a-long-enough-secret-key",
        "token_encryption_key": Fernet.generate_key().decode(),
        "groq_api_key": "test-key",
        "llm_max_output_tokens": 2048,
        "llm_output_token_ceiling": 4096,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class _Provider:
    """A fake Groq endpoint. Each reply is a function of the request body; every request body is recorded."""

    def __init__(self, replies: list[Callable[[dict[str, object]], httpx.Response]]) -> None:
        self._replies = replies
        self.bodies: list[dict[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, object] = json.loads(request.content)
        self.bodies.append(body)
        return self._replies[min(len(self.bodies), len(self._replies)) - 1](body)

    @property
    def caps(self) -> list[object]:
        return [body["max_tokens"] for body in self.bodies]


def _answered(content: str) -> Callable[[dict[str, object]], httpx.Response]:
    def reply(_: dict[str, object]) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    return reply


def _cut_off() -> Callable[[dict[str, object]], httpx.Response]:
    def reply(_: dict[str, object]) -> httpx.Response:
        body = {"choices": [{"finish_reason": "length", "message": {"content": '{"lines": ['}}]}
        return httpx.Response(200, json=body)

    return reply


def _wire(monkeypatch: pytest.MonkeyPatch, settings: Settings, provider: _Provider) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        llm_module,
        "get_llm_client",
        lambda: GroqClient(settings, transport=httpx.MockTransport(provider), budget=TokenBudget(8000)),
    )


def test_a_cut_answer_is_retried_on_a_larger_budget_and_succeeds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _Provider([_cut_off(), _answered(VALID_CV)])
    _wire(monkeypatch, _settings(), provider)

    with caplog.at_level(logging.WARNING, logger="linkedin_agent.infra.llm"):
        result = asyncio.run(complete_structured("PROMPT", TailoredCV, retries=1, step="cv_tailor"))

    assert result.lines[0].text == "Python"
    assert provider.caps == [2048, 4096]  # the second attempt has the larger cap
    assert provider.bodies[1]["messages"] == provider.bodies[0]["messages"]  # no "fix these problems" correction
    assert "step=cv_tailor attempt=1 max_tokens=2048, retrying with max_tokens=4096" in caplog.text


def test_a_cut_answer_that_persists_fails_at_the_ceiling_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider([_cut_off()])  # always cut off
    _wire(monkeypatch, _settings(), provider)

    with pytest.raises(CVStructureError) as info:
        asyncio.run(tailor_cv("ML Intern", "job", load_master_cv(SAMPLE_MASTER)))

    assert info.value.message == "The model's answer was cut off or malformed; retry, or raise the output limit"
    assert info.value.message == CV_STRUCTURE_MESSAGE
    assert provider.caps == [2048, 4096]  # stops at the ceiling: no third call, no unbounded growth
    assert all("validator" not in json.dumps(body) for body in provider.bodies)


def test_truncation_at_the_ceiling_is_a_truncated_error() -> None:
    provider = _Provider([_cut_off()])
    settings = _settings(llm_max_output_tokens=4096, llm_output_token_ceiling=4096)
    client = GroqClient(settings, transport=httpx.MockTransport(provider), budget=TokenBudget(8000))
    with pytest.raises(LLMTruncatedError, match="cut off at 4096 tokens"):
        asyncio.run(client.complete("prompt", max_tokens=4096, model="m"))


def test_step_overrides_send_their_model_and_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        llm_reasoning_effort="medium",
        llm_step_overrides={"cv_tailor": LLMStepOverride(reasoning_effort="low")},
    )
    provider = _Provider([_answered(VALID_CV), _answered(VALID_CV)])
    _wire(monkeypatch, settings, provider)

    asyncio.run(complete_structured("p", TailoredCV, step="cv_tailor"))
    asyncio.run(complete_structured("p", TailoredCV))  # no override: the global effort applies

    assert provider.bodies[0]["reasoning_effort"] == "low"
    assert provider.bodies[0]["model"] == settings.groq_model
    assert provider.bodies[1]["reasoning_effort"] == "medium"


def test_an_empty_effort_is_not_sent_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Provider([_answered(VALID_CV)])
    _wire(monkeypatch, _settings(), provider)
    asyncio.run(complete_structured("p", TailoredCV))
    assert "reasoning_effort" not in provider.bodies[0]


def test_resolve_step_prefers_the_step_and_falls_back_to_the_global_values() -> None:
    settings = _settings(
        groq_model="global-model",
        llm_reasoning_effort="medium",
        llm_step_overrides={
            "cv_tailor": LLMStepOverride(model="small-model", reasoning_effort="low"),
            "empty": LLMStepOverride(),
        },
    )
    assert resolve_step("cv_tailor", settings) == ("small-model", "low")
    assert resolve_step("empty", settings) == ("global-model", "medium")
    assert resolve_step("unknown", settings) == ("global-model", "medium")


def test_a_prompt_that_can_never_fit_the_budget_is_refused_before_any_call() -> None:
    provider = _Provider([_answered(VALID_CV)])
    client = GroqClient(_settings(), transport=httpx.MockTransport(provider), budget=TokenBudget(1000))
    with pytest.raises(LLMUnavailableError, match="LLM_OUTPUT_TOKEN_CEILING"):
        asyncio.run(client.complete("x" * 3000, max_tokens=2048, model="m"))
    assert provider.bodies == []


def test_the_output_budget_must_fit_the_per_minute_budget() -> None:
    with pytest.raises(ValueError, match="LLM_TOKENS_PER_MINUTE"):
        _settings(llm_output_token_ceiling=9000, llm_tokens_per_minute=8000)
    with pytest.raises(ValueError, match="at least LLM_MAX_OUTPUT_TOKENS"):
        _settings(llm_max_output_tokens=4096, llm_output_token_ceiling=2048)
