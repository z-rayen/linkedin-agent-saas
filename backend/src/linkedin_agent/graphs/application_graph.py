"""The application preparation graph.

    fetch_job -> analyze_job -> research_company -> retrieve_knowledge -> tailor_cv -> draft_email
        -> verify -> (problems and revisions left?) -> revise -> tailor_cv
                  -> (otherwise) persist_prepared -> await_human (interrupt) -> finalize

The graph prepares an application and pauses for a human. It has no path to approval or sending: its
dependencies do not include the mailer or the approval and send services, and a test enforces that.
Every node is a small function that calls an injected dependency. The grounding check is deterministic, and
the graph never loosens it: when the revision budget is spent, the problems travel with the application.

Job-page text and search results are untrusted data. They reach a model only through the prompt templates,
which wrap them in UNTRUSTED markers. Search results are kept for the user to verify, but are not sent to a model.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypedDict

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..core.exceptions import ExternalServiceError
from ..cv.email import build_body, email_facts, language_of
from ..cv.models import Fact, MasterCV, TailoredCV
from ..domain.outputs import JobAnalysis

logger = logging.getLogger(__name__)

NODE_NAMES = (
    "fetch_job",
    "analyze_job",
    "research_company",
    "retrieve_knowledge",
    "tailor_cv",
    "draft_email",
    "verify",
    "revise",
    "persist_prepared",
    "await_human",
    "finalize",
)


class PreparationState(TypedDict, total=False):
    user_id: int
    job_title: str
    job_text: str  # untrusted: a job page or pasted text
    language: str  # "fr" or "en": the user's choice, or detected from the title and the text
    job_url: str | None
    recipient_email: str
    company: str | None
    master_json: str  # the user's own master CV, validated
    analysis: dict[str, Any]  # validated JobAnalysis
    sources: list[dict[str, str]]  # search results, kept so the user can verify them; never sent to a model
    knowledge: list[str]  # the user's own knowledge base snippets
    cv_json: str  # validated TailoredCV
    subject: str
    body: str
    problems: list[dict[str, str]]  # the exact problems of the last check: kind, location, term, message
    revisions: int
    feedback: str
    application_id: int
    review_decision: str


@dataclass(frozen=True)
class GraphDeps:
    """Everything the graph does to the outside world. Tests replace each field with a fake."""

    fetch_page: Callable[[str], Awaitable[str]]  # text of a job page, "" when refused
    analyze: Callable[[str, str], Awaitable[JobAnalysis]]  # (job_title, job_text)
    research: Callable[[str], Awaitable[list[dict[str, str]]]]  # company name -> search results
    retrieve: Callable[[int, str], list[str]]  # (user_id, query) -> knowledge snippets
    tailor: Callable[[str, str, MasterCV, str], Awaitable[TailoredCV]]  # (title, job_text, master, feedback)
    draft: Callable[[str, str], Awaitable[dict[str, str]]]  # (job_title, feedback) -> subject, opening, closing
    verify: Callable[[TailoredCV, str, str, MasterCV, str], list[str]]  # (cv, subject, body, master, context)
    persist: Callable[[PreparationState], Awaitable[int]]  # stores the application as prepared, returns its id
    on_step: Callable[[str], Awaitable[None]]  # progress for the status endpoint
    max_revisions: int
    email_max_facts: int  # facts quoted one per sentence in the email


class PreparationError(Exception):
    """A preparation that cannot continue for a reason the user must act on."""


def _problem_dict(problem: object) -> dict[str, str]:
    """A problem as the graph keeps it: a plain dict, which survives the checkpoint and the storage unchanged."""
    if hasattr(problem, "as_dict"):
        return dict(problem.as_dict())
    return {"kind": "unknown", "location": "", "term": "", "message": str(problem)}


def _revisable(state: "PreparationState") -> list[dict[str, str]]:
    """The problems a revision can fix: every kind except "mandatory" (a missing part of the master CV)."""
    return [problem for problem in state.get("problems", []) if problem.get("kind") != "mandatory"]


def augmented_master(master_json: str, knowledge: list[str]) -> MasterCV:
    """The master CV plus the knowledge snippets, as facts with stable ids. Grounding checks against this."""
    master = MasterCV.model_validate_json(master_json)
    extra = [Fact(id=f"kb-{index}", kind="experience", text=text) for index, text in enumerate(knowledge, start=1)]
    return master.model_copy(update={"facts": [*master.facts, *extra]})


def _instrumented(name: str, deps: GraphDeps, node: Callable[[PreparationState], Awaitable[dict[str, Any]]]):  # type: ignore[no-untyped-def]
    @wraps(node)
    async def run(state: PreparationState) -> dict[str, Any]:
        started = time.perf_counter()
        await deps.on_step(name)
        try:
            update = await node(state)
        except GraphInterrupt:
            raise  # a pause for human review is the intended behaviour, not a failure
        except Exception as exc:
            logger.error("graph node=%s failed error_type=%s", name, type(exc).__name__)
            raise
        logger.info("graph node=%s duration_ms=%d", name, int((time.perf_counter() - started) * 1000))
        return update

    return run


def build_graph(deps: GraphDeps, checkpointer: Any = None) -> Any:
    """The compiled graph. The checkpointer is what makes the human pause survive a restart."""

    async def fetch_job(state: PreparationState) -> dict[str, Any]:
        if state.get("job_text"):
            return {}
        url = state.get("job_url")
        if not url:
            raise PreparationError("no job text and no job URL were given")
        text = await deps.fetch_page(url)
        if not text:
            raise PreparationError("the job page could not be read: paste the job text instead")
        return {"job_text": text}

    async def analyze_job(state: PreparationState) -> dict[str, Any]:
        try:
            analysis = await deps.analyze(state["job_title"], state["job_text"])
        except ExternalServiceError:
            analysis = JobAnalysis()  # an unreadable analysis is not a reason to stop the preparation
        return {"analysis": analysis.model_dump()}

    async def research_company(state: PreparationState) -> dict[str, Any]:
        company = state.get("company") or (state.get("analysis") or {}).get("company")
        if not company:
            return {"sources": []}
        try:
            sources = await deps.research(str(company))
        except Exception:
            sources = []
        return {"sources": sources}

    async def retrieve_knowledge(state: PreparationState) -> dict[str, Any]:
        requirements = " ".join((state.get("analysis") or {}).get("requirements", []))
        query = f"{state['job_title']} {requirements}".strip()
        return {"knowledge": deps.retrieve(state["user_id"], query)}

    def _master(state: PreparationState) -> MasterCV:
        return augmented_master(state["master_json"], state.get("knowledge", []))

    async def tailor_cv(state: PreparationState) -> dict[str, Any]:
        cv = await deps.tailor(state["job_title"], state["job_text"], _master(state), state.get("feedback") or "(none)")
        return {"cv_json": cv.model_dump_json()}

    async def draft_email(state: PreparationState) -> dict[str, Any]:
        # The model writes the subject, the opening and the closing. The facts, the greeting and the sign-off are
        # added here, from the facts the tailored CV cites, so the email never holds a sentence the model wrote
        # about the candidate.
        cv = TailoredCV.model_validate_json(state["cv_json"])
        master = _master(state)
        mail = await deps.draft(state["job_title"], state.get("feedback") or "(none)")
        body = build_body(
            state.get("language") or language_of(state["job_title"], state.get("job_text", "")),
            mail.get("opening", ""),
            mail.get("closing", ""),
            email_facts(cv, master, deps.email_max_facts),
            master,
        )
        return {"subject": mail["subject"], "body": body}

    async def verify(state: PreparationState) -> dict[str, Any]:
        cv = TailoredCV.model_validate_json(state["cv_json"])
        context = " ".join(part for part in (state["job_title"], state.get("company") or "") if part)
        problems = [_problem_dict(p) for p in deps.verify(cv, state["subject"], state["body"], _master(state), context)]
        return {"problems": problems}

    def after_verify(state: PreparationState) -> str:
        if _revisable(state) and state.get("revisions", 0) < deps.max_revisions:
            return "revise"
        return "persist_prepared"

    async def revise(state: PreparationState) -> dict[str, Any]:
        # The exact problems go back to the model as data. The check itself is never relaxed.
        # Only what the model can fix is sent back. A missing part of the master CV is not a revision matter.
        feedback = "\n".join(problem["message"] for problem in _revisable(state))
        return {"revisions": state.get("revisions", 0) + 1, "feedback": feedback}

    async def persist_prepared(state: PreparationState) -> dict[str, Any]:
        return {"application_id": await deps.persist(state)}

    async def await_human(state: PreparationState) -> dict[str, Any]:
        decision = interrupt(
            {
                "application_id": state.get("application_id"),
                "status": "prepared",
                "problems": state.get("problems", []),
            }
        )
        return {"review_decision": str(decision)}

    async def finalize(state: PreparationState) -> dict[str, Any]:
        return {}

    builder = StateGraph(PreparationState)
    nodes: dict[str, Callable[[PreparationState], Awaitable[dict[str, Any]]]] = {
        "fetch_job": fetch_job,
        "analyze_job": analyze_job,
        "research_company": research_company,
        "retrieve_knowledge": retrieve_knowledge,
        "tailor_cv": tailor_cv,
        "draft_email": draft_email,
        "verify": verify,
        "revise": revise,
        "persist_prepared": persist_prepared,
        "await_human": await_human,
        "finalize": finalize,
    }
    for name in NODE_NAMES:
        builder.add_node(name, _instrumented(name, deps, nodes[name]))

    builder.add_edge(START, "fetch_job")
    builder.add_edge("fetch_job", "analyze_job")
    builder.add_edge("analyze_job", "research_company")
    builder.add_edge("research_company", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "tailor_cv")
    builder.add_edge("tailor_cv", "draft_email")
    builder.add_edge("draft_email", "verify")
    builder.add_conditional_edges("verify", after_verify, {"revise": "revise", "persist_prepared": "persist_prepared"})
    builder.add_edge("revise", "tailor_cv")
    builder.add_edge("persist_prepared", "await_human")
    builder.add_edge("await_human", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
