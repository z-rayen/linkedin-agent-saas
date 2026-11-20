"""Two real database connections race to claim the same approved application. Exactly one must win.

Runs against the dedicated test database (conftest refuses anything else). Each round re-approves the row,
so the race happens again and again rather than once.
"""

import threading
from datetime import datetime

from linkedin_agent import models
from linkedin_agent.database import SessionLocal
from linkedin_agent.domain.applications import ApplicationStatus
from linkedin_agent.services.applications import claim_for_sending

ROUNDS = 20
CONTENDERS = 2


def _make_approved_application() -> int:
    with SessionLocal() as db:
        user = models.User(email="race@example.com", hashed_password="x")
        db.add(user)
        db.flush()
        row = models.Application(
            user_id=user.id,
            job_title="ML Intern",
            recipient_email="hr@acme.com",
            company_key="acme.com",
            subject="Application",
            body="Hello.",
            status=ApplicationStatus.APPROVED.value,
        )
        db.add(row)
        db.commit()
        return row.id


def _reset_to_approved(application_id: int) -> None:
    with SessionLocal() as db:
        db.query(models.Application).filter(models.Application.id == application_id).update(
            {"status": ApplicationStatus.APPROVED.value, "smtp_message_id": None, "sending_started_at": None}
        )
        db.commit()


def _race(application_id: int, round_number: int) -> list[bool]:
    barrier = threading.Barrier(CONTENDERS)
    results: list[bool] = [False] * CONTENDERS

    def contend(slot: int) -> None:
        with SessionLocal() as db:  # one database connection per contender
            barrier.wait()  # both start the claim at the same moment
            results[slot] = claim_for_sending(
                db, application_id, f"<round{round_number}-{slot}@test>", datetime(2026, 11, 20, 12, 0)
            )

    threads = [threading.Thread(target=contend, args=(slot,)) for slot in range(CONTENDERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a claim did not finish: possible deadlock"
    return results


def test_two_simultaneous_claims_exactly_one_wins(clean_database: None) -> None:
    application_id = _make_approved_application()
    for round_number in range(ROUNDS):
        _reset_to_approved(application_id)
        results = _race(application_id, round_number)
        assert sorted(results) == [False, True], f"round {round_number}: {results}"


def test_the_winner_records_its_message_id_and_start_time(clean_database: None) -> None:
    application_id = _make_approved_application()
    results = _race(application_id, 0)
    winner_slot = results.index(True)
    with SessionLocal() as db:
        row = db.query(models.Application).filter(models.Application.id == application_id).one()
    assert row.status == ApplicationStatus.SENDING.value
    assert row.smtp_message_id == f"<round0-{winner_slot}@test>"
    assert row.sending_started_at == datetime(2026, 11, 20, 12, 0)


def test_a_claim_on_an_already_sending_row_fails(clean_database: None) -> None:
    application_id = _make_approved_application()
    with SessionLocal() as db:
        assert claim_for_sending(db, application_id, "<first@test>", datetime(2026, 11, 20, 12, 0)) is True
    with SessionLocal() as db:
        assert claim_for_sending(db, application_id, "<second@test>", datetime(2026, 11, 20, 12, 1)) is False
