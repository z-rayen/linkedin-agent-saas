"""The research agent graph: plan, search, read, reflect, synthesize, and an optional draft post.

    plan -> search -> read -> reflect -> search      (reflection asks for more evidence, at most research_max_loops)
                                      -> synthesize -> draft_post -> END   (only when the user asked for a post)
                                                    -> END

Every node calls an injected dependency. The graph has no path to the LinkedIn publisher: its dependencies do not
include it, and a test enforces that. A post is only ever saved as a draft in the content queue.

Search results and page text are untrusted data. They reach a model only through prompt templates, which wrap them in
UNTRUSTED markers, and the graph never turns them into a query, a link to follow or an instruction: the only links
that are read are the ones the search returned, and every fetch goes through safe_http in the service layer.

The answer is checked by code (graphs/research_checks.py): citations name shown sources, a statement rests on a full
page or is labelled as a snippet, numbers match their sources by value, relative times are removed, and every answer
states the date its sources were retrieved.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..clock import utcnow
from ..core.exceptions import ExternalServiceError
from ..core.settings import ResearchLanguage
from .research_checks import Evidence, check_answer, check_citations, detect_language, is_relevant

logger = logging.getLogger(__name__)

NODE_NAMES = ("plan", "search", "read", "reflect", "synthesize", "draft_post")
NO_SOURCES_MESSAGE = "No usable source was found for this request. Try a more specific request."
ANSWER_UNAVAILABLE_MESSAGE = "The answer could not be produced right now. Retry in a minute."
# Storage bounds for text that comes from search results. They protect the database, not the cost of a run:
# the cost limits (queries, pages, characters per prompt, loops) are all in settings.
TITLE_MAX_CHARS = 200
LINK_MAX_CHARS = 2048
FALLBACK_QUERY_MAX_CHARS = 200


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
    fetched: bool  # the full page was read: its text is the page, not a snippet
    read: bool  # a fetch was attempted for this source (once, whatever the outcome)


class ResearchState(TypedDict, total=False):
    user_id: int
    request: str
    profile: dict[str, str]
    history: str
    draft_post: bool
    retrieved_at: str  # the date the sources were retrieved (ISO, UTC)
    language: str  # the language of the request, which is the language of the answer
    pending_queries: list[str]
    queries_run: list[str]
    sources: list[Source]
    seen_links: list[str]
    loop: int  # search rounds run so far; round 1 is the first search, not a refinement
    new_sources: int  # new links found by the last search round
    off_topic: int  # search results dropped by the relevance filter
    pages_read: int
    sufficient: bool
    shown: list[Source]  # the sources the synthesis was shown; their numbers are the ones it may cite
    answer: str
    answer_ok: bool
    claims: list[dict[str, Any]]  # each statement of the answer, with its sources and their basis
    unsupported: dict[str, int]  # statements dropped by the checks, by reason (counts only)
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
    min_relevance: float  # a search result must match this share of the query's topic words (title, summary, domain)
    min_title_matches: int  # ... and this many of them in its title or domain (capped by the query's term count)
    require_citations: bool  # an uncited sentence is dropped


@dataclass(frozen=True)
class Reflection:
    sufficient: bool
    queries: list[str]


@dataclass(frozen=True)
class ResearchDeps:
    """Everything the graph does to the outside world. Tests replace each field with a fake."""

    plan: Callable[[str, dict[str, str], str], Awaitable[list[str]]]  # (request, profile, history) -> queries
    search: Callable[[str], Awaitable[list[Hit]]]  # query -> results
    read: Callable[[str], Awaitable[str]]  # url -> page text, "" when refused or unreachable
    reflect: Callable[[str, dict[str, str], list[Source]], Awaitable[Reflection]]
    # (request, profile, history, shown sources, language code) -> answer with [n] citations
    synthesize: Callable[[str, dict[str, str], str, list[Source], str], Awaitable[str]]
    draft: Callable[[str, dict[str, str], str], Awaitable[str]]  # (request, profile, answer) -> post text
    save_draft: Callable[[int, str, str], Awaitable[int]]  # (user_id, title, text) -> content queue id
    on_step: Callable[[str], Awaitable[None]]
    limits: ResearchLimits
    languages: dict[str, ResearchLanguage]  # the answer's wording and number conventions, by language code
    default_language: str


def numbered_block(number: int, source: Source) -> str:
    basis = "full page" if source["fetched"] else "search snippet only"
    return f"[{number}] {source['title']} ({basis})\nURL: {source['link']}\n{source['text']}"


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
    # A query's topic words are compared with the stopwords of every configured language: a word that is a stopword
    # in one of them is not a topic.
    stopwords = frozenset(word for pack in deps.languages.values() for word in pack.stopwords)

    async def plan(state: ResearchState) -> dict[str, Any]:
        try:
            queries = await deps.plan(state["request"], state["profile"], state.get("history", ""))
        except ExternalServiceError:
            queries = []
        cleaned = [query.strip() for query in queries if query.strip()][: limits.max_queries]
        if not cleaned:
            cleaned = [state["request"][:FALLBACK_QUERY_MAX_CHARS]]
        language = detect_language(state["request"], deps.languages, deps.default_language)
        return {
            "retrieved_at": utcnow().date().isoformat(),
            "language": language,
            "pending_queries": cleaned,
            "queries_run": [],
            "sources": [],
            "seen_links": [],
            "loop": 0,
            "new_sources": 0,
            "off_topic": 0,
            "pages_read": 0,
            "sufficient": False,
        }

    async def search(state: ResearchState) -> dict[str, Any]:
        sources: list[Source] = [Source(**source) for source in state.get("sources", [])]
        seen = set(state.get("seen_links", []))
        queries_run = list(state.get("queries_run", []))
        off_topic = state.get("off_topic", 0)
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
                relevant = is_relevant(
                    query,
                    str(hit["title"]),
                    str(hit["summary"]),
                    link,
                    stopwords=stopwords,
                    min_share=limits.min_relevance,
                    min_title_matches=limits.min_title_matches,
                )
                if not relevant:
                    off_topic += 1  # not read, not shown, not cited
                    continue
                seen.add(link)
                snippet = str(hit["summary"])[: limits.page_max_chars]
                sources.append(
                    Source(
                        title=str(hit["title"])[:TITLE_MAX_CHARS],
                        link=link,
                        snippet=snippet,
                        text=snippet,
                        fetched=False,
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
            "off_topic": off_topic,
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
                source["fetched"] = True
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
        # Full pages first: a page the model can cite for a precise fact is never left out behind a snippet.
        ordered = sorted(state.get("sources", []), key=lambda source: not source["fetched"])
        shown = fit_sources(ordered, limits.context_max_chars)
        if not shown:
            return {
                "shown": [],
                "answer": NO_SOURCES_MESSAGE,
                "answer_ok": False,
                "claims": [],
                "unsupported": {},
                "citations": [],
                "removed_citations": 0,
            }
        try:
            raw = await deps.synthesize(
                state["request"],
                state["profile"],
                state.get("history", ""),
                shown,
                state.get("language", deps.default_language),
            )
        except ExternalServiceError:
            return {
                "shown": shown,
                "answer": ANSWER_UNAVAILABLE_MESSAGE,
                "answer_ok": False,
                "claims": [],
                "unsupported": {},
                "citations": [],
                "removed_citations": 0,
            }
        cited = check_citations(raw, len(shown))
        if cited.removed:
            logger.warning("research citations removed count=%d", cited.removed)
        language = deps.languages.get(state.get("language", ""), deps.languages[deps.default_language])
        checked = check_answer(
            cited.text,
            # The title is part of the source: a figure in a title ("Plus de 1 250 offres") is a figure of the source.
            [Evidence(text=f"{source['title']} {source['text']}", fetched=source["fetched"]) for source in shown],
            language=language,
            require_citations=limits.require_citations,
            known_labels=tuple(pack.snippet_label for pack in deps.languages.values()),
        )
        unsupported = {
            "uncited": checked.dropped_uncited,
            "relative_time": checked.dropped_relative_time,
            "numbers_not_found": checked.dropped_numbers_not_found,
            "numbers_snippet_only": checked.dropped_numbers_snippet_only,
        }
        logger.info(
            "research checks statements=%d dropped=%d labelled=%d cells_filled=%d",
            len(checked.claims),
            sum(unsupported.values()),
            checked.labelled,
            checked.cells_filled,
        )
        answer_ok = bool(checked.text)
        # The answer ends with what the reader needs to read it: what was removed and why it is not "nothing found",
        # and the date the sources were retrieved ("current" means nothing without it).
        parts = [checked.text if answer_ok else language.nothing_checked]
        if checked.dropped_relative_time:
            parts.append(language.relative_time_removed.format(count=checked.dropped_relative_time))
        parts.append(language.retrieval_line.format(date=state["retrieved_at"]))
        return {
            "shown": shown,
            "answer": "\n\n".join(parts),
            "answer_ok": answer_ok,
            "claims": [
                {"text": claim.text, "sources": list(claim.sources), "basis": claim.basis} for claim in checked.claims
            ],
            "unsupported": unsupported,
            "citations": cited.cited,
            "removed_citations": cited.removed,
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
