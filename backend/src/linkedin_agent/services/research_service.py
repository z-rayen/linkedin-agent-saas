"""Real dependencies of the research graph, and the entry point that runs it.

The graph (graphs/research_graph.py) decides the steps. This module supplies them: the LLM (structured steps for the
plan and the reflection, free text for the answer and the post), the search provider, safe page reading, and the
content queue for drafts. Prompts are built here, from templates, so every untrusted text is wrapped before a model
reads it.

Nothing here publishes anything. The content queue receives drafts only.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bs4 import BeautifulSoup

from ..core.settings import Settings, get_settings
from ..domain.outputs import PlannerOutput, ReflectOutput
from ..graphs.research_graph import (
    Hit,
    Reflection,
    ResearchDeps,
    ResearchLimits,
    Source,
    build_graph,
    fit_sources,
    numbered_block,
)
from ..infra.llm import complete_structured, complete_text, output_budgets
from ..infra.safe_http import FetchError, fetch_text
from ..prompts import render
from .search_service import search_web

logger = logging.getLogger(__name__)
STEP_PLAN = "research_plan"
STEP_REFLECT = "research_reflect"
STEP_ANSWER = "research_answer"
STEP_POST = "research_post"
NO_EVIDENCE = "(none)"
SaveDraft = Callable[[int, str, str], Awaitable[int]]


def limits_from(settings: Settings) -> ResearchLimits:
    return ResearchLimits(
        max_loops=settings.research_max_loops,
        max_queries=settings.research_max_queries,
        results_per_query=settings.research_results_per_query,
        max_sources=settings.research_max_sources,
        max_pages=settings.research_max_pages,
        page_max_chars=settings.research_page_max_chars,
        context_max_chars=settings.research_context_max_chars,
        min_relevance=settings.research_min_relevance,
        min_title_matches=settings.research_min_title_matches,
        require_citations=settings.research_require_citations,
    )


def worst_case_llm_calls(settings: Settings, *, post: bool) -> int:
    """The most LLM calls one research run can make, with every loop used and every answer cut off once.

    Plan and reflection are structured: each has retries+1 attempts. A reflection runs once per search round, and
    there are max_loops + 1 rounds. The answer and the post are free text: one call per output cap. Retries of the
    provider's HTTP 429 answer are not counted here; they are bounded by GROQ_MAX_RETRIES.
    """
    attempts = settings.research_structured_retries + 1
    rounds = settings.research_max_loops + 1
    answer = len(output_budgets(settings))
    plan = attempts
    reflect = attempts * rounds
    return plan + reflect + answer + (answer if post else 0)


def evidence_text(sources: list[Source], max_chars: int) -> str:
    shown = fit_sources(sources, max_chars)
    if not shown:
        return NO_EVIDENCE
    return "\n\n".join(numbered_block(number, source) for number, source in enumerate(shown, start=1))


def extract_page_text(html: str, max_chars: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:max_chars]


async def read_page_text(url: str, max_chars: int) -> str:
    """The readable text of one page, fetched under safe_http's rules. A refused or unreachable page gives ""."""
    try:
        html = await fetch_text(url)
    except FetchError:
        return ""
    return extract_page_text(html, max_chars)


