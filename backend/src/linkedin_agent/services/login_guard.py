"""Limits failed logins per account and per client address.

A failed attempt is counted under two keys: the normalised email (account) and the client address. When either key has
reached `login_max_failures` inside `login_window_seconds`, every login is refused with 429 until the oldest failure
leaves the window, even with the right password. A successful login clears the account's failures.

The state is in memory, per process. With several API workers each process counts on its own, so the effective limit is
a multiple of the configured one until a shared store is added (the same trade-off as the stream tickets, ADR 0003).
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from ..core.exceptions import RateLimitedError
from ..core.settings import Settings, get_settings

ACCOUNT_KEY = "account:"
ADDRESS_KEY = "address:"


class LoginGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def check(self, account: str, address: str) -> None:
        """Raises RateLimitedError when either the account or the address is blocked."""
        settings = get_settings()
        now = self._clock()
        with self._lock:
            waits = [
                self._seconds_until_allowed(ACCOUNT_KEY + account, now, settings),
                self._seconds_until_allowed(ADDRESS_KEY + address, now, settings),
            ]
        longest = max(waits)
        if longest > 0:
            raise RateLimitedError(math.ceil(longest))

    def record_failure(self, account: str, address: str) -> None:
        settings = get_settings()
        now = self._clock()
        with self._lock:
            self._sweep(now, settings)
            for key in (ACCOUNT_KEY + account, ADDRESS_KEY + address):
                self._failures.setdefault(key, deque()).append(now)

    def record_success(self, account: str) -> None:
        with self._lock:
            self._failures.pop(ACCOUNT_KEY + account, None)

    def reset(self) -> None:
        """Forget every failure. For tests."""
        with self._lock:
            self._failures.clear()

    def _seconds_until_allowed(self, key: str, now: float, settings: Settings) -> float:
        """0 when the key may try again; otherwise the time until enough failures have left the window."""
        window = settings.login_window_seconds
        failures = self._failures.get(key)
        if failures is None:
            return 0.0
        _expire(failures, now, window)
        if not failures:
            del self._failures[key]
            return 0.0
        blocking_index = len(failures) - settings.login_max_failures
        if blocking_index < 0:
            return 0.0
        return failures[blocking_index] + window - now

    def _sweep(self, now: float, settings: Settings) -> None:
        """Drops the keys whose failures have all left the window, so memory does not grow without bound."""
        for key in list(self._failures):
            failures = self._failures[key]
            _expire(failures, now, settings.login_window_seconds)
            if not failures:
                del self._failures[key]


def _expire(failures: deque[float], now: float, window: float) -> None:
    while failures and now - failures[0] >= window:
        failures.popleft()


LOGIN_GUARD = LoginGuard()
