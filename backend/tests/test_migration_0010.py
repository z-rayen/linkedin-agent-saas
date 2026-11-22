"""Migration 0010 on a populated users table: addresses are normalised, and duplicates stop the migration with a list."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from linkedin_agent.database import engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "0009_background_run_kind"
HEAD = "0010_normalise_user_emails"


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture
def migrations_back_at_head() -> Iterator[None]:
    yield
    command.upgrade(_config(), "head")


def _insert_user(conn: object, email: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text("INSERT INTO users (email, hashed_password, created_at) VALUES (:email, 'x', now())"),
        {"email": email},
    )


def test_addresses_are_lowercased_and_trimmed(clean_database: None, migrations_back_at_head: None) -> None:
    command.downgrade(_config(), BEFORE)
    with engine.begin() as conn:
        _insert_user(conn, "  Alice@Example.COM ")
        _insert_user(conn, "bob@example.com")

    command.upgrade(_config(), HEAD)

    with engine.connect() as conn:
        emails = sorted(row[0] for row in conn.execute(text("SELECT email FROM users")).all())
    assert emails == ["alice@example.com", "bob@example.com"]


def test_duplicates_stop_the_migration_with_the_accounts_named_and_change_nothing(
    clean_database: None, migrations_back_at_head: None
) -> None:
    command.downgrade(_config(), BEFORE)
    with engine.begin() as conn:
        _insert_user(conn, "dup@example.com")
        _insert_user(conn, "DUP@example.com ")
        _insert_user(conn, "Solo@example.com")

    with pytest.raises(Exception, match="cannot be normalised") as info:
        command.upgrade(_config(), HEAD)
    assert "dup@example.com: account ids" in str(info.value)

    with engine.connect() as conn:
        emails = sorted(row[0] for row in conn.execute(text("SELECT email FROM users")).all())
    assert emails == ["DUP@example.com ", "Solo@example.com", "dup@example.com"]  # nothing was partly changed
    # The duplicates are removed here: the fixture moves the database back to the head revision afterwards.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users"))
