"""The watch graph: find offers and certifications for one user, score them, and keep the ones worth a look.

    search -> dedupe -> read -> score -> route -> persist -> END

Every node calls an injected dependency; nothing here touches the network, the database or a model directly. The graph
has one pass and no loop, so the number of LLM calls is known before it starts (see worst_case_llm_calls in
services/watch_pipeline.py).

Search results and page text are untrusted data. They reach a model only through the prompt templates, which wrap them
in UNTRUSTED markers. Queries come from the profile and the configured templates, never from a result. The graph never
submits an application and never sends a message: it stores results for the user to review.

Grounding: a company or a job title that a model returns must appear in the text the candidate was judged on (the
fetched page, or the snippet). Otherwise it is blanked, never kept. A justification that names a term absent from that
text is blanked too. A candidate whose full page was not read is stored with `unverified` set, and its score is capped.
"""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from itertools import pairwise
from typing import Any, TypedDict
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from ..core.exceptions import ExternalServiceError
from ..cv.grounding import capitalised_terms, contains_term
from ..cv.lexicon import fold
from .research_checks import is_relevant

logger = logging.getLogger(__name__)

NODE_NAMES = ("search", "dedupe", "read", "score", "route", "persist")
TITLE_MAX_CHARS = 300
LINK_MAX_CHARS = 2048
TRACKING_PARAMETER = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|ref|source)$", re.IGNORECASE)
CATEGORY_OTHER = "other"
CATEGORY_JOB = "job"
CATEGORY_CERTIFICATION = "certification"


class Hit(TypedDict):
    title: str
    link: str
    summary: str


class Candidate(TypedDict):
    title: str
    link: str
    canonical: str  # the link in canonical form: the key of deduplication
    snippet: str  # the search result's summary
    text: str  # the page text when it was read, otherwise the snippet
    fetched: bool  # the full page was read; false means the candidate is judged on its snippet only
    read: bool  # a fetch was attempted (once, whatever the outcome)
    query: str  # the query that found it
    relevance: int  # 0-100, from the scoring call; capped for a snippet-only candidate
    justification: str  # grounded in the text, or empty
    company: str  # grounded in the text, or empty
    job_title: str  # grounded in the text, or empty
    category: str  # "job", "certification" or "other", from the routing call
    unverified: bool  # not fetched: shown with a visible flag


class WatchState(TypedDict, total=False):
    user_id: int
    profile: dict[str, Any]
    candidates: list[Candidate]
    queries_run: list[str]
    counts: dict[str, int]
    stored: list[Candidate]
    stored_count: int


@dataclass(frozen=True)
class WatchLimits:
    max_queries: int
    max_regions: int
    results_per_query: int
    max_candidates: int
    max_pages: int
    page_max_chars: int
    batch_size: int
    snippet_score_cap: int
    min_score: int
    min_relevance: float
    min_title_matches: int
    templates: tuple[str, ...]


@dataclass(frozen=True)
class WatchDeps:
    """Everything the graph does to the outside world. Tests replace each field with a fake."""

    search: Callable[[str, int], Awaitable[list[Hit]]]  # (query, count) -> results
    read: Callable[[str], Awaitable[str]]  # url -> page text, "" when refused (403), unreachable or not HTML
    score: Callable[[list[Candidate], dict[str, Any]], Awaitable[list[dict[str, Any]]]]  # one batch -> scored items
    route: Callable[[list[Candidate], dict[str, Any]], Awaitable[list[dict[str, Any]]]]  # one batch -> categories
    known_links: Callable[[int], Awaitable[list[str]]]  # canonical links already stored for the user
    persist: Callable[[int, list[Candidate]], Awaitable[int]]  # stores the kept candidates, returns how many
    on_step: Callable[[str], Awaitable[None]]
    limits: WatchLimits
    stopwords: frozenset[str]


