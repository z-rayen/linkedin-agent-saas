# ruff: noqa: E501  (generated SQL from the pinned library: line length is not ours to choose)
"""LangGraph checkpointer tables, exactly as langgraph-checkpoint-postgres==3.2.1 defines them.

The statements are generated from the pinned library (see tests/test_checkpoint_schema.py, which fails if they
ever differ). Each statement is recorded in checkpoint_migrations, so the library's own setup() sees the
schema as up to date and does nothing.

CREATE INDEX CONCURRENTLY cannot run inside a transaction, so those statements run in an autocommit block.

Revision ID: 0006_langgraph_checkpoint_tables
Revises: 0005_frozen_pdf_and_stuck_sends
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_langgraph_checkpoint_tables"
down_revision: str | None = "0005_frozen_pdf_and_stuck_sends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied from langgraph.checkpoint.postgres.PostgresSaver.MIGRATIONS (langgraph-checkpoint-postgres==3.2.1).
STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS checkpoint_migrations (\n    v INTEGER PRIMARY KEY\n);",
    "CREATE TABLE IF NOT EXISTS checkpoints (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    parent_checkpoint_id TEXT,\n    type TEXT,\n    checkpoint JSONB NOT NULL,\n    metadata JSONB NOT NULL DEFAULT '{}',\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)\n);",
    "CREATE TABLE IF NOT EXISTS checkpoint_blobs (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    channel TEXT NOT NULL,\n    version TEXT NOT NULL,\n    type TEXT NOT NULL,\n    blob BYTEA,\n    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)\n);",
    "CREATE TABLE IF NOT EXISTS checkpoint_writes (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    task_id TEXT NOT NULL,\n    idx INTEGER NOT NULL,\n    channel TEXT NOT NULL,\n    type TEXT,\n    blob BYTEA NOT NULL,\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n);",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    "SELECT 1;",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);\n    ",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);\n    ",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);\n    ",
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';",
)


def upgrade() -> None:
    for version, statement in enumerate(STATEMENTS):
        if "CONCURRENTLY" in statement:
            with op.get_context().autocommit_block():
                op.execute(statement)
        else:
            op.execute(statement)
        op.execute(f"INSERT INTO checkpoint_migrations (v) VALUES ({version})")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, checkpoints, checkpoint_migrations CASCADE")
