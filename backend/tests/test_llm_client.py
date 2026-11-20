"""GroqClient and complete_structured against a mocked HTTP transport. No network, no database."""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.exceptions import LLMOutputError, LLMUnavailableError
from linkedin_agent.core.settings import Settings
from linkedin_agent.domain.outputs import ScoreOutput
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.llm import GroqClient, complete_structured


def make_settings(max_retries: int = 2) -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
        groq_max_retries=max_retries,
        groq_retry_wait_seconds=0,
    )


def groq_answer(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def rate_limited() -> httpx.Response:
    return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "slow down"}})


class Script:
    """Answers each request with the next response in the list, and records how many requests were made."""

    def __init__(self, responses: list[Callable[[], httpx.Response]]) -> None:
        self._responses = responses
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        index = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[index]()


def run_complete(script: Script, max_retries: int = 2) -> str:
    client = GroqClient(make_settings(max_retries), transport=httpx.MockTransport(script))
    return asyncio.run(client.complete("prompt"))


def test_rate_limit_is_retried_then_succeeds() -> None:
    script = Script([rate_limited, rate_limited, lambda: groq_answer("finally")])
    assert run_complete(script) == "finally"
    assert script.calls == 3


def test_retries_are_exhausted_then_the_call_fails_cleanly() -> None:
    script = Script([rate_limited])
    with pytest.raises(LLMUnavailableError):
        run_complete(script, max_retries=2)
    assert script.calls == 3  # first attempt + 2 retries, then give up


def test_non_rate_limit_error_is_not_retried() -> None:
    script = Script([lambda: httpx.Response(500, json={"error": {"code": "server_error"}})])
    with pytest.raises(LLMUnavailableError):
        run_complete(script)
    assert script.calls == 1


def test_non_json_response_is_reported_as_unavailable() -> None:
    script = Script([lambda: httpx.Response(502, text="<html>bad gateway</html>")])
    with pytest.raises(LLMUnavailableError):
        run_complete(script)


def test_structured_output_is_validated_and_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"score": 77, "justification": "good fit"})
    script = Script([lambda: groq_answer(f"```json\n{body}\n```")])
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: GroqClient(make_settings(), httpx.MockTransport(script)))
    result = asyncio.run(complete_structured("prompt", ScoreOutput))
    assert result == ScoreOutput(score=77, justification="good fit")
    assert script.calls == 1


def test_malformed_structured_output_is_retried_then_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script([lambda: groq_answer('{"score": 500}')])  # out of range: never validates
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: GroqClient(make_settings(), httpx.MockTransport(script)))
    with pytest.raises(LLMOutputError):
        asyncio.run(complete_structured("prompt", ScoreOutput, retries=1))
    assert script.calls == 2


def test_structured_output_recovers_on_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(
        [
            lambda: groq_answer("Sure! Here it is: not json"),
            lambda: groq_answer('{"score": 40, "justification": "partial"}'),
        ]
    )
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: GroqClient(make_settings(), httpx.MockTransport(script)))
    result = asyncio.run(complete_structured("prompt", ScoreOutput, retries=1))
    assert result.score == 40
    assert script.calls == 2
