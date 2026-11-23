"""Watch preferences on the profile: regions, languages and the offer type. Purely additive.

Every column is nullable and no existing row is rewritten. Without a value, the watch uses the settings defaults.
The downgrade drops the three columns; the preferences are then lost, which is why it is a deliberate step.

Revision ID: 0011_profile_watch_preferences
Revises: 0010_normalise_user_emails
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_profile_watch_preferences"
down_revision: str | None = "0010_normalise_user_emails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("profile_config", sa.Column("regions", sa.Text(), nullable=True))
    op.add_column("profile_config", sa.Column("languages", sa.Text(), nullable=True))
    op.add_column("profile_config", sa.Column("offer_type", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("profile_config", "offer_type")
    op.drop_column("profile_config", "languages")
    op.drop_column("profile_config", "regions")
