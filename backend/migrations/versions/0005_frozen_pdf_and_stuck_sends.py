"""Frozen PDF at approval, and the start time of a claimed send (to detect sends stuck in "sending").

Revision ID: 0005_frozen_pdf_and_stuck_sends
Revises: 0004_approval_integrity
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_frozen_pdf_and_stuck_sends"
down_revision: str | None = "0004_approval_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("cv_pdf", sa.LargeBinary(), nullable=True))
    op.add_column("applications", sa.Column("cv_pdf_sha256", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("sending_started_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "sending_started_at")
    op.drop_column("applications", "cv_pdf_sha256")
    op.drop_column("applications", "cv_pdf")
