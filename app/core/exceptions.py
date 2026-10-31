"""Domain exceptions mapped to safe HTTP error responses."""

from __future__ import annotations


class MedKidsError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(MedKidsError):
    status_code = 404
    code = "not_found"


class AuthenticationError(MedKidsError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(MedKidsError):
    status_code = 403
    code = "forbidden"


class WorkflowError(MedKidsError):
    """An action is not allowed in the current workflow state."""

    status_code = 409
    code = "invalid_workflow_transition"


class InputRejectedError(MedKidsError):
    status_code = 422
    code = "input_rejected"


# --- LLM errors -----------------------------------------------------------


class LLMError(MedKidsError):
    status_code = 502
    code = "llm_error"
    retryable: bool = False


class LLMRateLimitError(LLMError):
    status_code = 503
    code = "llm_rate_limited"
    retryable = True


class LLMTimeoutError(LLMError):
    status_code = 504
    code = "llm_timeout"
    retryable = True


class LLMServerError(LLMError):
    status_code = 502
    code = "llm_unavailable"
    retryable = True


class LLMAuthenticationError(LLMError):
    status_code = 502
    code = "llm_auth_failed"
    retryable = False


class LLMConfigurationError(LLMError):
    status_code = 500
    code = "llm_misconfigured"
    retryable = False


class LLMOutputValidationError(LLMError):
    """The model returned output that could not be parsed/validated."""

    status_code = 502
    code = "llm_invalid_output"
    retryable = False
