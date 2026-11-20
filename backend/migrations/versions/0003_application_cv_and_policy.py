"""Applications carry their tailored CV and grounding result; profiles carry the master CV.

Revision ID: 0003_application_cv_and_policy
Revises: 0002_applications
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_application_cv_and_policy"
down_revision: str | None = "0002_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("profile_config", sa.Column("master_cv_json", sa.Text(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("company_key", sa.String(), nullable=False, server_default=""),
    )
    op.add_column("applications", sa.Column("tailored_cv_json", sa.Text(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("grounding_problems", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_index(op.f("ix_applications_company_key"), "applications", ["company_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_applications_company_key"), table_name="applications")
    op.drop_column("applications", "grounding_problems")
    op.drop_column("applications", "tailored_cv_json")
    op.drop_column("applications", "company_key")
    op.drop_column("profile_config", "master_cv_json")
