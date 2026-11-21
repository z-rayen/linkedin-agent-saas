"""The LangGraph checkpointer of the application workflow, started once per process by the application lifespan.

Postgres (production): one connection pool, opened at startup and closed at shutdown. Each checkpoint operation
borrows a connection from it; no run opens its own connection. The schema is owned by Alembic, not by setup().

Memory (tests and throwaway runs): an in-process saver. Nothing survives a restart.

The connection string comes from settings.checkpoint_url(). It is never logged, and never returned in an error
message by this module.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..core.exceptions import ExternalServiceError
from ..core.settings import Settings

logger = logging.getLogger(__name__)
_active: BaseCheckpointSaver[Any] | None = None
POOL_OPEN_TIMEOUT_SECONDS = 30.0


def active_saver() -> BaseCheckpointSaver[Any]:
    """The checkpointer of this process. Raises if the application lifespan has not started it."""
    if _active is None:
        raise RuntimeError("the checkpointer is not started: the application lifespan must run first")
    return _active


def clear_memory_checkpoints() -> None:
    """Forget every checkpoint of the in-memory backend. Used by tests that reuse one application for many cases.

    Does nothing for Postgres: its checkpoints are cleared with the database.
    """
    if isinstance(_active, MemorySaver):
        _active.storage.clear()
        _active.writes.clear()
        _active.blobs.clear()


@asynccontextmanager
async def checkpointer_lifespan(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Start the configured checkpointer for the lifetime of the application, and release it on shutdown."""
    global _active
    if settings.checkpoint_backend == "memory":
        saver: BaseCheckpointSaver[Any] = MemorySaver()
        _active = saver
        try:
            yield saver
        finally:
            _active = None
        return

    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
        settings.checkpoint_url(),
        min_size=1,
        max_size=settings.checkpoint_pool_max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        await pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT_SECONDS)
    except Exception as exc:
        # The message is generic on purpose: driver errors can echo the connection string.
        logger.error("checkpoint pool could not be opened error_type=%s", type(exc).__name__)
        raise ExternalServiceError("the workflow checkpoint database is unreachable") from None
    saver = AsyncPostgresSaver(pool)
    _active = saver
    try:
        yield saver
    finally:
        _active = None
        await pool.close()
