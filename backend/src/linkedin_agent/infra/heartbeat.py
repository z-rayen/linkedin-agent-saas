"""Heartbeat for long-running jobs: a proof of life that the job is still making progress.

A job binds a callback once (see `heartbeat_bound`). Everything it awaits underneath (graph nodes, the token
budget, the provider's Retry-After waits) calls `beat()`, and the callback records the time. A waiting job is
therefore never silent for longer than the wait interval, and "stalled" means: no beat for longer than the
longest expected silence (see services/preparations.py).

The callback travels in a context variable, so it reaches every task started inside the bound block without
being passed through the call chain.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

Beat = Callable[[], Awaitable[None]]
Sleep = Callable[[float], Awaitable[object]]

_current: ContextVar[Beat | None] = ContextVar("heartbeat", default=None)


@contextmanager
def heartbeat_bound(beat: Beat) -> Iterator[None]:
    """Route every `beat()` inside this block to `beat`. Nests and unwinds correctly."""
    token = _current.set(beat)
    try:
        yield
    finally:
        _current.reset(token)


async def beat() -> None:
    """Record that the current job is alive. A no-op when no job is bound (unit tests, scripts)."""
    callback = _current.get()
    if callback is not None:
        await callback()


async def wait(seconds: float, interval: float, sleep: Sleep | None = None) -> None:
    """Sleep for `seconds`, beating at least every `interval` seconds. `sleep` is injectable for tests."""
    pause: Sleep = sleep or asyncio.sleep
    remaining = seconds
    while remaining > 0:
        step = min(remaining, interval)
        await pause(step)
        remaining -= step
        await beat()
