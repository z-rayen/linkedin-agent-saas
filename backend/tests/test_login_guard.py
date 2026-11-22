"""The failed-login limit, without HTTP: the clock is injected, so nothing waits."""

import pytest

from linkedin_agent.core.exceptions import RateLimitedError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.services.login_guard import LoginGuard


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _fail(guard: LoginGuard, account: str, address: str, times: int) -> None:
    for _ in range(times):
        guard.record_failure(account, address)


def test_an_account_is_blocked_after_the_limit_whatever_the_address() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, "a@example.com", "10.0.0.1", get_settings().login_max_failures)
    with pytest.raises(RateLimitedError):
        guard.check("a@example.com", "10.9.9.9")


def test_an_address_is_blocked_after_the_limit_whatever_the_account() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, "first@example.com", "10.0.0.1", get_settings().login_max_failures)
    with pytest.raises(RateLimitedError):
        guard.check("second@example.com", "10.0.0.1")


def test_one_failure_below_the_limit_is_still_allowed() -> None:
    guard = LoginGuard(FakeClock())
    _fail(guard, "a@example.com", "10.0.0.1", get_settings().login_max_failures - 1)
    guard.check("a@example.com", "10.0.0.1")


def test_a_success_clears_the_account_but_not_the_address() -> None:
    guard = LoginGuard(FakeClock())
    limit = get_settings().login_max_failures
    _fail(guard, "a@example.com", "1.1.1.1", limit - 1)
    guard.record_success("a@example.com")
    _fail(guard, "a@example.com", "2.2.2.2", limit - 1)
    guard.check("a@example.com", "3.3.3.3")  # the account is back to a count below the limit
    _fail(guard, "b@example.com", "1.1.1.1", 1)  # the address kept its failures: 1.1.1.1 is now at the limit
    with pytest.raises(RateLimitedError):
        guard.check("c@example.com", "1.1.1.1")


def test_the_block_ends_when_the_oldest_failure_leaves_the_window() -> None:
    clock = FakeClock()
    guard = LoginGuard(clock)
    window = get_settings().login_window_seconds
    _fail(guard, "a@example.com", "10.0.0.1", get_settings().login_max_failures)

    clock.now += window - 1
    with pytest.raises(RateLimitedError):
        guard.check("a@example.com", "10.0.0.1")

    clock.now += 1
    guard.check("a@example.com", "10.0.0.1")


def test_retry_after_says_how_long_to_wait() -> None:
    guard = LoginGuard(FakeClock())
    window = get_settings().login_window_seconds
    _fail(guard, "a@example.com", "10.0.0.1", get_settings().login_max_failures)
    with pytest.raises(RateLimitedError) as info:
        guard.check("a@example.com", "10.0.0.1")
    assert 0 < info.value.details["retry_after_seconds"] <= window
