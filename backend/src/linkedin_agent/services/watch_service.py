import logging

from ..core.exceptions import ExternalServiceError, LLMUnavailableError
from ..domain.outputs import ScoreOutput
from ..infra.llm import complete_structured, get_llm_client
from ..prompts import render

logger = logging.getLogger(__name__)
COVER_LETTER_FALLBACK = "Cover letter generation failed (rate limit)."


async def score_item(item: dict, profile: dict) -> dict:
    prompt = render(
        "score_item.v1",
        field=profile["field"],
        skills=profile["skills"],
        level=profile["level"],
        title=item["title"],
        summary=item["summary"],
    )
    try:
        scored = await complete_structured(prompt, ScoreOutput)
    except LLMUnavailableError:
        return {"score": 0, "justification": "LLM unavailable"}
    except ExternalServiceError:
        return {"score": 0, "justification": "parsing error"}
    return scored.model_dump()


async def generate_cover_letter(item: dict, profile: dict) -> str:
    prompt = render(
        "cover_letter.v1",
        field=profile["field"],
        skills=profile["skills"],
        level=profile["level"],
        title=item["title"],
        summary=item["summary"],
    )
    try:
        text = await get_llm_client().complete(prompt)
    except ExternalServiceError:
        return COVER_LETTER_FALLBACK
    return text.strip() or COVER_LETTER_FALLBACK
