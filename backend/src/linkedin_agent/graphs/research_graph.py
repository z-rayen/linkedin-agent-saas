"""The research agent graph: plan, search, read, reflect, synthesize, and an optional draft post.

    plan -> search -> read -> reflect -> search      (reflection asks for more evidence, at most research_max_loops)
                                      -> synthesize -> draft_post -> END   (only when the user asked for a post)
                                                    -> END

Every node calls an injected dependency. The graph has no path to the LinkedIn publisher: its dependencies do not
include it, and a test enforces that. A post is only ever saved as a draft in the content queue.

Search results and page text are untrusted data. They reach a model only through prompt templates, which wrap them in
UNTRUSTED markers, and the graph never turns them into a query, a link to follow or an instruction: the only links
that are read are the ones the search returned, and every fetch goes through safe_http in the service layer.

Citations are checked here, deterministically: each [n] in the answer must name a source the model was shown. Any
other number is removed from the text and counted. A number is never kept, and never invented.
"""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

NODE_NAMES = ("plan", "search", "read", "reflect", "synthesize", "draft_post")
NO_SOURCES_MESSAGE = "No usable source was found for this request. Try a more specific request."
ANSWER_UNAVAILABLE_MESSAGE = "The answer could not be produced right now. Retry in a minute."
# Storage bounds for text that comes from search results. They protect the database, not the cost of a run:
# the cost limits (queries, pages, characters per prompt, loops) are all in settings.
TITLE_MAX_CHARS = 200
LINK_MAX_CHARS = 2048
FALLBACK_QUERY_MAX_CHARS = 200
CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")
SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?])")
REPEATED_SPACES = re.compile(r"[ \t]{2,}")


class Hit(TypedDict):
    """One search result, as the search provider returns it."""

    title: str
    link: str
    summary: str


class Source(TypedDict):
    title: str
    link: str
    snippet: str  # the search result's summary
    text: str  # the page text when the page was read, otherwise the snippet
    read: bool  # a fetch was attempted for this source (once, whatever the outcome)


class ResearchState(TypedDict, total=False):
    user_id: int
    request: str
    profile: dict[str, str]
    history: str
    draft_post: bool
    pending_queries: list[str]
    queries_run: list[str]
    sources: list[Source]
    seen_links: list[str]
    loop: int  # search rounds run so far; round 1 is the first search, not a refinement
    new_sources: int  # new links found by the last search round
    pages_read: int
    sufficient: bool
    shown: list[Source]  # the sources the synthesis was shown; their numbers are the ones it may cite
    answer: str
    answer_ok: bool
    citations: list[int]
    removed_citations: int
    post_id: int | None


@dataclass(frozen=True)
class ResearchLimits:
    max_loops: int  # how many times reflection may send the run back to search
    max_queries: int
    results_per_query: int
    max_sources: int
    max_pages: int
    page_max_chars: int
    context_max_chars: int


@dataclass(frozen=True)
class Reflection:
    sufficient: bool
    queries: list[str]


@dataclass(frozen=True)
class CitationCheck:
    text: str
    cited: list[int]  # valid numbers, in order of first use
    removed: int  # markers removed because they named no source that was shown


@dataclass(frozen=True)
class ResearchDeps:
    """Everything the graph does to the outside world. Tests replace each field with a fake."""

    plan: Callable[[str, dict[str, str], str], Awaitable[list[str]]]  # (request, profile, history) -> queries
    search: Callable[[str], Awaitable[list[Hit]]]  # query -> results
    read: Callable[[str], Awaitable[str]]  # url -> page text, "" when refused or unreachable
    reflect: Callable[[str, dict[str, str], list[Source]], Awaitable[Reflection]]
    synthesize: Callable[[str, dict[str, str], str, list[Source]], Awaitable[str]]  # -> answer with [n] citations
    draft: Callable[[str, dict[str, str], str], Awaitable[str]]  # (request, profile, answer) -> post text
    save_draft: Callable[[int, str, str], Awaitable[int]]  # (user_id, title, text) -> content queue id
    on_step: Callable[[str], Awaitable[None]]
    limits: ResearchLimits


