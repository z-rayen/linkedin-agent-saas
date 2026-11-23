"""Provenance of watch results: the dedupe key, whether the page was read, the verified flag, and the grounded company
and job title. Additive.

Existing rows are marked unverified (fetched false, unverified true): they were judged on the search snippet only, and
the UI shows them with that flag. Their canonical link stays null; it is computed when the rows are read.

The downgrade drops the columns and the index. Nothing else changes.

Revision ID: 0012_watch_result_provenance
Revises: 0011_profile_watch_preferences
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_watch_result_provenance"
down_revision: str | None = "0011_profile_watch_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("watch_results", sa.Column("canonical_link", sa.String(), nullable=True))
    op.create_index(op.f("ix_watch_results_canonical_link"), "watch_results", ["canonical_link"], unique=False)
    op.add_column(
        "watch_results",
        sa.Column("fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "watch_results",
        sa.Column("unverified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("watch_results", sa.Column("company", sa.String(), nullable=True))
    op.add_column("watch_results", sa.Column("job_title", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("watch_results", "job_title")
    op.drop_column("watch_results", "company")
    op.drop_column("watch_results", "unverified")
    op.drop_column("watch_results", "fetched")
    op.drop_index(op.f("ix_watch_results_canonical_link"), table_name="watch_results")
    op.drop_column("watch_results", "canonical_link")
