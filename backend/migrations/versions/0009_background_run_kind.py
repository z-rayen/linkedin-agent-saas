"""Background runs of two kinds: applications and research. Purely additive.

Adds to preparation_runs a `kind` (every existing row is an application, through the server default) and a
`result_json` (the answer of a research run). No row is rewritten, and no existing column changes.

The downgrade refuses to run while research runs exist: dropping `kind` would turn them into application runs, and
the application code would then try to resume them. Delete them first, deliberately.

Revision ID: 0009_background_run_kind
Revises: 0008_preparation_public_id
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_background_run_kind"
down_revision: str | None = "0008_preparation_public_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_KIND = "application"


def upgrade() -> None:
    op.add_column(
        "preparation_runs",
        sa.Column("kind", sa.String(), nullable=False, server_default=APPLICATION_KIND),
    )
    op.add_column("preparation_runs", sa.Column("result_json", sa.Text(), nullable=True))
    op.create_index(op.f("ix_preparation_runs_kind"), "preparation_runs", ["kind"], unique=False)


def downgrade() -> None:
    research_runs = (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM preparation_runs WHERE kind <> :kind"), {"kind": APPLICATION_KIND})
        .scalar_one()
    )
    if research_runs:
        raise RuntimeError(
            f"{research_runs} research run(s) exist: delete them before downgrading, or they would become "
            "application runs"
        )
    op.drop_index(op.f("ix_preparation_runs_kind"), table_name="preparation_runs")
    op.drop_column("preparation_runs", "result_json")
    op.drop_column("preparation_runs", "kind")
