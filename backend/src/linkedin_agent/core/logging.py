"""Structured logging with request IDs and secret redaction.

Log lines are key=value pairs. Every request gets an ID (taken from the
X-Request-ID header when present, generated otherwise) that is attached to all
log records emitted while the request is handled and returned in the response.
"""

import logging
import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Matches bearer tokens and common secret-looking key=value pairs in log messages.
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|secret|token|password)\s*[=:]\s*)[^\s,;'\"]+", re.IGNORECASE),
]


def redact(text: str) -> str:
    """Replace secret-looking values in a log message with a placeholder."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "***", text)
    return text


class RequestContextFilter(logging.Filter):
    """Adds request_id to every record and redacts secrets from the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once. Safe to call more than once."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "ts=%(asctime)s level=%(levelname)s logger=%(name)s request_id=%(request_id)s msg=%(message)s"
        )
    )
    handler.addFilter(RequestContextFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID per request and echoes it in the response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
