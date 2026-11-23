"""Unit tests for the LLM layer: prompt rendering and structured-output validation. No network, no database."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from linkedin_agent.core.exceptions import LLMOutputError
from linkedin_agent.domain.outputs import PlannerOutput, ScoreOutput
from linkedin_agent.infra.llm import complete_structured

LLM_COMPLETE = "linkedin_agent.infra.llm.GroqClient.complete"


# --- prompt templates --------------------------------------------------------


# --- domain models -----------------------------------------------------------


def test_score_output_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        ScoreOutput(score=150, justification="too high")


def test_planner_output_caps_queries_at_three() -> None:
    with pytest.raises(ValidationError):
        PlannerOutput(queries=["a", "b", "c", "d"])


# --- complete_structured -----------------------------------------------------


def test_complete_structured_returns_validated_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LLM_COMPLETE, AsyncMock(return_value='```json\n{"score": 80, "justification": "ok"}\n```'))
    result = asyncio.run(complete_structured("prompt", ScoreOutput))
    assert result == ScoreOutput(score=80, justification="ok")


def test_complete_structured_retries_once_on_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = AsyncMock(side_effect=["not json at all", '{"score": 70, "justification": "second try"}'])
    monkeypatch.setattr(LLM_COMPLETE, fake)
    result = asyncio.run(complete_structured("prompt", ScoreOutput))
    assert result.score == 70
    assert fake.await_count == 2


def test_complete_structured_raises_after_retries_are_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = AsyncMock(return_value='{"score": 999}')
    monkeypatch.setattr(LLM_COMPLETE, fake)
    with pytest.raises(LLMOutputError):
        asyncio.run(complete_structured("prompt", ScoreOutput, retries=1))
    assert fake.await_count == 2
