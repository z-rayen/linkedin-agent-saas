"""Exception hierarchy. Every error the API returns is one of these, mapped to an HTTP response by main.py."""

from fastapi import HTTPException, status


class AppError(Exception):
    """Base class. Carries a stable machine-readable code and an HTTP status."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


CV_STRUCTURE_MESSAGE = "The model returned an invalid CV structure; retry or try another model"


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
    """The tailored CV has claims that cannot be traced to the master CV. Approval is refused until they are fixed."""

    status_code = status.HTTP_409_CONFLICT
    code = "cv_not_grounded"


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


def http_error(error: AppError) -> HTTPException:
    """Convert to FastAPI's exception for routes that still raise HTTPException."""
    return HTTPException(status_code=error.status_code, detail=error.message)
