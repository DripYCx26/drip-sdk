"""
Drip SDK error classes.

This module defines custom exception types for the Drip SDK,
mirroring the TypeScript SDK's error handling patterns.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class DripError(Exception):
    """
    Base exception for all Drip SDK errors.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code from the API response.
        code: Optional error code for programmatic handling.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"(status: {self.status_code})")
        if self.code:
            parts.append(f"[{self.code}]")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"status_code={self.status_code}, "
            f"code={self.code!r})"
        )


class DripAPIError(DripError):
    """
    Error from the Drip API.

    Raised when the API returns an error response (4xx or 5xx status codes).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        code: str | None = None,
        response_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code, code)
        self.response_body = response_body

    def __str__(self) -> str:
        return super().__str__()

    def detailed_message(self) -> str:
        """Return full error details including response body (for logging only, never expose to clients)."""
        base = super().__str__()
        if self.response_body:
            return f"{base} | response: {self.response_body}"
        return base


class DripValidationError(DripError):
    """
    Validation error for request parameters.

    Raised when request parameters fail validation before being sent to the API.
    """

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any = None,
    ) -> None:
        super().__init__(message, status_code=400, code="VALIDATION_ERROR")
        self.field = field
        self.value = value


class DripNetworkError(DripError):
    """
    Network-related error.

    Raised when there are network connectivity issues or timeouts.
    """

    def __init__(
        self,
        message: str,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message, status_code=0, code="NETWORK_ERROR")
        self.original_error = original_error


class DripAuthenticationError(DripError):
    """
    Authentication error.

    Raised when the API key is invalid or missing.
    """

    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__(message, status_code=401, code="AUTHENTICATION_ERROR")


class DripRateLimitError(DripError):
    """
    Rate limit error.

    Raised when API rate limits are exceeded.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message, status_code=429, code="RATE_LIMIT_ERROR")
        self.retry_after = retry_after


class DripPaymentRequiredError(DripError):
    """
    Payment required error (x402).

    Raised when a customer has insufficient balance.
    Contains payment request information for the x402 flow.
    """

    def __init__(
        self,
        message: str = "Payment required",
        payment_request: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=402, code="PAYMENT_REQUIRED")
        self.payment_request = payment_request


# =============================================================================
# Middleware Error Types
# =============================================================================


class DripMiddlewareErrorCode(str, Enum):
    """Error codes for middleware errors."""

    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
    CUSTOMER_RESOLUTION_FAILED = "CUSTOMER_RESOLUTION_FAILED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    PAYMENT_VERIFICATION_FAILED = "PAYMENT_VERIFICATION_FAILED"
    CHARGE_FAILED = "CHARGE_FAILED"
    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DripMiddlewareError(DripError):
    """
    Middleware-specific error.

    Raised during request processing in the middleware layer.
    """

    def __init__(
        self,
        message: str,
        code: DripMiddlewareErrorCode,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code, code.value)
        self.middleware_code = code
        self.details = details or {}


# =============================================================================
# Error Factory Functions
# =============================================================================


def create_api_error_from_response(
    status_code: int,
    response_body: dict[str, Any] | None = None,
) -> DripError:
    """
    Create an appropriate error type from an API response.

    Args:
        status_code: HTTP status code from the response.
        response_body: Parsed JSON response body.

    Returns:
        An appropriate DripError subclass instance.
    """
    if response_body is None:
        response_body = {}

    message = response_body.get("message", response_body.get("error", "Unknown error"))
    code = response_body.get("code")

    if status_code == 401:
        return DripAuthenticationError(message)

    if status_code == 402:
        payment_request = (
            response_body.get("payment")
            or response_body.get("paymentRequest")
            or response_body.get("x402")
        )
        return DripPaymentRequiredError(message, payment_request)

    if status_code == 429:
        retry_after = response_body.get("retryAfter")
        return DripRateLimitError(message, retry_after)

    return DripAPIError(message, status_code, code, response_body)
