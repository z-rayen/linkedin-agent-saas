"""Progress-stream tickets: a browser EventSource reads a run's progress without the login token in the URL.

Why a ticket: the browser EventSource cannot send an Authorization header. The JWT in the URL would end up in
logs, in browser history and in proxies. Instead, an authenticated endpoint issues a short-lived, single-use ticket,
bound to one user and one run. The stream endpoint accepts the ticket, and only the ticket.

Rules:
- a ticket expires after `stream_ticket_seconds`;
- a ticket works once: a second use is refused as "reused";
- a ticket works only for the user it was issued to ("wrong_user") and the run it names ("wrong_run");
- tickets live in this process's memory: one worker process, as for the token budget. Lost on restart, which is fine:
  the client asks again, and the run itself is never affected.

The events carry step names and counters only. Prompts, answers, queries, page text and titles never go into a stream;
`stream_event` keeps only the keys of an allowlist, so a future change cannot leak them by accident.
"""

import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..clock import utcnow

TICKET_BYTES = 32
ALLOWED_EVENT_KEYS = frozenset({"step", "status", "stage", "counts", "heartbeat", "stalled", "elapsed_ms"})
ALLOWED_COUNT_KEYS = frozenset({"candidates", "stored", "queries", "pages_read", "off_topic", "duplicates", "unscored"})


class TicketError(Exception):
    """Why a ticket was refused. The reason is one of a fixed set, never a secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Ticket:
    user_id: int
    run_id: str
    expires_at: datetime
    used: bool = False


class TicketStore:
    def __init__(self) -> None:
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, user_id: int, run_id: str, ttl_seconds: int, now: datetime | None = None) -> str:
        moment = now or utcnow()
        token = secrets.token_urlsafe(TICKET_BYTES)
        with self._lock:
            self._forget_expired(moment)
            self._tickets[token] = _Ticket(
                user_id=user_id, run_id=run_id, expires_at=moment + timedelta(seconds=ttl_seconds)
            )
        return token

    def consume(self, token: str, run_id: str, now: datetime | None = None) -> int:
        """Accept the ticket for this run, once, and return the user it was issued to. Raises TicketError otherwise.

        The stream has no login token, so the owner is the ticket's user; the caller checks it with `ensure_owner`."""
        moment = now or utcnow()
        with self._lock:
            ticket = self._tickets.get(token)
            if ticket is None:
                raise TicketError("unknown")
            if ticket.used:
                raise TicketError("reused")
            if moment >= ticket.expires_at:
                raise TicketError("expired")
            if ticket.run_id != run_id:
                raise TicketError("wrong_run")
            ticket.used = True
            return ticket.user_id

    def _forget_expired(self, moment: datetime) -> None:
        for token in [t for t, ticket in self._tickets.items() if moment >= ticket.expires_at]:
            del self._tickets[token]

    def clear(self) -> None:
        with self._lock:
            self._tickets.clear()


TICKETS = TicketStore()


def ensure_owner(ticket_user_id: int, run_owner_id: int) -> None:
    """The run's owner must be the user the ticket was issued to. A ticket never opens someone else's run."""
    if ticket_user_id != run_owner_id:
        raise TicketError("wrong_user")


def stream_event(name: str, data: dict[str, Any]) -> str:
    """One server-sent event. Only allowlisted keys are kept; the counts are restricted to counters."""
    clean: dict[str, Any] = {key: value for key, value in data.items() if key in ALLOWED_EVENT_KEYS}
    if isinstance(clean.get("counts"), dict):
        clean["counts"] = {
            k: int(v) for k, v in clean["counts"].items() if k in ALLOWED_COUNT_KEYS and isinstance(v, int)
        }
    payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n"
