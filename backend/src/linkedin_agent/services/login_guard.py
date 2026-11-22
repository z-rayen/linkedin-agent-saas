"""Slows down failed logins per account and per client address, instead of locking them out.

The first `login_free_failures` failures are free. After that, the wait before the next attempt doubles with each
failure (`login_base_delay_seconds`: 2 s, 4 s, 8 s ...), capped at `login_max_delay_seconds`. An attempt made before the
wait is over is refused with 429 and `retry_after_seconds`, even with the right password. A success clears the
account's count; a quiet period of `login_forget_seconds` without failure forgets the count of either key. See
docs/adr/0004-login-delay-and-trusted-proxies.md for the reasons and the trade-offs.

The state is in memory, per process: with several API workers each process counts on its own.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core.exceptions import RateLimitedError
from ..core.settings import Settings, get_settings

ACCOUNT_KEY = "account:"
ADDRESS_KEY = "address:"
MAX_EXPONENT = 32


@dataclass
class _Failures:
    count: int
    last_at: float


class LoginGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, _Failures] = {}

    def check(self, account: str, address: str) -> None:
        """Raises RateLimitedError when either the account or the address has to wait."""
        settings = get_settings()
        now = self._clock()
        with self._lock:
            wait = max(
                self._seconds_to_wait(ACCOUNT_KEY + account, now, settings),
                self._seconds_to_wait(ADDRESS_KEY + address, now, settings),
            )
        if wait > 0:
            raise RateLimitedError(math.ceil(wait))

    def record_failure(self, account: str, address: str) -> None:
        settings = get_settings()
        now = self._clock()
        with self._lock:
            self._forget_quiet_keys(now, settings)
            for key in (ACCOUNT_KEY + account, ADDRESS_KEY + address):
                entry = self._failures.get(key)
                if entry is None:
                    self._failures[key] = _Failures(count=1, last_at=now)
                else:
                    entry.count += 1
                    entry.last_at = now

    def record_success(self, account: str) -> None:
        with self._lock:
            self._failures.pop(ACCOUNT_KEY + account, None)

    def reset(self) -> None:
        """Forget every failure. For tests."""
        with self._lock:
            self._failures.clear()

    def _seconds_to_wait(self, key: str, now: float, settings: Settings) -> float:
        entry = self._failures.get(key)
        if entry is None:
            return 0.0
        if now - entry.last_at >= settings.login_forget_seconds:
            del self._failures[key]
            return 0.0
        return max(0.0, entry.last_at + delay_after(entry.count, settings) - now)

    def _forget_quiet_keys(self, now: float, settings: Settings) -> None:
        quiet = [key for key, entry in self._failures.items() if now - entry.last_at >= settings.login_forget_seconds]
        for key in quiet:
            del self._failures[key]


def delay_after(count: int, settings: Settings) -> float:
    """Seconds to wait after `count` consecutive failures. Zero while the failures are still free."""
    extra = count - settings.login_free_failures
    if extra < 0:
        return 0.0
    exponent = min(extra, MAX_EXPONENT)  # the cap is reached long before: this only keeps the arithmetic finite
    return min(settings.login_base_delay_seconds * 2**exponent, settings.login_max_delay_seconds)


LOGIN_GUARD = LoginGuard()
