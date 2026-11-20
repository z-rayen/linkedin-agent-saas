"""Application lifecycle, content hash, timezone day start and sending policy. No database, no network."""

from datetime import datetime

import pytest

from linkedin_agent.domain.applications import (
    ApplicationStatus,
    InvalidTransitionError,
    SendContext,
    content_hash,
    ensure_transition,
    is_editable,
    local_day_start_utc,
    send_violations,
)

PREPARED = ApplicationStatus.PREPARED
APPROVED = ApplicationStatus.APPROVED
SENDING = ApplicationStatus.SENDING
SENT = ApplicationStatus.SENT
FAILED = ApplicationStatus.FAILED
REPLIED = ApplicationStatus.REPLIED


def allowed_context(**overrides: object) -> SendContext:
    base: dict[str, object] = {
        "sent_today": 0,
        "daily_cap": 10,
        "seconds_since_last_send": None,
        "min_delay_seconds": 300.0,
        "recipient_already_sent": False,
        "company_sent_recently": False,
    }
    base.update(overrides)
    return SendContext(**base)  # type: ignore[arg-type]


# --- lifecycle ---------------------------------------------------------------


def test_nothing_reaches_sent_without_approval() -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(PREPARED, SENT)


def test_sent_requires_the_sending_claim() -> None:
    # Going straight from approved to sent is refused: the claim is what makes a double send impossible.
    with pytest.raises(InvalidTransitionError):
        ensure_transition(APPROVED, SENT)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PREPARED, APPROVED),
        (APPROVED, SENDING),
        (APPROVED, PREPARED),
        (SENDING, SENT),
        (SENDING, FAILED),
        (FAILED, PREPARED),
        (FAILED, APPROVED),
        (SENT, REPLIED),
    ],
)
def test_documented_transitions_are_allowed(current: ApplicationStatus, target: ApplicationStatus) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize("target", [SENT, FAILED])
def test_a_sending_application_cannot_be_claimed_again(target: ApplicationStatus) -> None:
    if target is SENT:
        ensure_transition(SENDING, SENT)
    with pytest.raises(InvalidTransitionError):
        ensure_transition(SENDING, APPROVED)


def test_sent_is_only_left_by_a_reply() -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(SENT, APPROVED)
    ensure_transition(SENT, REPLIED)


def test_replied_is_terminal() -> None:
    for target in ApplicationStatus:
        with pytest.raises(InvalidTransitionError):
            ensure_transition(REPLIED, target)


def test_editable_statuses_exclude_sending_and_sent() -> None:
    assert is_editable(PREPARED) and is_editable(APPROVED) and is_editable(FAILED)
    assert not is_editable(SENDING)
    assert not is_editable(SENT)


# --- content hash -------------------------------------------------------------

LINES = [{"section": "experience", "text": "Built a service", "source_ids": ["exp-1"]}]


def test_hash_is_stable_for_identical_content() -> None:
    assert content_hash("S", "B", "a@acme.com", LINES) == content_hash("S", "B", "a@acme.com", LINES)


@pytest.mark.parametrize(
    "changed",
    [
        ("S2", "B", "a@acme.com", LINES),
        ("S", "B2", "a@acme.com", LINES),
        ("S", "B", "b@acme.com", LINES),
        ("S", "B", "a@acme.com", [{**LINES[0], "text": "Led a service"}]),
    ],
)
def test_hash_changes_when_any_sent_part_changes(changed: tuple) -> None:  # type: ignore[type-arg]
    assert content_hash(*changed) != content_hash("S", "B", "a@acme.com", LINES)


def test_hash_ignores_recipient_case_and_spacing() -> None:
    assert content_hash("S", "B", " A@Acme.com ", LINES) == content_hash("S", "B", "a@acme.com", LINES)


# --- timezone for the daily cap ----------------------------------------------------


def test_daily_cap_day_follows_the_configured_timezone() -> None:
    # 23:30 UTC on 20 Nov is already 00:30 on 21 Nov in Tunis (UTC+1, no daylight saving).
    # The Tunis day therefore started at 23:00 UTC on 20 Nov, not at midnight UTC.
    now_utc = datetime(2026, 11, 20, 23, 30)
    assert local_day_start_utc(now_utc, "Africa/Tunis") == datetime(2026, 11, 20, 23, 0)


def test_daily_cap_day_in_utc_when_configured_so() -> None:
    assert local_day_start_utc(datetime(2026, 11, 20, 23, 30), "UTC") == datetime(2026, 11, 20, 0, 0)


# --- sending policy ----------------------------------------------------------


def test_send_allowed_when_every_rule_passes() -> None:
    assert send_violations(allowed_context()) == []


def test_daily_cap_blocks_the_send() -> None:
    assert any("daily cap" in v for v in send_violations(allowed_context(sent_today=10, daily_cap=10)))


def test_minimum_delay_blocks_a_quick_second_send() -> None:
    violations = send_violations(allowed_context(seconds_since_last_send=60.0, min_delay_seconds=300.0))
    assert any("wait" in v for v in violations)


def test_duplicate_recipient_is_blocked() -> None:
    assert any("recipient" in v for v in send_violations(allowed_context(recipient_already_sent=True)))


def test_recent_company_is_blocked() -> None:
    assert any("company" in v for v in send_violations(allowed_context(company_sent_recently=True)))


def test_several_violations_are_all_reported() -> None:
    violations = send_violations(
        allowed_context(sent_today=10, recipient_already_sent=True, company_sent_recently=True)
    )
    assert len(violations) == 3
