"""Single source of truth for the current UTC time.

Returns a naive datetime in UTC, which is what the existing DateTime columns store.
Replaces the deprecated datetime.utcnow().
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current time in UTC, as a naive datetime (no tzinfo)."""
    return datetime.now(UTC).replace(tzinfo=None)
