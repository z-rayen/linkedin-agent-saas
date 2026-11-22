"""Exception hierarchy. Every error the API returns is one of these, mapped to an HTTP response by main.py."""

from fastapi import HTTPException, status


class AppError(Exception):
    """Base class. Carries a stable machine-readable code and an HTTP status."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details or {}  # extra fields of the HTTP error body, such as a full list


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "conflict"


class ValidationFailedError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_failed"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"


class NotConnectedError(AppError):
    """The user has not connected the third-party account this action needs (or the connection is unusable)."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "not_connected"


class ExternalServiceError(AppError):
    """A third-party API (LinkedIn, GitHub, Groq, Serper) failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


class LLMUnavailableError(ExternalServiceError):
    """The LLM provider failed after retries (rate limit, outage, bad key)."""

    code = "llm_unavailable"


class LLMOutputError(ExternalServiceError):
    """The LLM answered, but not in the structure the prompt asked for, even after retries."""

    code = "llm_invalid_output"

    def __init__(self, message: str = "The model returned an invalid structure; retry or try another model") -> None:
        super().__init__(message)


class LLMTruncatedError(LLMOutputError):
    """The answer hit the output token limit before it finished. A budget problem, not a problem with the answer."""

    code = "llm_truncated"

    def __init__(self, max_tokens: int) -> None:
        super().__init__(f"The model's answer was cut off at {max_tokens} tokens")


CV_STRUCTURE_MESSAGE = "The model's answer was cut off or malformed; retry, or raise the output limit"


class CVStructureError(LLMOutputError):
    """The model's tailored CV did not match the CV structure, even after one correction attempt. Shown to the user."""

    code = "cv_invalid_structure"

    def __init__(self) -> None:
        super().__init__(CV_STRUCTURE_MESSAGE)


class InvalidStateError(AppError):
    """The action is not allowed in the resource's current state (for example editing a sent application)."""

    status_code = status.HTTP_409_CONFLICT
    code = "invalid_state"


class CVNotGroundedError(AppError):
    """The tailored CV has claims that cannot be traced to the master CV. Approval is refused until they are fixed.

    `problems` is the complete list, each one a dict with kind, location, term and message. It is never truncated.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "cv_not_grounded"

    def __init__(self, message: str, problems: list[dict[str, str]] | None = None) -> None:
        super().__init__(message, {"problems": list(problems or [])})


class ContentChangedError(AppError):
    """The email or CV changed after approval. The approval no longer covers what would be sent."""

    status_code = status.HTTP_409_CONFLICT
    code = "content_changed"


class SendPolicyViolationError(AppError):
    """A sending rule blocks this send (daily cap, delay, duplicate recipient or company)."""

    status_code = status.HTTP_409_CONFLICT
    code = "send_policy_violation"


class CVCompilationError(AppError):
    """Tectonic could not produce a PDF from the generated LaTeX. The log is kept for diagnosis."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "cv_compilation_failed"


class AlreadyRunningError(AppError):
    """A preparation for the same application is already queued or running."""

    status_code = status.HTTP_409_CONFLICT
    code = "already_running"


class InvalidOAuthStateError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_oauth_state"


class PasswordTooShortError(AppError):
    """The password is shorter than the registration policy. The details carry the limit, for the screen to show."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "password_too_short"

    def __init__(self, min_length: int) -> None:
        super().__init__(f"The password must be at least {min_length} characters long.", {"min_length": min_length})


class PasswordTooLongError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "password_too_long"

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"The password must be at most {max_bytes} bytes long (UTF-8).", {"max_bytes": max_bytes})


class RateLimitedError(AppError):
    """Too many failed logins for this account or this address. Nothing is checked until the window has passed."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many failed attempts. Try again later.", {"retry_after_seconds": retry_after_seconds})


def http_error(error: AppError) -> HTTPException:
    """Convert to FastAPI's exception for routes that still raise HTTPException."""
    return HTTPException(status_code=error.status_code, detail=error.message)
