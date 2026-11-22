"""The failed-login delay, without HTTP: the clock is injected, so nothing waits.

Counts are per (account, address) pair, with a higher ceiling per address across accounts."""

import math

import pytest

from linkedin_agent.core.exceptions import RateLimitedError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.services.login_guard import LoginGuard, delay_after

OWNER = "owner@example.com"
HOME = "198.51.100.10"
ATTACKER = "203.0.113.66"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _fail(guard: LoginGuard, account: str, address: str, times: int) -> None:
    for _ in range(times):
        guard.record_failure(account, address)


def _free_pair() -> int:
    return get_settings().login_free_failures


def test_the_first_failures_of_a_pair_are_free() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, OWNER, HOME, _free_pair() - 1)
    guard.check(OWNER, HOME)


def test_after_the_free_failures_the_pair_waits_the_base_delay() -> None:
    guard = LoginGuard(FakeClock())
    settings = get_settings()
    _fail(guard, OWNER, HOME, _free_pair())
    with pytest.raises(RateLimitedError) as info:
        guard.check(OWNER, HOME)
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds)


def test_the_wait_doubles_with_each_further_failure() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, OWNER, HOME, _free_pair())
    clock.now += settings.login_base_delay_seconds
    guard.check(OWNER, HOME)  # the first wait is over
    guard.record_failure(OWNER, HOME)

    with pytest.raises(RateLimitedError) as info:
        guard.check(OWNER, HOME)
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds * 2)


def test_the_wait_never_exceeds_the_cap() -> None:
    settings = get_settings()
    assert delay_after(_free_pair() + 60, _free_pair(), settings) == settings.login_max_delay_seconds


def test_a_quiet_period_forgets_the_failures() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, OWNER, HOME, _free_pair() + 5)
    clock.now += settings.login_forget_seconds
    guard.check(OWNER, HOME)


def test_a_third_party_cannot_lock_the_owner_out() -> None:
    """Failures from the attacker's address count for that pair only: the owner, from home, is not slowed down."""
    guard = LoginGuard(FakeClock())
    _fail(guard, OWNER, ATTACKER, _free_pair() + 10)
    guard.check(OWNER, HOME)


def test_a_success_clears_the_pair_it_belongs_to() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, OWNER, HOME, _free_pair() - 1)
    guard.record_success(OWNER, HOME)
    _fail(guard, OWNER, HOME, _free_pair() - 1)
    guard.check(OWNER, HOME)


def test_the_address_ceiling_is_higher_than_the_pair_and_covers_many_accounts() -> None:
    guard = LoginGuard(FakeClock())
    ceiling = get_settings().login_address_free_failures
    assert ceiling > _free_pair()
    for index in range(ceiling - 1):
        guard.record_failure(f"victim{index}@example.com", ATTACKER)
    guard.check("someone@example.com", ATTACKER)  # one below the ceiling: still free
    guard.record_failure("another@example.com", ATTACKER)

    with pytest.raises(RateLimitedError):
        guard.check("someone@example.com", ATTACKER)  # the address waits, whatever the account


def test_a_success_does_not_clear_the_address_ceiling() -> None:
    guard = LoginGuard(FakeClock())
    ceiling = get_settings().login_address_free_failures
    for index in range(ceiling):
        guard.record_failure(f"account{index}@example.com", ATTACKER)
    guard.record_success("account0@example.com", ATTACKER)
    with pytest.raises(RateLimitedError):
        guard.check("fresh@example.com", ATTACKER)


def test_retry_after_is_the_time_still_to_wait() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, OWNER, HOME, _free_pair())
    clock.now += settings.login_base_delay_seconds / 2
    with pytest.raises(RateLimitedError) as info:
        guard.check(OWNER, HOME)
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds / 2)
