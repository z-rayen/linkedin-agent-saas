import asyncio
import sys

if sys.platform == "win32":
    # psycopg (async) cannot run on Windows' default ProactorEventLoop. The selector loop works everywhere psycopg does.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core.exceptions import AppError
from .core.logging import RequestIdMiddleware, setup_logging
from .core.responses import UTF8JSONResponse
from .core.settings import Settings, get_settings, retired_variable_warnings
from .cv.compile import ensure_tectonic_at_startup
from .infra.checkpoint import checkpointer_lifespan
from .infra.embeddings import get_embedder
from .routers import agent, applications, apply, auth, content, engage, oauth, profile, runs, watch
from .services import preparations, research_runs, retention

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)
ENV_FILE = Path(".env")  # the same file settings reads, relative to the working directory


def warn_about_retired_variables() -> None:
    """Retired variables are ignored by settings. Say so, instead of letting the change go unnoticed."""
    no_env_file = os.environ.get("LINKEDIN_AGENT_NO_ENV_FILE") == "1"  # mirrors core/settings.py
    dotenv = {} if no_env_file or not ENV_FILE.is_file() else dotenv_values(ENV_FILE)
    for message in retired_variable_warnings(dict(os.environ), dotenv):
        logger.warning(message)


async def warm_embedding_model(current: Settings) -> None:
    """Load the embedding model before the first request, in a worker thread (the download can take minutes).

    A failure is logged, not fatal: the application works without retrieval, and the first request tries again.
    """
    if not current.embedding_warm_on_startup:
        return
    try:
        await asyncio.to_thread(get_embedder().warm)
        logger.info("embedding model ready")
    except Exception as exc:
        logger.error("embedding model not loaded at startup error_type=%s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Everything long-lived is set up here, once per process: the checkpoint pool, the startup checks, recovery."""
    current = get_settings()
    ensure_tectonic_at_startup(current)
    preparations.check_stall_threshold(current)
    await warm_embedding_model(current)
    async with checkpointer_lifespan(current):
        # A restart must not leave a preparation stuck: stale runs are resumed from their checkpoint or failed.
        await preparations.recover_interrupted()
        await research_runs.recover_interrupted()
        purge = asyncio.create_task(retention.purge_forever())  # retention: once now, then periodically
        try:
            yield
        finally:
            purge.cancel()


warn_about_retired_variables()
app = FastAPI(
    title="LinkedIn Agent SaaS",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
    description=(
        "Applications are prepared asynchronously: `POST /applications` returns 202 with a `run_id`, and "
        "`GET /applications/preparations/{run_id}` reports progress until the application is `awaiting_review`. "
        "Approval and sending are separate, explicit steps."
    ),
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Map domain errors to HTTP responses. Keeps the `detail` field the frontend already reads."""
    return UTF8JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.details}
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Errors raised as HTTPException (http_error in core/exceptions.py) keep FastAPI's `detail` and get UTF-8 too."""
    return UTF8JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return UTF8JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(content.router)
app.include_router(watch.router)
app.include_router(profile.router)
app.include_router(agent.router)
app.include_router(engage.router)
app.include_router(apply.router)
app.include_router(applications.router)
app.include_router(runs.router)


@app.get("/")
def root():
    return {"status": "ok", "message": "LinkedIn Agent API is running"}
