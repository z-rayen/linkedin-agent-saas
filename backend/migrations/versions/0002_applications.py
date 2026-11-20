"""Applications: prepared emails and tailored CVs, with their lifecycle status.

Revision ID: 0002_applications
Revises: 0001_baseline
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_applications"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("watch_result_id", sa.Integer(), nullable=True),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("job_title", sa.String(), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("cv_pdf_path", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("smtp_message_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["watch_result_id"], ["watch_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_applications_id"), "applications", ["id"], unique=False)
    op.create_index(op.f("ix_applications_user_id"), "applications", ["user_id"], unique=False)
    op.create_index(op.f("ix_applications_recipient_email"), "applications", ["recipient_email"], unique=False)
    op.create_index(op.f("ix_applications_status"), "applications", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_applications_status"), table_name="applications")
    op.drop_index(op.f("ix_applications_recipient_email"), table_name="applications")
    op.drop_index(op.f("ix_applications_user_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_id"), table_name="applications")
    op.drop_table("applications")
