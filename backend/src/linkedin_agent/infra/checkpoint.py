"""The LangGraph checkpointer connection (psycopg v3, separate from the psycopg2 engine of SQLAlchemy).

The connection string comes from settings.checkpoint_url(). It is never logged, and never returned in an error
message by this module.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ..core.settings import get_settings


@asynccontextmanager
async def open_checkpointer(conninfo: str | None = None) -> AsyncIterator[AsyncPostgresSaver]:
    """A checkpointer on its own autocommit connection. The schema is owned by Alembic, not by setup()."""
    url = conninfo or get_settings().checkpoint_url()
    async with await AsyncConnection.connect(
        url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as connection:
        yield AsyncPostgresSaver(connection)