def numbered_block(number: int, source: Source) -> str:
    return f"[{number}] {source['title']}\nURL: {source['link']}\n{source['text']}"


def fit_sources(sources: list[Source], max_chars: int) -> list[Source]:
    """The leading sources whose numbered blocks fit in max_chars.

    The rest are not shown to the model, so the model cannot cite them and they are not reported as used.
    """
    kept: list[Source] = []
    used = 0
    for number, source in enumerate(sources, start=1):
        size = len(numbered_block(number, source)) + 2  # the separator between blocks
        if used + size > max_chars:
            break
        kept.append(source)
        used += size
    return kept


def check_citations(answer: str, source_count: int) -> CitationCheck:
    """Keep the [n] markers that name a shown source, remove the others. Deterministic, no model involved."""
    cited: list[int] = []
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        number = int(match.group(1))
        if 1 <= number <= source_count:
            if number not in cited:
                cited.append(number)
            return match.group(0)
        removed += 1
        return ""

    text = CITATION_PATTERN.sub(replace, answer)
    if removed:
        text = SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
        text = REPEATED_SPACES.sub(" ", text)
    return CitationCheck(text=text.strip(), cited=cited, removed=removed)


def _instrumented(name: str, deps: ResearchDeps, node: Callable[[ResearchState], Awaitable[dict[str, Any]]]):  # type: ignore[no-untyped-def]
    @wraps(node)
    async def run(state: ResearchState) -> dict[str, Any]:
        started = time.perf_counter()
        await deps.on_step(name)
        update = await node(state)
        # Counts and durations only: never a prompt, an answer, a query or page text.
        logger.info(
            "research node=%s duration_ms=%d loop=%d",
            name,
            int((time.perf_counter() - started) * 1000),
            state.get("loop", 0),
        )
        return update

    return run


def after_reflect(state: ResearchState, limits: ResearchLimits) -> str:
    """Search again only when reflection wants more, there is something to search, and the loop budget allows it.

    `loop` counts the search rounds run. After round k, k - 1 refinements have been made, so one more is allowed
    while k <= max_loops. A round that found no new link is not progress: the run stops rather than repeat itself.
    """
    if state.get("sufficient", True) or not state.get("pending_queries"):
        return "synthesize"
    if state.get("loop", 0) > limits.max_loops:
        logger.info("research loops exhausted rounds=%d", state.get("loop", 0))
        return "synthesize"
    if state.get("new_sources", 0) == 0:
        logger.info("research stopped: the last search round found no new source")
        return "synthesize"
    return "search"


def after_synthesize(state: ResearchState) -> str:
    """A post is drafted only when the user asked for one and there is an answer to base it on."""
    if state.get("draft_post") and state.get("answer_ok") and state.get("shown"):
        return "draft_post"
    return "done"