def canonical_url(url: str) -> str:
    """The link in one form: lowercase scheme and host, no "www.", no fragment, no trailing slash, no tracking
    parameters, and the remaining parameters sorted. Two links to the same page give the same key."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    kept = sorted(pair for pair in parts.query.split("&") if pair and not TRACKING_PARAMETER.match(pair.split("=")[0]))
    query = f"?{'&'.join(kept)}" if kept else ""
    return f"{parts.scheme.lower()}://{host}{path}{query}"


def build_queries(profile: dict[str, Any], templates: tuple[str, ...], limits: WatchLimits) -> list[str]:
    """Queries from the profile and the configured templates. Deterministic; no result text is used."""
    regions = [region for region in profile.get("regions", []) if region][: limits.max_regions] or [""]
    languages = profile.get("languages") or []
    values = {
        "field": profile.get("field", ""),
        "level": profile.get("level", ""),
        "offer_type": profile.get("offer_type", ""),
        "language": languages[0] if languages else "",
    }
    queries: list[str] = []
    for template in templates:
        for region in regions:
            query = " ".join(template.format(region=region, **values).split())
            if query and query not in queries:
                queries.append(query)
    return queries[: limits.max_queries]


def ground_fields(candidate: Candidate, company: str, job_title: str, justification: str) -> tuple[str, str, str]:
    """Company, job title and justification, each kept only if the candidate's own text supports it."""
    text = f"{candidate['title']} {candidate['text']}"
    folded = fold(text)
    kept_company = company if company and fold(company) in folded else ""
    kept_title = job_title if job_title and fold(job_title) in folded else ""
    kept_justification = justification
    if justification and any(not contains_term(text, term) for term in capitalised_terms(justification)):
        kept_justification = ""
    return kept_company, kept_title, kept_justification


