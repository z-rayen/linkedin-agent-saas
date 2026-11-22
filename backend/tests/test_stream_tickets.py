"""Progress-stream tickets: expired, reused, wrong user, wrong run; and what a stream event may carry. No network."""

import json
from datetime import timedelta

import pytest

from linkedin_agent.clock import utcnow
from linkedin_agent.services.stream_tickets import TicketError, TicketStore, ensure_owner, stream_event

RUN = "run-public-id-for-a-test-000000000000000"
OTHER_RUN = "another-run-public-id-0000000000000000"


@pytest.fixture
def store() -> TicketStore:
    return TicketStore()


def test_a_fresh_ticket_opens_its_run_once_and_names_its_user(store: TicketStore) -> None:
    now = utcnow()
    token = store.issue(user_id=7, run_id=RUN, ttl_seconds=60, now=now)
    assert store.consume(token, RUN, now=now + timedelta(seconds=1)) == 7


def test_a_ticket_is_expired_after_its_lifetime(store: TicketStore) -> None:
    now = utcnow()
    token = store.issue(user_id=7, run_id=RUN, ttl_seconds=60, now=now)
    with pytest.raises(TicketError) as info:
        store.consume(token, RUN, now=now + timedelta(seconds=60))
    assert info.value.reason == "expired"


def test_a_ticket_cannot_be_used_twice(store: TicketStore) -> None:
    now = utcnow()
    token = store.issue(user_id=7, run_id=RUN, ttl_seconds=60, now=now)
    store.consume(token, RUN, now=now)
    with pytest.raises(TicketError) as info:
        store.consume(token, RUN, now=now)
    assert info.value.reason == "reused"


def test_a_ticket_for_one_run_does_not_open_another(store: TicketStore) -> None:
    now = utcnow()
    token = store.issue(user_id=7, run_id=RUN, ttl_seconds=60, now=now)
    with pytest.raises(TicketError) as info:
        store.consume(token, OTHER_RUN, now=now)
    assert info.value.reason == "wrong_run"


def test_a_ticket_of_one_user_does_not_open_another_users_run(store: TicketStore) -> None:
    now = utcnow()
    token = store.issue(user_id=7, run_id=RUN, ttl_seconds=60, now=now)
    issued_to = store.consume(token, RUN, now=now)
    with pytest.raises(TicketError) as info:
        ensure_owner(issued_to, run_owner_id=8)
    assert info.value.reason == "wrong_user"
    ensure_owner(issued_to, run_owner_id=7)  # the owner is fine


def test_an_unknown_token_is_refused(store: TicketStore) -> None:
    with pytest.raises(TicketError) as info:
        store.consume("not-a-ticket", RUN)
    assert info.value.reason == "unknown"


def test_tokens_are_long_and_unique(store: TicketStore) -> None:
    now = utcnow()
    tokens = {store.issue(user_id=1, run_id=RUN, ttl_seconds=60, now=now) for _ in range(2000)}
    assert len(tokens) == 2000
    assert all(len(token) >= 40 for token in tokens)


def test_expired_tickets_are_forgotten_when_new_ones_are_issued(store: TicketStore) -> None:
    now = utcnow()
    store.issue(user_id=1, run_id=RUN, ttl_seconds=5, now=now)
    store.issue(user_id=1, run_id=RUN, ttl_seconds=5, now=now + timedelta(seconds=10))
    assert len(store._tickets) == 1


# --- what an event carries -----------------------------------------------------------------------------


def test_an_event_carries_only_the_allowlisted_keys() -> None:
    payload = stream_event(
        "progress",
        {
            "step": "score",
            "status": "running",
            "prompt": "SECRET PROMPT TEXT",
            "answer": "SECRET ANSWER",
            "title": "SECRET TITLE",
            "counts": {"candidates": 3, "queries": 2, "snippet_text": 9},
        },
    )
    assert "SECRET" not in payload
    assert payload.startswith("event: progress\ndata: ")
    data = json.loads(payload.split("data: ", 1)[1].strip())
    assert data == {"step": "score", "status": "running", "counts": {"candidates": 3, "queries": 2}}


def test_an_event_is_utf8_and_keeps_no_content_of_a_result() -> None:
    payload = stream_event("progress", {"step": "read", "counts": {"pages_read": 2}})
    assert "\\u" not in payload  # accents are written as they are, never escaped as ASCII sequences
