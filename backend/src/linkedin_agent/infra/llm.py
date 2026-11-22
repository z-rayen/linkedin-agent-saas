"""LLM access behind a provider interface.

Callers depend on the LLMClient protocol and on complete_structured(), never on Groq directly. Every call
reserves its estimated tokens in the shared budget (infra/rate_limit.py) and corrects the reservation with the
usage the provider reports. A 429 response is a safety net: its Retry-After header is honoured, capped.
"""

import logging
from functools import cache, lru_cache
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..core.exceptions import LLMOutputError, LLMUnavailableError
from ..core.settings import Settings, get_settings
from . import heartbeat
from .rate_limit import Reservation, TokenBudget, estimate_prompt_tokens

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_RETRY_AFTER_SECONDS = 60.0


class LLMClient(Protocol):
    """Anything that turns a prompt into text. Raises LLMUnavailableError when it cannot answer."""

    async def complete(self, prompt: str) -> str: ...


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("retry-after", "")
    try:
        seconds = float(raw)
    except ValueError:
        seconds = fallback
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class GroqClient:
    """Groq's OpenAI-compatible chat endpoint, with the shared token budget and a 429 safety net."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        budget: TokenBudget | None = None,
    ) -> None:
        # `transport` and `budget` let tests inject fakes; production leaves them None.
        self._settings = settings
        self._transport = transport
        self._budget = budget or get_token_budget()

    async def complete(self, prompt: str) -> str:
        s = self._settings
        estimate = estimate_prompt_tokens(prompt) + s.llm_max_output_tokens
        for attempt in range(s.groq_max_retries + 1):
            reservation: Reservation = await self._budget.reserve(estimate)
            await heartbeat.beat()  # the HTTP call below may take up to groq_timeout_seconds with no other sign of life
            async with httpx.AsyncClient(transport=self._transport, trust_env=False) as client:
                resp = await client.post(
                    s.groq_url,
                    headers={"Authorization": f"Bearer {s.groq_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": s.groq_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": s.llm_max_output_tokens,
                    },
                    timeout=s.groq_timeout_seconds,
                )
            try:
                data = resp.json()
            except ValueError as exc:
                self._budget.correct(reservation, estimate)
                logger.error("llm returned non-json status=%s", resp.status_code)
                raise LLMUnavailableError("LLM provider returned an invalid response") from exc

            if "choices" in data:
                usage = data.get("usage") or {}
                actual = usage.get("total_tokens")
                self._budget.correct(reservation, int(actual) if isinstance(actual, int) else estimate)
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    # Reasoning tokens count against max_tokens too: a cut answer is not valid JSON.
                    logger.warning("llm answer cut at max_tokens=%d", s.llm_max_output_tokens)
                content: str = choice["message"]["content"]
                return content

            # Rejected: the request may or may not have been counted by the provider. Keep the estimate.
            self._budget.correct(reservation, estimate)
            error_code = (data.get("error") or {}).get("code")
            if resp.status_code == 429 and attempt < s.groq_max_retries:
                wait = _retry_after_seconds(resp, fallback=s.groq_retry_wait_seconds)
                logger.warning("llm rate limited, waiting %.1fs, retry %d/%d", wait, attempt + 1, s.groq_max_retries)
                await heartbeat.wait(wait, s.heartbeat_interval_seconds)
                continue

            logger.error("llm request failed status=%s error=%s", resp.status_code, error_code)
            raise LLMUnavailableError("LLM provider unavailable")

        raise LLMUnavailableError("LLM provider unavailable")


@lru_cache(maxsize=1)
def get_token_budget() -> TokenBudget:
    """The one budget of this process, sized from settings."""
    return TokenBudget(get_settings().llm_tokens_per_minute)


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Process-wide LLM client, selected from settings."""
    return GroqClient(get_settings())


def _strip_code_fence(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


MAX_PROBLEMS_REPORTED = 10
UNKNOWN_FIELD = "<unknown>"


@cache
def _schema_field_names(model: type[BaseModel]) -> frozenset[str]:
    """Every property name in the model's JSON schema. Used to keep model-supplied keys out of logs."""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(model.model_json_schema())
    return frozenset(names)


def describe_validation_errors(exc: ValidationError, model: type[BaseModel]) -> list[str]:
    """Field path and error type of each problem, such as "lines.0.section: literal_error".

    Never the input value or the message: those can contain the model's text or the candidate's CV. Path parts that
    are not schema field names are replaced, so a key the model invented cannot reach the logs.
    """
    known = _schema_field_names(model)
    described: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        parts = [
            str(part) if isinstance(part, int) else (part if part in known else UNKNOWN_FIELD) for part in error["loc"]
        ]
        path = ".".join(parts) or "(root)"
        described.append(f"{path}: {error['type']}")
    return described[:MAX_PROBLEMS_REPORTED]


def correction_for(problems: list[str]) -> str:
    """The retry instruction. It carries the problems (paths and types), never the rejected answer."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        "\n\nYour previous answer was rejected by the output validator. Problems (field path and error type only):\n"
        f"{listed}\n"
        "Return the complete answer again in the exact format the instructions require, fixing these problems. "
        "Return only the JSON."
    )


async def complete_structured(prompt: str, model: type[ModelT], retries: int = 1) -> ModelT:
    """Ask for JSON, validate it against `model`, and retry up to `retries` times if it does not validate.

    Each retry sends the validation problems back to the model (field paths and error types, not content), so the
    model can correct itself instead of repeating the same answer.

    Raises LLMUnavailableError if the provider fails, LLMOutputError if no attempt validates.
    """
    client = get_llm_client()
    attempt_prompt = prompt
    for attempt in range(1, retries + 2):
        text = await client.complete(attempt_prompt)
        try:
            return model.model_validate_json(_strip_code_fence(text))
        except ValidationError as exc:
            problems = describe_validation_errors(exc, model)
            logger.warning(
                "llm output rejected model=%s attempt=%d problems=%s", model.__name__, attempt, "; ".join(problems)
            )
            attempt_prompt = prompt + correction_for(problems)
    raise LLMOutputError()
