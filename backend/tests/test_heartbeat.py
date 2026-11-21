"""Heartbeat: a healthy long wait is never reported as stalled, and the stall threshold cannot be set below the longest
expected silence. Time is fake (injected clock and sleep), so nothing actually waits.
"""

import asyncio
from datetime import timedelta

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver

from linkedin_agent import models
from linkedin_agent.clock import utcnow
from linkedin_agent.core.settings import Settings, get_settings
from linkedin_agent.graphs.application_graph import build_graph
from linkedin_agent.infra import heartbeat
from linkedin_agent.infra.llm import GroqClient
from linkedin_agent.infra.rate_limit import WINDOW_SECONDS, TokenBudget
from linkedin_agent.services import preparations
from tests.test_application_graph import GROUNDED, Fakes, _deps, _initial


class Beats:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# --- the module ---------------------------------------------------------------------


def test_a_beat_outside_a_bound_job_does_nothing() -> None:
    asyncio.run(heartbeat.beat())  # no error, no callback


def test_a_long_wait_beats_at_least_every_interval() -> None:
    beats, time = Beats(), FakeTime()

    async def run() -> None:
        with heartbeat.heartbeat_bound(beats):
            await heartbeat.wait(35.0, 10.0, sleep=time.sleep)

    asyncio.run(run())
    assert time.slept == [10.0, 10.0, 10.0, 5.0]  # never one silent sleep longer than the interval
    assert beats.count == 4


def test_nested_bindings_restore_the_outer_job() -> None:
    outer, inner = Beats(), Beats()

    async def run() -> None:
        with heartbeat.heartbeat_bound(outer):
            with heartbeat.heartbeat_bound(inner):
                await heartbeat.beat()
            await heartbeat.beat()

    asyncio.run(run())
    assert (outer.count, inner.count) == (1, 1)


# --- the token budget ---------------------------------------------------------------


def test_a_token_budget_wait_beats_while_it_waits() -> None:
    time = FakeTime()
    budget = TokenBudget(100, clock=time.clock, sleep=time.sleep, heartbeat_interval=10.0)
    beats = Beats()

    async def run() -> None:
        with heartbeat.heartbeat_bound(beats):
            await budget.reserve(100)  # fills the window
            await budget.reserve(100)  # must wait for the first reservation to expire

    asyncio.run(run())
    assert sum(time.slept) == pytest.approx(WINDOW_SECONDS)
    assert max(time.slept) <= 10.0
    assert beats.count == 6  # 60 s in chunks of 10 s, one beat per chunk


# --- the provider's Retry-After wait -------------------------------------------------


def test_a_rate_limit_wait_beats_while_it_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "25"}, json={"error": {"code": "rate_limit"}}),
            httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}),
        ]
    )
    settings = get_settings().model_copy(update={"groq_max_retries": 1, "heartbeat_interval_seconds": 10.0})
    client = GroqClient(
        settings,
        transport=httpx.MockTransport(lambda request: next(responses)),
        budget=TokenBudget(1_000_000),
    )
    beats = Beats()

    async def run() -> str:
        with heartbeat.heartbeat_bound(beats):
            return await client.complete("hello")

    assert asyncio.run(run()) == "ok"
    assert slept == [10.0, 10.0, 5.0]  # the 25 s Retry-After, in chunks
    assert beats.count == 5  # a beat before each of the two HTTP attempts, and one per chunk


# --- the graph ----------------------------------------------------------------------


def test_each_graph_node_reports_its_start_through_on_step() -> None:
    """Production's on_step writes the heartbeat, so every node start is a beat. Nodes that wait beat themselves."""
    fakes = Fakes([GROUNDED])
    graph = build_graph(_deps(fakes), checkpointer=MemorySaver())
    asyncio.run(graph.ainvoke(_initial(), {"configurable": {"thread_id": "beats"}}))
    assert fakes.steps[0] == "fetch_job"
    assert fakes.steps[-1] == "await_human"
    assert len(fakes.steps) == 9


# --- stalled and the threshold --------------------------------------------------------


def _run_with_heartbeat(age_seconds: float) -> models.PreparationRun:
    return models.PreparationRun(
        id=1,
        user_id=1,
        public_id="token",
        lock_key="k",
        status="running",
        payload_json="{}",
        heartbeat_at=utcnow() - timedelta(seconds=age_seconds),
    )


def test_a_run_that_beat_recently_is_not_stalled_even_during_a_long_wait() -> None:
    run = _run_with_heartbeat(age_seconds=9)
    assert preparations.status_of(run, utcnow(), stale_seconds=300.0)["stalled"] is False


def test_a_run_silent_for_longer_than_the_threshold_is_stalled() -> None:
    run = _run_with_heartbeat(age_seconds=301)
    assert preparations.status_of(run, utcnow(), stale_seconds=300.0)["stalled"] is True


def test_the_longest_expected_silence_is_the_slowest_call_plus_one_beat_interval() -> None:
    settings = get_settings().model_copy(
        update={"groq_timeout_seconds": 30.0, "serper_timeout_seconds": 20.0, "heartbeat_interval_seconds": 10.0}
    )
    assert preparations.longest_expected_silence_seconds(settings) == 40.0


def test_the_default_threshold_is_longer_than_the_longest_expected_silence() -> None:
    preparations.check_stall_threshold(get_settings())  # does not raise


def test_a_threshold_shorter_than_a_healthy_wait_is_refused_at_startup() -> None:
    settings = get_settings().model_copy(update={"preparation_stale_seconds": 30.0})
    with pytest.raises(ValueError, match="PREPARATION_STALE_SECONDS"):
        preparations.check_stall_threshold(settings)


def test_the_heartbeat_interval_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(
            database_url="postgresql://u:p@h/db_test",
            secret_key="a-long-enough-secret-key",
            token_encryption_key=get_settings().token_encryption_key,
            heartbeat_interval_seconds=0,
        )
