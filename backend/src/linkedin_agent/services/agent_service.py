import logging

from bs4 import BeautifulSoup

from ..core.exceptions import ExternalServiceError
from ..domain.outputs import PlannerOutput
from ..infra.llm import complete_structured
from ..infra.safe_http import FetchError, fetch_text
from ..prompts import render

logger = logging.getLogger(__name__)

PAGE_MAX_CHARS = 1500
PAGES_TO_READ = 2
MAX_SOURCES = 6
RESULTS_PER_QUERY = 5
FINDINGS_MAX_CHARS = 1500
SERVICE_BUSY_MESSAGE = "Le service est temporairement saturé. Réessaie dans une minute."


def _format_history(history, max_msgs=4, max_chars=300):
    return "\n".join(f"{m.role}: {m.content[:max_chars]}" for m in history[-max_msgs:])


async def plan(user_request: str, profile: dict, history: list) -> dict:
    prompt = render(
        "planner.v1",
        field=profile["field"],
        level=profile["level"],
        history=_format_history(history) or "(none)",
        request=user_request,
    )
    try:
        planned = await complete_structured(prompt, PlannerOutput)
    except ExternalServiceError:
        return {"intent": "search", "queries": [user_request]}
    return {"intent": planned.intent, "queries": planned.queries}


async def read_page(url: str, max_chars: int = PAGE_MAX_CHARS) -> str:
    """The readable text of a search result. A refused or failed fetch yields an empty string."""
    try:
        html = await fetch_text(url)
    except FetchError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:max_chars]
