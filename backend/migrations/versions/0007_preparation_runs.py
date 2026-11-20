"""Background preparation runs: status, progress, and one active run per application at a time.

Revision ID: 0007_preparation_runs
Revises: 0006_langgraph_checkpoint_tables
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_preparation_runs"
down_revision: str | None = "0006_langgraph_checkpoint_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "preparation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lock_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("step", sa.String(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_preparation_runs_id"), "preparation_runs", ["id"], unique=False)
    op.create_index(op.f("ix_preparation_runs_user_id"), "preparation_runs", ["user_id"], unique=False)
    op.create_index(op.f("ix_preparation_runs_status"), "preparation_runs", ["status"], unique=False)
    # The database enforces the lock: two active runs with the same key cannot coexist.
    op.create_index(
        "uq_preparation_runs_active_lock",
        "preparation_runs",
        ["lock_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_preparation_runs_active_lock", table_name="preparation_runs")
    op.drop_index(op.f("ix_preparation_runs_status"), table_name="preparation_runs")
    op.drop_index(op.f("ix_preparation_runs_user_id"), table_name="preparation_runs")
    op.drop_index(op.f("ix_preparation_runs_id"), table_name="preparation_runs")
    op.drop_table("preparation_runs")
