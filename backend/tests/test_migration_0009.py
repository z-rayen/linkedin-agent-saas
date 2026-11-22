"""Migration 0009 on a populated table: additive, the rows survive, and the downgrade refuses while research runs exist.

The test database is moved back to 0008, filled with application runs, moved forward again, and finally returned to
the head revision, whatever happens in the middle.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from linkedin_agent.database import engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "0008_preparation_public_id"
HEAD = "0009_background_run_kind"


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture
def migrations_back_at_head() -> Iterator[None]:
    yield
    command.upgrade(_config(), "head")


def _columns() -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("preparation_runs")}


def _insert_user(conn: object) -> int:
    row = conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO users (email, hashed_password, created_at) "
            "VALUES ('migrate@example.com', 'x', now()) RETURNING id"
        )
    ).one()
    return int(row[0])


def _insert_run(conn: object, user_id: int, number: int, status: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO preparation_runs (public_id, user_id, lock_key, status, payload_json, heartbeat_at, created_at) "
            "VALUES (:public_id, :user_id, :lock_key, :status, '{}', now(), now())"
        ),
        {"public_id": f"public-{number}", "user_id": user_id, "lock_key": f"lock-{number}", "status": status},
    )


def test_the_upgrade_keeps_every_row_as_an_application_run(clean_database: None, migrations_back_at_head: None) -> None:
    command.downgrade(_config(), BEFORE)
    assert "kind" not in _columns()
    with engine.begin() as conn:
        user_id = _insert_user(conn)
        for number, status in enumerate(["queued", "awaiting_review", "closed", "failed"], start=1):
            _insert_run(conn, user_id, number, status)

    command.upgrade(_config(), HEAD)

    assert {"kind", "result_json"} <= _columns()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT status, kind, result_json FROM preparation_runs ORDER BY id")).all()
    assert [row[0] for row in rows] == ["queued", "awaiting_review", "closed", "failed"]  # nothing lost or changed
    assert {row[1] for row in rows} == {"application"}
    assert {row[2] for row in rows} == {None}


def test_the_downgrade_refuses_while_research_runs_exist_and_succeeds_after_they_are_removed(
    clean_database: None, migrations_back_at_head: None
) -> None:
    with engine.begin() as conn:
        user_id = _insert_user(conn)
        _insert_run(conn, user_id, 1, "queued")
        conn.execute(text("UPDATE preparation_runs SET kind = 'research'"))

    with pytest.raises(Exception, match="research run"):
        command.downgrade(_config(), BEFORE)
    assert {"kind", "result_json"} <= _columns()  # the refused downgrade changed nothing

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM preparation_runs WHERE kind = 'research'"))
    command.downgrade(_config(), BEFORE)
    assert "kind" not in _columns() and "result_json" not in _columns()

    command.upgrade(_config(), HEAD)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM preparation_runs")).scalar_one() == 0
