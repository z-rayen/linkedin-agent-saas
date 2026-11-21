"""The checkpointer schema: the Alembic revision must match the pinned library, and the library must agree.

Two guarantees:
1. The statements in migration 0006 are the library's own statements. If the library is upgraded without a new
   migration, the first test fails.
2. After the migrations ran, the library's setup() finds nothing to do: it does not change the schema.
"""

import importlib.util
from pathlib import Path

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from linkedin_agent.core.settings import get_settings

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0006_langgraph_checkpoint_tables.py"
CHECKPOINT_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
CONCURRENT_INDEXES = {"checkpoints_thread_id_idx", "checkpoint_blobs_thread_id_idx", "checkpoint_writes_thread_id_idx"}


def _migration_statements() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: tuple[str, ...] = module.STATEMENTS
    return statements


def test_the_migration_is_exactly_the_pinned_library_schema() -> None:
    library = tuple(PostgresSaver.MIGRATIONS)
    assert _migration_statements() == library, (
        "langgraph-checkpoint-postgres changed its schema: add a new Alembic revision with the new statements"
    )


def _connect() -> psycopg.Connection:
    # The library talks psycopg v3, so it gets the plain postgresql:// URL from the settings helper.
    return psycopg.connect(get_settings().checkpoint_url(), autocommit=True, row_factory=dict_row)


def _table_names(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()
    return {row["table_name"] for row in rows}


@pytest.mark.integration
def test_the_migrations_create_the_checkpoint_tables(migrated_database: None) -> None:
    with _connect() as conn:
        assert _table_names(conn) >= CHECKPOINT_TABLES
        indexes = {row["indexname"] for row in conn.execute("SELECT indexname FROM pg_indexes").fetchall()}
        assert indexes >= CONCURRENT_INDEXES


@pytest.mark.integration
def test_the_library_setup_does_nothing_after_the_migrations(migrated_database: None) -> None:
    with _connect() as conn:
        versions_before = [
            row["v"] for row in conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
        ]
        tables_before = _table_names(conn)

        PostgresSaver(conn).setup()  # what the library would do at startup; Alembic already did it

        versions_after = [row["v"] for row in conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()]
        assert versions_after == versions_before == list(range(len(PostgresSaver.MIGRATIONS)))
        assert _table_names(conn) == tables_before