def _batches(items: list[Candidate], size: int) -> list[list[Candidate]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _count(state: WatchState, key: str, amount: int = 1) -> dict[str, int]:
    counts = dict(state.get("counts", {}))
    counts[key] = counts.get(key, 0) + amount
    return counts


def build_graph(deps: WatchDeps, checkpointer: Any = None) -> Any:
    """The compiled watch graph. The checkpointer, when given, lets a run resume after a restart."""
    limits = deps.limits

    async def search(state: WatchState) -> dict[str, Any]:
        queries = build_queries(state["profile"], limits.templates, limits)
        seen: set[str] = set()
        candidates: list[Candidate] = []
        counts = dict(state.get("counts", {}))
        for query in queries:
            try:
                hits = await deps.search(query, limits.results_per_query)
            except Exception as exc:  # one failed query does not stop the watch
                logger.warning("watch search failed query_index=%d error_type=%s", len(seen), type(exc).__name__)
                continue
            for hit in hits[: limits.results_per_query]:
                link = str(hit["link"])[:LINK_MAX_CHARS]
                key = canonical_url(link) if link else ""
                if not key or key in seen or len(candidates) >= limits.max_candidates:
                    continue
                if not is_relevant(
                    query,
                    str(hit["title"]),
                    str(hit["summary"]),
                    link,
                    stopwords=deps.stopwords,
                    min_share=limits.min_relevance,
                    min_title_matches=limits.min_title_matches,
                ):
                    counts["off_topic"] = counts.get("off_topic", 0) + 1
                    continue
                seen.add(key)
                snippet = str(hit["summary"])
                candidates.append(
                    Candidate(
                        title=str(hit["title"])[:TITLE_MAX_CHARS],
                        link=link,
                        canonical=key,
                        snippet=snippet,
                        text=snippet,
                        fetched=False,
                        read=False,
                        query=query,
                        relevance=0,
                        justification="",
                        company="",
                        job_title="",
                        category=CATEGORY_OTHER,
                        unverified=True,
                    )
                )
        counts["queries"] = len(queries)
        counts["candidates"] = len(candidates)
        return {"candidates": candidates, "queries_run": queries, "counts": counts}

    async def dedupe(state: WatchState) -> dict[str, Any]:
        known = set(await deps.known_links(state["user_id"]))  # the links this user already has, in canonical form
        fresh = [candidate for candidate in state.get("candidates", []) if candidate["canonical"] not in known]
        counts = _count(state, "duplicates", len(state.get("candidates", [])) - len(fresh))
        return {"candidates": fresh, "counts": counts}

    async def read(state: WatchState) -> dict[str, Any]:
        candidates: list[Candidate] = [Candidate(**candidate) for candidate in state.get("candidates", [])]
        pages = 0
        for candidate in candidates:
            if pages >= limits.max_pages:
                break
            candidate["read"] = True  # attempted once, whatever the outcome
            pages += 1
            try:
                text = await deps.read(candidate["link"])
            except Exception:  # a refusal is "", and any other failure is the same as a refusal here
                text = ""
            if text:
                candidate["text"] = text[: limits.page_max_chars]
                candidate["fetched"] = True
                candidate["unverified"] = False
        return {"candidates": candidates, "counts": _count(state, "pages_read", pages)}

    async def score(state: WatchState) -> dict[str, Any]:
        candidates: list[Candidate] = [Candidate(**candidate) for candidate in state.get("candidates", [])]
        unscored = 0
        for batch in _batches(candidates, limits.batch_size):
            try:
                items = await deps.score(batch, state["profile"])
            except ExternalServiceError:
                items = []  # an unavailable model scores nothing: the batch is counted as unscored
            by_index = {int(item.get("index", 0)): item for item in items}
            for position, candidate in enumerate(batch, start=1):
                item = by_index.get(position)
                if item is None:
                    unscored += 1
                    candidate["relevance"] = 0
                    continue
                relevance = max(0, min(100, int(item.get("relevance", 0))))
                if not candidate["fetched"]:
                    relevance = min(relevance, limits.snippet_score_cap)  # a snippet never scores above the cap
                candidate["relevance"] = relevance
                company, title, justification = ground_fields(
                    candidate,
                    str(item.get("company", "")),
                    str(item.get("job_title", "")),
                    str(item.get("justification", "")),
                )
                candidate["company"], candidate["job_title"], candidate["justification"] = company, title, justification
        return {"candidates": candidates, "counts": _count(state, "unscored", unscored)}

    async def route(state: WatchState) -> dict[str, Any]:
        candidates: list[Candidate] = [Candidate(**candidate) for candidate in state.get("candidates", [])]
        # Only candidates that reach the threshold are classified: the others are not stored whatever they are.
        to_route = [candidate for candidate in candidates if candidate["relevance"] >= limits.min_score]
        for batch in _batches(to_route, limits.batch_size):
            try:
                items = await deps.route(batch, state["profile"])
            except ExternalServiceError:
                items = []
            by_index = {int(item.get("index", 0)): str(item.get("category", "")) for item in items}
            for position, candidate in enumerate(batch, start=1):
                category = by_index.get(position, CATEGORY_OTHER)
                candidate["category"] = (
                    category if category in (CATEGORY_JOB, CATEGORY_CERTIFICATION) else CATEGORY_OTHER
                )
        return {"candidates": candidates}

    async def persist(state: WatchState) -> dict[str, Any]:
        counts = dict(state.get("counts", {}))
        kept: list[Candidate] = []
        for candidate in state.get("candidates", []):
            if candidate["relevance"] < limits.min_score:
                counts["below_threshold"] = counts.get("below_threshold", 0) + 1
            elif candidate["category"] == CATEGORY_OTHER:
                counts["off_topic"] = counts.get("off_topic", 0) + 1  # neither an offer nor a certification
            else:
                kept.append(candidate)
        stored = await deps.persist(state["user_id"], kept) if kept else 0
        counts["stored"] = stored
        return {"stored": kept, "stored_count": stored, "counts": counts}

    nodes: dict[str, Callable[[WatchState], Awaitable[dict[str, Any]]]] = {
        "search": search,
        "dedupe": dedupe,
        "read": read,
        "score": score,
        "route": route,
        "persist": persist,
    }

    def _instrumented(name: str, node: Callable[[WatchState], Awaitable[dict[str, Any]]]):  # type: ignore[no-untyped-def]
        @wraps(node)
        async def run(state: WatchState) -> dict[str, Any]:
            started = time.perf_counter()
            await deps.on_step(name)
            update = await node(state)
            # Counts and durations only: never a query, a result, a page or a justification.
            logger.info("watch node=%s duration_ms=%d", name, int((time.perf_counter() - started) * 1000))
            return update

        return run

    builder = StateGraph(WatchState)
    for name in NODE_NAMES:
        builder.add_node(name, _instrumented(name, nodes[name]))
    builder.add_edge(START, "search")
    for source, target in pairwise(NODE_NAMES):
        builder.add_edge(source, target)
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)
