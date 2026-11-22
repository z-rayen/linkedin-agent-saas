"""The failed-login delay, without HTTP: the clock is injected, so nothing waits."""

import math

import pytest

from linkedin_agent.core.exceptions import RateLimitedError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.services.login_guard import LoginGuard, delay_after


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _fail(guard: LoginGuard, account: str, address: str, times: int) -> None:
    for _ in range(times):
        guard.record_failure(account, address)


def test_the_first_failures_are_free() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, "a@example.com", "10.0.0.1", get_settings().login_free_failures - 1)
    guard.check("a@example.com", "10.0.0.1")


def test_after_the_free_failures_the_next_attempt_waits_the_base_delay() -> None:
    guard = LoginGuard(FakeClock())
    settings = get_settings()
    _fail(guard, "a@example.com", "10.0.0.1", settings.login_free_failures)
    with pytest.raises(RateLimitedError) as info:
        guard.check("a@example.com", "10.0.0.1")
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds)


def test_the_wait_doubles_with_each_further_failure() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, "a@example.com", "10.0.0.1", settings.login_free_failures)
    clock.now += settings.login_base_delay_seconds
    guard.check("a@example.com", "10.0.0.1")  # the first wait is over
    guard.record_failure("a@example.com", "10.0.0.1")

    with pytest.raises(RateLimitedError) as info:
        guard.check("a@example.com", "10.0.0.1")
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds * 2)


def test_the_wait_never_exceeds_the_cap() -> None:
    settings = get_settings()
    assert delay_after(settings.login_free_failures + 60, settings) == settings.login_max_delay_seconds


def test_a_quiet_period_forgets_the_failures() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, "a@example.com", "10.0.0.1", settings.login_free_failures + 5)
    clock.now += settings.login_forget_seconds
    guard.check("a@example.com", "10.0.0.1")


def test_a_success_clears_the_account_but_not_the_address() -> None:
    guard = LoginGuard(FakeClock())
    free = get_settings().login_free_failures
    _fail(guard, "a@example.com", "1.1.1.1", free - 1)
    guard.record_success("a@example.com")
    _fail(guard, "a@example.com", "2.2.2.2", free - 1)
    guard.check("a@example.com", "3.3.3.3")  # the account count is back below the free failures
    _fail(guard, "b@example.com", "1.1.1.1", 1)  # the address kept its failures: it now has to wait
    with pytest.raises(RateLimitedError):
        guard.check("c@example.com", "1.1.1.1")


def test_the_address_wait_applies_across_accounts() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, "first@example.com", "10.0.0.1", get_settings().login_free_failures)
    with pytest.raises(RateLimitedError):
        guard.check("second@example.com", "10.0.0.1")


def test_retry_after_is_the_time_still_to_wait() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    settings = get_settings()
    _fail(guard, "a@example.com", "10.0.0.1", settings.login_free_failures)
    clock.now += settings.login_base_delay_seconds / 2
    with pytest.raises(RateLimitedError) as info:
        guard.check("a@example.com", "10.0.0.1")
    assert info.value.details["retry_after_seconds"] == math.ceil(settings.login_base_delay_seconds / 2)
