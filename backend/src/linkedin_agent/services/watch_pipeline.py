"""Real dependencies of the watch graph, and the entry point that runs it.

The graph (graphs/watch_graph.py) decides the steps. This module supplies them: the search provider, safe page reading,
the two structured model calls (scoring and routing, batched), and the storage of kept results, which the caller
injects. Prompts are built here, from templates, so every search result and page text is wrapped as untrusted data.

Nothing here submits an application, sends a message or publishes anything.
"""

import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.settings import Settings, get_settings
from ..domain.outputs import WatchRouteOutput, WatchScoreOutput
from ..graphs.watch_graph import Candidate, WatchDeps, WatchLimits, build_graph
from ..infra.llm import complete_structured
from ..prompts import render
from .research_service import read_page_text
from .search_service import search_web

logger = logging.getLogger(__name__)
STEP_SCORE = "watch_score"
STEP_ROUTE = "watch_route"
NO_VALUE = "(none)"
SaveKept = Callable[[int, list[Candidate]], Awaitable[int]]


def normalise_profile(profile: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The profile the watch works with. Every field has a value; missing ones come from the settings, never from a
    query or a result."""
    languages = [language for language in profile.get("languages", []) if language]
    return {
        "field": str(profile.get("field") or settings.default_field),
        "level": str(profile.get("level") or settings.default_level),
        "skills": str(profile.get("skills") or settings.default_skills),
        "regions": [region for region in profile.get("regions", []) if region],
        "languages": languages or ["English"],
        "offer_type": str(profile.get("offer_type") or settings.watch_default_offer_type),
        "min_score": int(profile.get("min_match_score") or settings.default_min_match_score),
    }


def limits_from(settings: Settings, profile: dict[str, Any]) -> WatchLimits:
    return WatchLimits(
        max_queries=settings.watch_max_queries,
        max_regions=settings.watch_max_regions,
        results_per_query=settings.watch_results_per_query,
        max_candidates=settings.watch_max_candidates,
        max_pages=settings.watch_max_pages,
        page_max_chars=settings.watch_page_max_chars,
        batch_size=settings.watch_batch_size,
        snippet_score_cap=settings.watch_snippet_score_cap,
        min_score=profile["min_score"],
        min_relevance=settings.watch_min_relevance,
        min_title_matches=settings.watch_min_title_matches,
        templates=tuple(settings.watch_query_templates),
    )


def worst_case_llm_calls(settings: Settings) -> int:
    """The most LLM calls one watch run can make.

    Scoring runs on every candidate, in batches. Routing runs only on the candidates that reach the score threshold, so
    in the worst case it runs on the same candidates. Each structured call has retries+1 attempts, and an attempt that
    is cut off is one of them. Retries of the provider's HTTP 429 answer are not counted here.
    """
    batches = math.ceil(settings.watch_max_candidates / settings.watch_batch_size)
    attempts = settings.watch_structured_retries + 1
    return 2 * batches * attempts


def _block(candidates: list[Candidate], text_chars: int) -> str:
    parts = []
    for number, candidate in enumerate(candidates, start=1):
        basis = "full page" if candidate["fetched"] else "search snippet only"
        text = (candidate["text"] or NO_VALUE)[:text_chars] if text_chars else ""
        block = f"[{number}] {candidate['title']} ({basis})\nURL: {candidate['link']}\n{text}"
        parts.append(block.rstrip())
    return "\n\n".join(parts)


def _as_items(output: Any) -> list[dict[str, Any]]:
    return [item.model_dump() for item in output.items]


def build_deps(
    *,
    profile: dict[str, Any],
    settings: Settings,
    known_links: Callable[[int], Awaitable[list[str]]],
    save_kept: SaveKept,
    on_step: Callable[[str], Awaitable[None]] | None = None,
    stopwords: frozenset[str] = frozenset(),
) -> WatchDeps:
    limits = limits_from(settings, profile)
    retries = settings.watch_structured_retries

    async def search(query: str, count: int) -> list[dict[str, Any]]:
        results = await search_web(query, num_results=count)
        return [{"title": str(r["title"]), "link": str(r["link"]), "summary": str(r["summary"])} for r in results]

    async def read(url: str) -> str:
        return await read_page_text(url, limits.page_max_chars)

    async def score(batch: list[Candidate], user_profile: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = render(
            "watch_score.v1",
            field=user_profile["field"],
            level=user_profile["level"],
            skills=user_profile["skills"],
            offer_type=user_profile["offer_type"],
            region=", ".join(user_profile["regions"]) or "anywhere",
            language=", ".join(user_profile["languages"]),
            candidates=_block(batch, limits.page_max_chars),
        )
        output = await complete_structured(prompt, WatchScoreOutput, retries=retries, step=STEP_SCORE)
        return _as_items(output)

    async def route(batch: list[Candidate], user_profile: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = render("watch_route.v1", candidates=_block(batch, settings.watch_route_text_chars))
        output = await complete_structured(prompt, WatchRouteOutput, retries=retries, step=STEP_ROUTE)
        return _as_items(output)

    async def no_op(_: str) -> None:
        return None

    async def persist(owner: int, kept: list[Candidate]) -> int:
        return await save_kept(owner, kept)

    async def known(owner: int) -> list[str]:
        return await known_links(owner)

    return WatchDeps(
        search=search,
        read=read,
        score=score,
        route=route,
        known_links=known,
        persist=persist,
        on_step=on_step or no_op,
        limits=limits,
        stopwords=stopwords,
    )


async def run_watch(
    *,
    user_id: int,
    profile: dict[str, Any],
    save_kept: SaveKept,
    known_links: Callable[[int], Awaitable[list[str]]],
    settings: Settings | None = None,
    on_step: Callable[[str], Awaitable[None]] | None = None,
    stopwords: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run the watch to its end, in this process. A background run wraps this; nothing is persisted here itself."""
    current = settings or get_settings()
    normalised = normalise_profile(profile, current)
    deps = build_deps(
        profile=normalised,
        settings=current,
        known_links=known_links,
        save_kept=save_kept,
        on_step=on_step,
        stopwords=stopwords,
    )
    graph = build_graph(deps)
    state = await graph.ainvoke({"user_id": user_id, "profile": normalised})
    return watch_result_of(state)


def watch_result_of(state: dict[str, Any]) -> dict[str, Any]:
    """The public result of a finished watch: what was kept, with its flags, and the counts of what was dropped."""
    return {
        "stored": [
            {
                "title": c["title"],
                "link": c["link"],
                "category": c["category"],
                "relevance": c["relevance"],
                "justification": c["justification"],
                "company": c["company"],
                "job_title": c["job_title"],
                "fetched": c["fetched"],
                "unverified": c["unverified"],
            }
            for c in state.get("stored", [])
        ],
        "counts": dict(state.get("counts", {})),
        "queries": list(state.get("queries_run", [])),
    }
