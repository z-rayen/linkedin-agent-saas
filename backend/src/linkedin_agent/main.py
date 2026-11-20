import asyncio
import sys

if sys.platform == "win32":
    # psycopg (async) cannot run on Windows' default ProactorEventLoop. The selector loop works everywhere psycopg does.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.exceptions import AppError
from .core.logging import RequestIdMiddleware, setup_logging
from .core.settings import get_settings
from .routers import agent, applications, apply, auth, content, engage, oauth, profile, watch
from .services import preparations

settings = get_settings()
setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A restart must not leave a preparation stuck: stale runs are resumed from their checkpoint or failed.
    await preparations.recover_interrupted()
    yield


app = FastAPI(title="LinkedIn Agent SaaS", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Map domain errors to HTTP responses. Keeps the `detail` field the frontend already reads."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


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


@app.get("/")
def root():
    return {"status": "ok", "message": "LinkedIn Agent API is running"}
