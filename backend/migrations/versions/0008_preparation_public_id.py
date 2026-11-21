"""Unguessable public ids for preparation runs.

The status endpoint used to take the sequential integer id. Anyone who could guess a neighbouring number could probe
for runs. Clients now receive a random token instead. The integer stays internal (it is the checkpoint thread id).
Existing rows get a token as well.

Revision ID: 0008_preparation_public_id
Revises: 0007_preparation_runs
Create Date: 2026-11-20
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_preparation_public_id"
down_revision: str | None = "0007_preparation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLIC_ID_BYTES = 24  # 192 random bits, as in services/preparations.py


def upgrade() -> None:
    op.add_column("preparation_runs", sa.Column("public_id", sa.String(), nullable=True))
    connection = op.get_bind()
    run_ids = connection.execute(sa.text("SELECT id FROM preparation_runs")).scalars().all()
    for run_id in run_ids:
        connection.execute(
            sa.text("UPDATE preparation_runs SET public_id = :public_id WHERE id = :id"),
            {"public_id": secrets.token_urlsafe(PUBLIC_ID_BYTES), "id": run_id},
        )
    op.alter_column("preparation_runs", "public_id", existing_type=sa.String(), nullable=False)
    op.create_index(op.f("ix_preparation_runs_public_id"), "preparation_runs", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_preparation_runs_public_id"), table_name="preparation_runs")
    op.drop_column("preparation_runs", "public_id")
