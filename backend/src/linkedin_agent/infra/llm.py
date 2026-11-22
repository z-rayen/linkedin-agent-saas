"""LLM access behind a provider interface.

Callers depend on the LLMClient protocol and on complete_structured(), never on Groq directly. Every call
reserves its estimated tokens in the shared budget (infra/rate_limit.py) and corrects the reservation with the
usage the provider reports. A 429 response is a safety net: its Retry-After header is honoured, capped.
"""

import logging
import math
from functools import cache, lru_cache
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..core.exceptions import LLMOutputError, LLMTruncatedError, LLMUnavailableError
from ..core.settings import Settings, get_settings
from . import heartbeat
from .rate_limit import Reservation, TokenBudget, estimate_prompt_tokens

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_RETRY_AFTER_SECONDS = 60.0
DEFAULT_STEP = "default"  # the step name of a call that has no entry in llm_step_overrides


class LLMClient(Protocol):
    """Anything that turns a prompt into text. Raises LLMUnavailableError when it cannot answer and
    LLMTruncatedError when the answer hit max_tokens before it finished.

    max_tokens caps the answer (reasoning tokens included). None means llm_max_output_tokens, model None means
    groq_model. reasoning_effort is per call; an empty value is not sent, so the provider default applies.
    """

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> str: ...


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

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> str:
        s = self._settings
        cap = s.llm_max_output_tokens if max_tokens is None else max_tokens
        estimate = estimate_prompt_tokens(prompt) + cap
        if estimate > self._budget.capacity:
            # Refused before any call: a call that can never fit the per-minute budget would wait forever.
            raise LLMUnavailableError(
                f"the prompt and the output limit need {estimate} tokens per minute, more than LLM_TOKENS_PER_MINUTE: "
                "raise LLM_TOKENS_PER_MINUTE or lower LLM_OUTPUT_TOKEN_CEILING"
            )
        body: dict[str, object] = {
            "model": s.groq_model if model is None else model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": cap,
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        for attempt in range(s.groq_max_retries + 1):
            reservation: Reservation = await self._budget.reserve(estimate)
            await heartbeat.beat()  # the HTTP call below may take up to groq_timeout_seconds with no other sign of life
            async with httpx.AsyncClient(transport=self._transport, trust_env=False) as client:
                resp = await client.post(
                    s.groq_url,
                    headers={"Authorization": f"Bearer {s.groq_api_key}", "Content-Type": "application/json"},
                    json=body,
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
                    raise LLMTruncatedError(cap)
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


def resolve_step(step: str, settings: Settings) -> tuple[str, str]:
    """The model and the reasoning effort of one step. A step's override wins; an empty value means the global one."""
    override = settings.llm_step_overrides.get(step)
    model = (override.model if override else "") or settings.groq_model
    effort = (override.reasoning_effort if override else "") or settings.llm_reasoning_effort
    return model, effort


async def complete_structured(prompt: str, model: type[ModelT], retries: int = 1, step: str = DEFAULT_STEP) -> ModelT:
    """Ask for JSON, validate it against `model`, and retry up to `retries` times when an attempt fails.

    Two kinds of failure are retried differently. A rejected answer is retried with the validation problems sent
    back (field paths and error types, not content), so the model can correct itself. An answer cut off at max_tokens
    is retried with a larger cap, up to llm_output_token_ceiling, and no correction: the problem is the budget.
    Every attempt, retries included, counts against `retries`.

    Raises LLMUnavailableError if the provider fails, LLMTruncatedError if the cap is still too small at the ceiling,
    LLMOutputError if no attempt validates.
    """
    settings = get_settings()
    client = get_llm_client()
    llm_model, effort = resolve_step(step, settings)
    cap = settings.llm_max_output_tokens
    attempt_prompt = prompt
    for attempt in range(1, retries + 2):
        try:
            text = await client.complete(attempt_prompt, max_tokens=cap, model=llm_model, reasoning_effort=effort)
        except LLMTruncatedError:
            if cap >= settings.llm_output_token_ceiling or attempt > retries:
                logger.warning("llm answer cut step=%s attempt=%d max_tokens=%d, no retry left", step, attempt, cap)
                raise
            larger = min(settings.llm_output_token_ceiling, math.ceil(cap * settings.llm_output_budget_multiplier))
            logger.warning(
                "llm answer cut step=%s attempt=%d max_tokens=%d, retrying with max_tokens=%d",
                step,
                attempt,
                cap,
                larger,
            )
            cap = larger
            continue
        try:
            return model.model_validate_json(_strip_code_fence(text))
        except ValidationError as exc:
            problems = describe_validation_errors(exc, model)
            logger.warning(
                "llm output rejected model=%s step=%s attempt=%d problems=%s",
                model.__name__,
                step,
                attempt,
                "; ".join(problems),
            )
            attempt_prompt = prompt + correction_for(problems)
    raise LLMOutputError()


def output_budgets(settings: Settings) -> list[int]:
    """The output caps one call may use, in order: the default cap, then each escalation up to the ceiling.

    The list is the worst case of a call: each truncated answer moves to the next cap, and that is one attempt.
    """
    caps = [settings.llm_max_output_tokens]
    while caps[-1] < settings.llm_output_token_ceiling:
        larger = math.ceil(caps[-1] * settings.llm_output_budget_multiplier)
        caps.append(min(settings.llm_output_token_ceiling, larger))
    return caps


async def complete_text(prompt: str, step: str = DEFAULT_STEP) -> str:
    """A free-text answer. A cut-off answer is retried on the next cap; nothing is validated.

    Raises LLMUnavailableError if the provider fails, LLMTruncatedError if the ceiling is still too small.
    """
    settings = get_settings()
    client = get_llm_client()
    model, effort = resolve_step(step, settings)
    caps = output_budgets(settings)
    for index, cap in enumerate(caps):
        try:
            return await client.complete(prompt, max_tokens=cap, model=model, reasoning_effort=effort)
        except LLMTruncatedError:
            if index == len(caps) - 1:
                logger.warning("llm answer cut step=%s max_tokens=%d, no retry left", step, cap)
                raise
            logger.warning(
                "llm answer cut step=%s max_tokens=%d, retrying with max_tokens=%d", step, cap, caps[index + 1]
            )
    raise LLMOutputError()  # unreachable: the loop returns or raises on its last cap
