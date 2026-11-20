"""A shared token budget for LLM calls, per process.

Before each call, the caller reserves an estimate of the tokens it will use. The reservation counts against
the budget for one minute. When the provider answers, the reservation is corrected with the real usage, so
the budget tracks what was actually spent. A call that would exceed the budget waits instead of failing.

The budget is per process. Several API workers, or several machines, would each have their own budget and
could together exceed the provider limit. Run one worker process for this application.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

WINDOW_SECONDS = 60.0
CHARS_PER_TOKEN_ESTIMATE = 3  # conservative: French and code tokenise into more tokens than English prose


def estimate_prompt_tokens(prompt: str) -> int:
    """A deliberately high estimate. Over-reserving only delays a call; under-reserving breaks the limit."""
    return math.ceil(len(prompt) / CHARS_PER_TOKEN_ESTIMATE)


@dataclass
class Reservation:
    started_at: float
    tokens: int


class TokenBudget:
    def __init__(
        self,
        tokens_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        if tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        self._capacity = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._entries: deque[Reservation] = deque()

    @property
    def capacity(self) -> int:
        return self._capacity

    def _expire(self, now: float) -> None:
        while self._entries and now - self._entries[0].started_at >= WINDOW_SECONDS:
            self._entries.popleft()

    def used(self) -> int:
        """Tokens counted against the current window (for tests and diagnostics)."""
        self._expire(self._clock())
        return sum(entry.tokens for entry in self._entries)

    async def reserve(self, tokens: int) -> Reservation:
        """Wait until `tokens` fit in the window, then count them. Raises if they could never fit."""
        if tokens > self._capacity:
            raise ValueError(f"a single call needs {tokens} tokens, more than the budget of {self._capacity}")
        while True:
            # No await inside the check-and-reserve step: on one event loop it is atomic without a lock.
            now = self._clock()
            self._expire(now)
            used = sum(entry.tokens for entry in self._entries)
            if used + tokens <= self._capacity:
                reservation = Reservation(started_at=now, tokens=tokens)
                self._entries.append(reservation)
                return reservation
            oldest = self._entries[0].started_at
            await self._sleep(max(oldest + WINDOW_SECONDS - now, 0.05))

    def correct(self, reservation: Reservation, actual_tokens: int) -> None:
        """Replace the estimate with the tokens the provider reports. Keeps the original time in the window."""
        reservation.tokens = max(actual_tokens, 0)