def build_deps(
    *,
    user_id: int,
    profile: dict[str, str],
    settings: Settings,
    save_draft: SaveDraft,
    on_step: Callable[[str], Awaitable[None]] | None = None,
) -> ResearchDeps:
    limits = limits_from(settings)
    retries = settings.research_structured_retries

    async def plan(request: str, user_profile: dict[str, str], history: str) -> list[str]:
        prompt = render(
            "planner.v2",
            field=user_profile["field"],
            level=user_profile["level"],
            history=history or NO_EVIDENCE,
            request=request,
        )
        planned = await complete_structured(prompt, PlannerOutput, retries=retries, step=STEP_PLAN)
        return list(planned.queries)

    async def search(query: str) -> list[Hit]:
        results = await search_web(query, num_results=limits.results_per_query)
        return [Hit(title=str(r["title"]), link=str(r["link"]), summary=str(r["summary"])) for r in results]

    async def read(url: str) -> str:
        return await read_page_text(url, limits.page_max_chars)

    async def reflect(request: str, user_profile: dict[str, str], sources: list[Source]) -> Reflection:
        prompt = render(
            "research_reflect.v1",
            field=user_profile["field"],
            level=user_profile["level"],
            request=request,
            evidence=evidence_text(sources, limits.context_max_chars),
        )
        judged = await complete_structured(prompt, ReflectOutput, retries=retries, step=STEP_REFLECT)
        return Reflection(sufficient=judged.sufficient, queries=list(judged.queries))

    async def synthesize(
        request: str, user_profile: dict[str, str], history: str, shown: list[Source], language_code: str
    ) -> str:
        prompt = render(
            "research_answer.v3",
            field=user_profile["field"],
            skills=user_profile["skills"],
            level=user_profile["level"],
            history=history or NO_EVIDENCE,
            request=request,
            sources="\n\n".join(numbered_block(number, s) for number, s in enumerate(shown, start=1)),
            not_specified=settings.research_languages[language_code].not_specified,
        )
        return await complete_text(prompt, step=STEP_ANSWER)

    async def draft(request: str, user_profile: dict[str, str], answer: str) -> str:
        prompt = render(
            "linkedin_post.v1",
            field=user_profile["field"],
            level=user_profile["level"],
            request=request,
            findings=answer,
        )
        return await complete_text(prompt, step=STEP_POST)

    async def no_op(_: str) -> None:
        return None

    return ResearchDeps(
        plan=plan,
        search=search,
        read=read,
        reflect=reflect,
        synthesize=synthesize,
        draft=draft,
        save_draft=save_draft,
        on_step=on_step or no_op,
        limits=limits,
        languages=dict(settings.research_languages),
        default_language=settings.research_default_language,
    )


async def run_research(
    *,
    user_id: int,
    request: str,
    profile: dict[str, str],
    history: str,
    draft_post: bool,
    save_draft: SaveDraft,
    settings: Settings | None = None,
    on_step: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run the graph to its end, in this process. A background run wraps this later; nothing is persisted here."""
    current = settings or get_settings()
    deps = build_deps(user_id=user_id, profile=profile, settings=current, save_draft=save_draft, on_step=on_step)
    graph = build_graph(deps)
    state = await graph.ainvoke(
        {
            "user_id": user_id,
            "request": request,
            "profile": profile,
            "history": history,
            "draft_post": draft_post,
        }
    )
    return result_of(state)


def result_of(state: Any) -> dict[str, Any]:
    """The public result of a finished research graph: the answer, its traceability and what was filtered out."""
    shown: list[Source] = state.get("shown", [])
    claims: list[dict[str, Any]] = state.get("claims", [])
    cited_numbers = sorted({number for claim in claims for number in claim["sources"]})
    cited_links = {shown[number - 1]["link"] for number in cited_numbers}
    # "sources" are the ones the answer cites, numbered as in the answer. The other retrieved links are "also_found":
    # they were searched and read or kept as snippets, but no statement of the answer rests on them.
    return {
        "answer": state.get("answer", ""),
        "retrieved_at": state.get("retrieved_at", ""),
        "language": state.get("language", ""),
        "claims": claims,
        "sources": [
            {
                "number": number,
                "title": shown[number - 1]["title"],
                "link": shown[number - 1]["link"],
                "fetched": shown[number - 1]["fetched"],
            }
            for number in cited_numbers
        ],
        "also_found": [
            {"title": s["title"], "link": s["link"], "fetched": s["fetched"]}
            for s in state.get("sources", [])
            if s["link"] not in cited_links
        ],
        "unsupported": state.get("unsupported", {}),
        "off_topic": state.get("off_topic", 0),
        "queries": state.get("queries_run", []),
        "citations": state.get("citations", []),
        "removed_citations": state.get("removed_citations", 0),
        "post_id": state.get("post_id"),
    }
