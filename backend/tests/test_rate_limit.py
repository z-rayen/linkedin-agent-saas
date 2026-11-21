"""The shared token budget: reservations, correction with real usage, waiting, Retry-After, per process.

Time is fake: the clock and the sleep are injected, so nothing actually waits.
"""

import asyncio
import math

import httpx
import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.settings import Settings
from linkedin_agent.infra.llm import GroqClient, get_token_budget
from linkedin_agent.infra.rate_limit import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    WINDOW_SECONDS,
    TokenBudget,
    estimate_prompt_tokens,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _budget(capacity: int, time: FakeTime) -> TokenBudget:
    return TokenBudget(capacity, clock=time.clock, sleep=time.sleep)


def test_estimate_is_conservative_and_grows_with_the_prompt() -> None:
    assert estimate_prompt_tokens("") == 0
    assert estimate_prompt_tokens("a" * 300) == 100
    assert estimate_prompt_tokens("a" * 301) == math.ceil(301 / 3)


def test_a_reservation_within_capacity_does_not_wait() -> None:
    time = FakeTime()
    budget = _budget(1000, time)
    asyncio.run(budget.reserve(600))
    assert time.slept == []
    assert budget.used() == 600


def test_a_reservation_over_the_remaining_budget_waits_for_the_window() -> None:
    time = FakeTime()
    budget = _budget(1000, time)
    asyncio.run(budget.reserve(800))
    asyncio.run(budget.reserve(500))  # 800 + 500 > 1000: must wait until the first one leaves the window
    # The wait is split into heartbeat-sized chunks (see infra/heartbeat.py); the total is still one window.
    assert sum(time.slept) == pytest.approx(WINDOW_SECONDS)
    assert max(time.slept) <= DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def test_entries_leave_the_window_after_a_minute() -> None:
    time = FakeTime()
    budget = _budget(1000, time)
    asyncio.run(budget.reserve(900))
    time.now += WINDOW_SECONDS
    assert budget.used() == 0


def test_correcting_with_real_usage_frees_budget_for_the_next_call() -> None:
    time = FakeTime()
    budget = _budget(1000, time)
    reservation = asyncio.run(budget.reserve(900))  # estimate was high
    budget.correct(reservation, 100)  # the provider reported 100 tokens
    asyncio.run(budget.reserve(800))
    assert time.slept == []


def test_correcting_upward_counts_the_real_cost() -> None:
    time = FakeTime()
    budget = _budget(1000, time)
    reservation = asyncio.run(budget.reserve(100))
    budget.correct(reservation, 950)
    assert budget.used() == 950


def test_a_call_that_can_never_fit_is_refused_immediately() -> None:
    budget = _budget(1000, FakeTime())
    with pytest.raises(ValueError, match="more than the budget"):
        asyncio.run(budget.reserve(1001))


def test_a_non_positive_budget_is_refused() -> None:
    with pytest.raises(ValueError):
        TokenBudget(0)


def test_there_is_one_budget_per_process() -> None:
    assert get_token_budget() is get_token_budget()


# --- the Groq client uses the budget and honours Retry-After ----------------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://unused/unused_test",
        "secret_key": "a-long-enough-secret-key",
        "token_encryption_key": Fernet.generate_key().decode(),
        "groq_api_key": "test-fake-key",
        "groq_max_retries": 2,
        "groq_retry_wait_seconds": 8.0,
        "llm_max_output_tokens": 100,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _ok(total_tokens: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "done"}}], "usage": {"total_tokens": total_tokens}},
    )


def test_the_real_usage_replaces_the_estimate_after_a_call() -> None:
    time = FakeTime()
    budget = _budget(10_000, time)
    client = GroqClient(_settings(), transport=httpx.MockTransport(lambda r: _ok(42)), budget=budget)
    assert asyncio.run(client.complete("short prompt")) == "done"
    assert budget.used() == 42


def test_the_request_asks_the_provider_for_at_most_the_configured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return _ok(10)

    client = GroqClient(_settings(), transport=httpx.MockTransport(handler), budget=_budget(10_000, FakeTime()))
    asyncio.run(client.complete("p"))
    assert '"max_tokens":100' in str(seen["body"]).replace(" ", "")


def test_a_429_waits_for_the_retry_after_header_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "12"}, json={"error": {"code": "rate_limit_exceeded"}}),
            _ok(20),
        ]
    )
    client = GroqClient(
        _settings(), transport=httpx.MockTransport(lambda r: next(responses)), budget=_budget(10_000, FakeTime())
    )
    assert asyncio.run(client.complete("p")) == "done"
    assert sum(waits) == pytest.approx(12.0)  # chunked for the heartbeat; the total wait is the Retry-After
    assert max(waits) <= DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def test_a_429_without_a_header_uses_the_configured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    responses = iter([httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}), _ok(5)])
    client = GroqClient(
        _settings(), transport=httpx.MockTransport(lambda r: next(responses)), budget=_budget(10_000, FakeTime())
    )
    asyncio.run(client.complete("p"))
    assert waits == [8.0]


def test_an_absurd_retry_after_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    responses = iter([httpx.Response(429, headers={"retry-after": "86400"}, json={"error": {"code": "x"}}), _ok(5)])
    client = GroqClient(
        _settings(), transport=httpx.MockTransport(lambda r: next(responses)), budget=_budget(10_000, FakeTime())
    )
    asyncio.run(client.complete("p"))
    assert sum(waits) == pytest.approx(60.0)  # capped at one minute, chunked for the heartbeat
    assert max(waits) <= DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def test_a_refused_call_keeps_its_estimate_in_the_budget() -> None:
    time = FakeTime()
    budget = _budget(10_000, time)
    client = GroqClient(
        _settings(groq_max_retries=0),
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {"code": "boom"}})),
        budget=budget,
    )
    with pytest.raises(Exception, match="unavailable"):
        asyncio.run(client.complete("p"))
    assert budget.used() > 0  # the provider may have counted it: the estimate stays
