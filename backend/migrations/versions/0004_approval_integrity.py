"""Approval integrity: the content hash taken at approval, checked again at send time.

Revision ID: 0004_approval_integrity
Revises: 0003_application_cv_and_policy
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_approval_integrity"
down_revision: str | None = "0003_application_cv_and_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("approved_content_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "approved_content_hash")
