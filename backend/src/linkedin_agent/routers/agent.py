from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth_utils import get_current_user
from ..clock import utcnow
from ..core.exceptions import NotFoundError, http_error
from ..core.settings import get_settings
from ..database import get_db
from ..services import research_runs
from ..services.agent_service import run_agent

router = APIRouter(prefix="/agent", tags=["agent"])
RESEARCH_HISTORY_MESSAGES = 6
RESEARCH_HISTORY_MESSAGE_CHARS = 300


class ResearchRequest(BaseModel):
    request: str = Field(min_length=1, max_length=research_runs.MAX_REQUEST_CHARS)
    draft_post: bool = Field(
        default=False, description="Ask for a LinkedIn post, saved as a draft in the content queue."
    )


class AgentRequest(BaseModel):
    request: str


def _load_profile(db: Session, user_id: int) -> dict:
    """The user's profile fields, falling back to the configured defaults."""
    settings = get_settings()
    row = db.query(models.ProfileConfig).filter(models.ProfileConfig.user_id == user_id).first()
    return {
        "field": row.field if row and row.field else settings.default_field,
        "skills": row.skills if row and row.skills else settings.default_skills,
        "level": row.level if row and row.level else settings.default_level,
    }


@router.post("/ask")
async def ask(
    payload: AgentRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    profile = _load_profile(db, current_user.id)

    # Historique récent (6 derniers messages, dans l'ordre chronologique)
    recent = (
        db.query(models.AgentMessage)
        .filter(models.AgentMessage.user_id == current_user.id)
        .order_by(models.AgentMessage.created_at.desc())
        .limit(6)
        .all()
    )
    history = list(reversed(recent))

    result = await run_agent(payload.request, profile, history)

    # Sauvegarde de l'échange
    db.add(models.AgentMessage(user_id=current_user.id, role="user", content=payload.request))
    db.add(models.AgentMessage(user_id=current_user.id, role="assistant", content=result["answer"]))

    # Action : le post généré va directement dans la file d'attente en brouillon
    post_saved = False
    if result["post_text"]:
        db.add(
            models.ContentQueue(
                user_id=current_user.id,
                source="agent",
                title=payload.request[:80],
                post_text=result["post_text"],
                status="draft",
            )
        )
        post_saved = True

    db.commit()
    result["post_saved"] = post_saved
    return result


@router.post(
    "/research",
    status_code=202,
    summary="Start a research run (asynchronous)",
    description=(
        "Accepts a question and returns 202 at once with a `run_id` and `status: queued`. The research (searches, page "
        "reads, reflection, a cited answer) runs in the background.\n\n"
        "Flow: 1) POST here. 2) Poll `GET /agent/research/{run_id}` until `status` is `done` (read `result`) or "
        "`failed` (read `error`). `result` holds the answer, the retrieval date, the `claims` with their sources and "
        "whether each rests on a full page (`fetched`) or a snippet, the cited `sources`, the `also_found` links, and "
        "the `unsupported` counts (statements removed, by reason).\n\n"
        "A 409 means the same request is already running. The run id is random and only its owner can read it; "
        "anyone else gets the same 404 as for an unknown id. `POST /agent/ask` is the earlier synchronous endpoint and "
        "is unchanged."
    ),
)
async def start_research(
    payload: ResearchRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    profile = _load_profile(db, current_user.id)
    recent = (
        db.query(models.AgentMessage)
        .filter(models.AgentMessage.user_id == current_user.id)
        .order_by(models.AgentMessage.created_at.desc())
        .limit(RESEARCH_HISTORY_MESSAGES)
        .all()
    )
    history = "\n".join(f"{m.role}: {m.content[:RESEARCH_HISTORY_MESSAGE_CHARS]}" for m in reversed(recent))
    run = research_runs.start(
        db, current_user.id, payload.request, draft_post=payload.draft_post, profile=profile, history=history
    )
    background.add_task(research_runs.execute, run.id)
    return research_runs.status_of(run, utcnow(), get_settings().preparation_stale_seconds)


@router.get(
    "/research/{run_id}",
    summary="Status and result of a research run",
    description=(
        "`run_id` is the random token returned by `POST /agent/research`. Only the user who started the run can read "
        "it: "
        "for anyone else, and for an unknown id, the answer is the same 404."
    ),
)
def get_research(run_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    run = research_runs.find_run(db, current_user.id, run_id)
    if run is None:
        raise http_error(NotFoundError("Research run not found"))
    return research_runs.status_of(run, utcnow(), get_settings().preparation_stale_seconds)


@router.get("/history")
def get_history(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    msgs = (
        db.query(models.AgentMessage)
        .filter(models.AgentMessage.user_id == current_user.id)
        .order_by(models.AgentMessage.created_at.asc())
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in msgs]


@router.delete("/history")
def clear_history(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db.query(models.AgentMessage).filter(models.AgentMessage.user_id == current_user.id).delete()
    db.commit()
    return {"status": "cleared"}
