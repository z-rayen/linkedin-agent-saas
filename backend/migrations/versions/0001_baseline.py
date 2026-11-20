"""Baseline schema: the tables the prototype created with create_all.

Existing databases created by create_all must be marked as up to date instead of
being migrated again: run `alembic stamp 0001_baseline` once on them.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_oauth_tokens_id"), "oauth_tokens", ["id"], unique=False)

    op.create_table(
        "profile_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("min_match_score", sa.Integer(), nullable=True),
        sa.Column("job_feed_url", sa.String(), nullable=True),
        sa.Column("cert_feed_url", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_profile_config_id"), "profile_config", ["id"], unique=False)

    op.create_table(
        "content_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("post_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("post_url", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_content_queue_id"), "content_queue", ["id"], unique=False)

    op.create_table(
        "watch_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watch_results_id"), "watch_results", ["id"], unique=False)

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_messages_id"), "agent_messages", ["id"], unique=False)
    op.create_index(op.f("ix_agent_messages_user_id"), "agent_messages", ["user_id"], unique=False)

    op.create_table(
        "profile_knowledge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_profile_knowledge_id"), "profile_knowledge", ["id"], unique=False)
    op.create_index(op.f("ix_profile_knowledge_user_id"), "profile_knowledge", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_profile_knowledge_user_id"), table_name="profile_knowledge")
    op.drop_index(op.f("ix_profile_knowledge_id"), table_name="profile_knowledge")
    op.drop_table("profile_knowledge")

    op.drop_index(op.f("ix_agent_messages_user_id"), table_name="agent_messages")
    op.drop_index(op.f("ix_agent_messages_id"), table_name="agent_messages")
    op.drop_table("agent_messages")

    op.drop_index(op.f("ix_watch_results_id"), table_name="watch_results")
    op.drop_table("watch_results")

    op.drop_index(op.f("ix_content_queue_id"), table_name="content_queue")
    op.drop_table("content_queue")

    op.drop_index(op.f("ix_profile_config_id"), table_name="profile_config")
    op.drop_table("profile_config")

    op.drop_index(op.f("ix_oauth_tokens_id"), table_name="oauth_tokens")
    op.drop_table("oauth_tokens")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
