"""Lowercase and trim every account email, so that case and spaces cannot create a second account.

The rule is the one the registration and the login already apply (domain/accounts.py). Accounts whose addresses would
collide after normalisation are NOT changed: the migration stops, names their ids and the shared address, and a person
resolves them (merges or deletes an account) before running it again. Nothing is partly changed: the check runs before
the first update, inside the migration's transaction.

The downgrade cannot restore the original letter case, which is not stored anywhere, so it changes nothing.

Revision ID: 0010_normalise_user_emails
Revises: 0009_background_run_kind
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from linkedin_agent.domain.accounts import plan_email_normalisation

revision: str = "0010_normalise_user_emails"
down_revision: str | None = "0009_background_run_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class DuplicateAccountsError(RuntimeError):
    pass


def upgrade() -> None:
    connection = op.get_bind()
    rows = [(int(row[0]), str(row[1])) for row in connection.execute(sa.text("SELECT id, email FROM users")).all()]
    plan = plan_email_normalisation(rows)
    if not plan.is_safe:
        listing = "; ".join(
            f"{address}: account ids {', '.join(str(user_id) for user_id in ids)}"
            for address, ids in sorted(plan.duplicates.items())
        )
        raise DuplicateAccountsError(
            "users.email cannot be normalised: these accounts would share one address once lowercased and trimmed. "
            f"Merge or delete the extra accounts, then run the migration again. Duplicates: {listing}"
        )
    for user_id, address in plan.updates:
        connection.execute(sa.text("UPDATE users SET email = :email WHERE id = :id"), {"email": address, "id": user_id})


def downgrade() -> None:
    """Nothing to undo: the original letter case is not kept, and the normalised addresses remain valid."""
