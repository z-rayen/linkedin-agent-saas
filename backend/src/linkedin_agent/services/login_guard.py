"""Slows down failed logins, so that guessing is slow and nobody can be locked out by someone else.

Two keys are counted:

- the (account, client address) pair: the first `login_free_failures` failures of a pair are free; after that the wait
  before the pair's next attempt doubles with each failure (`login_base_delay_seconds`: 2 s, 4 s, 8 s ...), capped at
  `login_max_delay_seconds`. A success by the pair clears its count. Because the key includes the address, a third party
  who fails from another address never makes the owner wait: the owner's attempts from their own address are not
  affected.
- the client address alone, across every account: a higher ceiling (`login_address_free_failures`), with the same wait
  rule. It stops one machine from trying many accounts quickly, and a success does not clear it.

An attempt made before the longer of the two waits is over is refused with 429 and `retry_after_seconds`, even with the
right password. A quiet period of `login_forget_seconds` without failure forgets a count. See
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

PAIR_KEY = "pair:"
ADDRESS_KEY = "address:"
MAX_EXPONENT = 32
PAIR_SEPARATOR = "\n"  # cannot occur in an address or an email: the separator keeps the pair unambiguous


@dataclass
class _Failures:
    count: int
    last_at: float


def pair_key(account: str, address: str) -> str:
    return PAIR_KEY + account + PAIR_SEPARATOR + address


class LoginGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, _Failures] = {}

    def check(self, account: str, address: str) -> None:
        """Raises RateLimitedError when the pair or the address still has to wait."""
        settings = get_settings()
        now = self._clock()
        with self._lock:
            wait = max(
                self._seconds_to_wait(pair_key(account, address), settings.login_free_failures, now, settings),
                self._seconds_to_wait(ADDRESS_KEY + address, settings.login_address_free_failures, now, settings),
            )
        if wait > 0:
            raise RateLimitedError(math.ceil(wait))

    def record_failure(self, account: str, address: str) -> None:
        settings = get_settings()
        now = self._clock()
        with self._lock:
            self._forget_quiet_keys(now, settings)
            for key in (pair_key(account, address), ADDRESS_KEY + address):
                entry = self._failures.get(key)
                if entry is None:
                    self._failures[key] = _Failures(count=1, last_at=now)
                else:
                    entry.count += 1
                    entry.last_at = now

    def record_success(self, account: str, address: str) -> None:
        """Clears the pair's count only. The address ceiling is not cleared by a success: otherwise one success per
        account would reset it while the address keeps guessing."""
        with self._lock:
            self._failures.pop(pair_key(account, address), None)

    def reset(self) -> None:
        """Forget every failure. For tests."""
        with self._lock:
            self._failures.clear()

    def _seconds_to_wait(self, key: str, free_failures: int, now: float, settings: Settings) -> float:
        entry = self._failures.get(key)
        if entry is None:
            return 0.0
        if now - entry.last_at >= settings.login_forget_seconds:
            del self._failures[key]
            return 0.0
        return max(0.0, entry.last_at + delay_after(entry.count, free_failures, settings) - now)

    def _forget_quiet_keys(self, now: float, settings: Settings) -> None:
        quiet = [key for key, entry in self._failures.items() if now - entry.last_at >= settings.login_forget_seconds]
        for key in quiet:
            del self._failures[key]


def delay_after(count: int, free_failures: int, settings: Settings) -> float:
    """Seconds to wait after `count` consecutive failures. Zero while the failures are still free."""
    extra = count - free_failures
    if extra < 0:
        return 0.0
    exponent = min(extra, MAX_EXPONENT)  # the cap is reached long before: this only keeps the arithmetic finite
    return min(settings.login_base_delay_seconds * 2**exponent, settings.login_max_delay_seconds)


LOGIN_GUARD = LoginGuard()