def build_graph(deps: ResearchDeps, checkpointer: Any = None) -> Any:
    """The compiled research graph. The checkpointer, when given, lets a run resume after a restart."""
    limits = deps.limits

    async def plan(state: ResearchState) -> dict[str, Any]:
        try:
            queries = await deps.plan(state["request"], state["profile"], state.get("history", ""))
        except ExternalServiceError:
            queries = []
        cleaned = [query.strip() for query in queries if query.strip()][: limits.max_queries]
        if not cleaned:
            cleaned = [state["request"][:FALLBACK_QUERY_MAX_CHARS]]
        return {
            "pending_queries": cleaned,
            "queries_run": [],
            "sources": [],
            "seen_links": [],
            "loop": 0,
            "new_sources": 0,
            "pages_read": 0,
            "sufficient": False,
        }

    async def search(state: ResearchState) -> dict[str, Any]:
        sources: list[Source] = [Source(**source) for source in state.get("sources", [])]
        seen = set(state.get("seen_links", []))
        queries_run = list(state.get("queries_run", []))
        new = 0
        for query in state.get("pending_queries", []):
            queries_run.append(query)
            try:
                hits = await deps.search(query)
            except Exception as exc:  # one failed query does not stop the run
                logger.warning(
                    "research search failed query_index=%d error_type=%s", len(queries_run), type(exc).__name__
                )
                continue
            for hit in hits[: limits.results_per_query]:
                link = str(hit["link"])[:LINK_MAX_CHARS]
                if not link or link in seen or len(sources) >= limits.max_sources:
                    continue
                seen.add(link)
                snippet = str(hit["summary"])[: limits.page_max_chars]
                sources.append(
                    Source(
                        title=str(hit["title"])[:TITLE_MAX_CHARS],
                        link=link,
                        snippet=snippet,
                        text=snippet,
                        read=False,
                    )
                )
                new += 1
        return {
            "sources": sources,
            "seen_links": sorted(seen),
            "queries_run": queries_run,
            "pending_queries": [],
            "loop": state.get("loop", 0) + 1,
            "new_sources": new,
        }

    async def read(state: ResearchState) -> dict[str, Any]:
        sources: list[Source] = [Source(**source) for source in state.get("sources", [])]
        pages = state.get("pages_read", 0)
        for source in sources:
            if pages >= limits.max_pages:
                break
            if source["read"]:
                continue
            source["read"] = True  # attempted once, whatever the outcome
            pages += 1
            try:
                text = await deps.read(source["link"])
            except Exception:  # the read dependency already maps refusals to ""; this is the last line of defence
                text = ""
            if text:
                source["text"] = text[: limits.page_max_chars]
        return {"sources": sources, "pages_read": pages}

    async def reflect(state: ResearchState) -> dict[str, Any]:
        try:
            reflection = await deps.reflect(state["request"], state["profile"], state.get("sources", []))
        except ExternalServiceError:
            # Reflection is unavailable: answer with the evidence there is, rather than fail the run.
            reflection = Reflection(sufficient=True, queries=[])
        queries = [query.strip() for query in reflection.queries if query.strip()][: limits.max_queries]
        return {"sufficient": reflection.sufficient, "pending_queries": queries}

    async def synthesize(state: ResearchState) -> dict[str, Any]:
        shown = fit_sources(state.get("sources", []), limits.context_max_chars)
        if not shown:
            return {
                "shown": [],
                "answer": NO_SOURCES_MESSAGE,
                "answer_ok": False,
                "citations": [],
                "removed_citations": 0,
            }
        try:
            raw = await deps.synthesize(state["request"], state["profile"], state.get("history", ""), shown)
        except ExternalServiceError:
            return {
                "shown": shown,
                "answer": ANSWER_UNAVAILABLE_MESSAGE,
                "answer_ok": False,
                "citations": [],
                "removed_citations": 0,
            }
        checked = check_citations(raw, len(shown))
        if checked.removed:
            logger.warning("research citations removed count=%d", checked.removed)
        return {
            "shown": shown,
            "answer": checked.text,
            "answer_ok": bool(checked.text),
            "citations": checked.cited,
            "removed_citations": checked.removed,
        }

    async def draft_post(state: ResearchState) -> dict[str, Any]:
        try:
            post = (await deps.draft(state["request"], state["profile"], state["answer"])).strip()
        except ExternalServiceError:
            logger.warning("research post draft unavailable")
            return {"post_id": None}
        if not post:
            return {"post_id": None}
        # The draft goes into the content queue. Publishing is a separate, manual endpoint, never reached from here.
        post_id = await deps.save_draft(state["user_id"], state["request"][:TITLE_MAX_CHARS], post)
        return {"post_id": post_id}

    nodes: dict[str, Callable[[ResearchState], Awaitable[dict[str, Any]]]] = {
        "plan": plan,
        "search": search,
        "read": read,
        "reflect": reflect,
        "synthesize": synthesize,
        "draft_post": draft_post,
    }
    builder = StateGraph(ResearchState)
    for name in NODE_NAMES:
        builder.add_node(name, _instrumented(name, deps, nodes[name]))
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "search")
    builder.add_edge("search", "read")
    builder.add_edge("read", "reflect")
    builder.add_conditional_edges(
        "reflect", lambda state: after_reflect(state, limits), {"search": "search", "synthesize": "synthesize"}
    )
    builder.add_conditional_edges("synthesize", after_synthesize, {"draft_post": "draft_post", "done": END})
    builder.add_edge("draft_post", END)
    return builder.compile(checkpointer=checkpointer)
