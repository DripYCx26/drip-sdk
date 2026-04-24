"""
Drip middleware types.

This module defines types for the middleware layer, including
configuration options, context, and x402 payment protocol types.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from ..client import AsyncDrip, Drip
from ..models import ChargeResult, X402PaymentRequest

# =============================================================================
# Generic Request Protocol
# =============================================================================


@runtime_checkable
class RequestProtocol(Protocol):
    """Protocol for HTTP request objects."""

    @property
    def method(self) -> str:
        """HTTP method."""
        ...

    @property
    def url(self) -> str:
        """Request URL."""
        ...

    @property
    def headers(self) -> dict[str, Any]:
        """Request headers."""
        ...


TRequest = TypeVar("TRequest", bound=RequestProtocol)


# =============================================================================
# Customer Resolution
# =============================================================================


CustomerResolver = Callable[[TRequest], str | Awaitable[str]]


# =============================================================================
# Middleware Configuration
# =============================================================================


@dataclass
class DripMiddlewareConfig:
    """
    Configuration for Drip middleware.

    Attributes:
        meter: Required usage meter type (e.g., "api_calls", "tokens").
        quantity: Static quantity or callable to compute dynamically.
        api_key: API key. Defaults to DRIP_API_KEY environment variable.
        base_url: API base URL. Defaults to production or DRIP_API_URL env var.
        customer_resolver: A callable that resolves the customer ID from
            the request using a verified authentication source (e.g.,
            session token, JWT). Must be explicitly provided.
        idempotency_key: Optional function to generate idempotency keys.
        on_error: Optional error handler callback.
        on_charge: Optional success callback after charging.
        skip_in_development: Skip charging in development mode.
        metadata: Static metadata dict or callable to generate dynamically.
    """

    meter: str
    quantity: float | Callable[[Any], float | Awaitable[float]]
    customer_resolver: CustomerResolver[Any]
    api_key: str | None = None
    base_url: str | None = None
    idempotency_key: Callable[[Any, str], str | Awaitable[str]] | None = None
    on_error: Callable[[Exception, Any], Any] | None = None
    on_charge: Callable[[ChargeResult, Any], Any] | None = None
    skip_in_development: bool = False
    metadata: dict[str, Any] | Callable[[Any], dict[str, Any]] | None = None


# =============================================================================
# Drip Context
# =============================================================================


@dataclass
class DripContext:
    """
    Context passed to route handlers after successful charging.

    Attributes:
        drip: The Drip client instance.
        customer_id: The resolved customer ID.
        charge: The charge result.
        is_duplicate: Whether this was a duplicate request matched by idempotencyKey.
    """

    drip: Drip | AsyncDrip
    customer_id: str
    charge: ChargeResult
    is_duplicate: bool


# =============================================================================
# x402 Payment Protocol Types
# =============================================================================


@dataclass
class PaymentRequestHeaders:
    """
    Headers for a 402 Payment Required response.

    These headers instruct the client on how to sign a payment proof.
    """

    x_payment_required: str = "true"
    x_payment_amount: str = ""
    x_payment_recipient: str = ""
    x_payment_currency: str = "USDC"
    x_payment_chain: str = "base-sepolia"
    x_payment_usage_id: str = ""
    x_payment_description: str = ""
    x_payment_expires: str = ""
    x_payment_nonce: str = ""
    x_payment_timestamp: str = ""

    def to_dict(self) -> dict[str, str]:
        """Convert to header dictionary."""
        return {
            "X-Payment-Required": self.x_payment_required,
            "X-Payment-Amount": self.x_payment_amount,
            "X-Payment-Recipient": self.x_payment_recipient,
            "X-Payment-Currency": self.x_payment_currency,
            "X-Payment-Chain": self.x_payment_chain,
            "X-Payment-Usage-Id": self.x_payment_usage_id,
            "X-Payment-Description": self.x_payment_description,
            "X-Payment-Expires": self.x_payment_expires,
            "X-Payment-Nonce": self.x_payment_nonce,
            "X-Payment-Timestamp": self.x_payment_timestamp,
        }


# =============================================================================
# Process Request Result
# =============================================================================


@dataclass
class ProcessRequestResult:
    """
    Result of processing a request through the middleware.

    Attributes:
        success: Whether the charge was successful.
        context: The DripContext if successful.
        payment_required: True if 402 response should be returned.
        payment_request: Payment request details for 402 response.
        payment_headers: Headers for 402 response.
        error: Error if processing failed.
    """

    success: bool = False
    context: DripContext | None = None
    payment_required: bool = False
    payment_request: X402PaymentRequest | None = None
    payment_headers: PaymentRequestHeaders | None = None
    error: Exception | None = None
