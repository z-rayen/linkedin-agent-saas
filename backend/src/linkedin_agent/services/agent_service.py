import asyncio
import logging

from bs4 import BeautifulSoup

from ..core.exceptions import ExternalServiceError
from ..core.settings import get_settings
from ..domain.outputs import PlannerOutput
from ..infra.llm import complete_structured, get_llm_client
from ..infra.safe_http import FetchError, fetch_text
from ..prompts import render
from .search_service import search_web

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


async def run_agent(user_request: str, profile: dict, history: list) -> dict:
    settings = get_settings()
    pause = settings.llm_min_interval_seconds

    # 1. Planning (profile + recent history)
    planned = await plan(user_request, profile, history)
    await asyncio.sleep(pause)

    # 2. Search
    all_results, seen = [], set()
    for query in planned["queries"]:
        for result in await search_web(query, num_results=RESULTS_PER_QUERY):
            if result["link"] not in seen:
                seen.add(result["link"])
                all_results.append(result)
    top = all_results[:MAX_SOURCES]

    # 3. Read the best pages in full, keep the snippet for the others
    sources_text = []
    for i, result in enumerate(top):
        content = result["summary"]
        if i < PAGES_TO_READ:
            page = await read_page(result["link"])
            if page:
                content = page
        sources_text.append(f"[{i + 1}] {result['title']}\nURL: {result['link']}\n{content}")

    # 4. Grounded answer
    prompt = render(
        "research_answer.v1",
        field=profile["field"],
        skills=profile["skills"],
        level=profile["level"],
        history=_format_history(history, max_msgs=2, max_chars=200) or "(none)",
        request=user_request,
        sources="\n".join(sources_text),
    )
    try:
        answer: str | None = (await get_llm_client().complete(prompt)).strip() or None
    except ExternalServiceError:
        answer = None
    await asyncio.sleep(pause)

    # 5. Optional LinkedIn post drafted from the answer
    post_text = None
    if planned["intent"] == "search_and_post" and answer:
        post_prompt = render(
            "linkedin_post.v1",
            field=profile["field"],
            level=profile["level"],
            request=user_request,
            findings=answer[:FINDINGS_MAX_CHARS],
        )
        try:
            post_text = await get_llm_client().complete(post_prompt)
        except ExternalServiceError:
            post_text = None

    return {
        "answer": answer or SERVICE_BUSY_MESSAGE,
        "queries": planned["queries"],
        "intent": planned["intent"],
        "sources": [{"title": r["title"], "link": r["link"]} for r in top],
        "post_text": post_text,
    }
