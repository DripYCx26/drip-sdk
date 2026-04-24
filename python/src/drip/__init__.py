"""
Drip SDK - Official Python SDK for usage-based billing with on-chain settlement.

Drip is metered billing infrastructure for high-frequency, sub-cent charges.
This SDK provides:

- **Core client**: Direct API access for managing customers, charges, and webhooks
- **Framework middleware**: One-liner integration for FastAPI and Flask
- **x402 payment protocol**: Automatic handling of payment flows

Quick Start:
    >>> from drip import Drip
    >>>
    >>> # Initialize the client
    >>> client = Drip(api_key="sk_test_...")
    >>>
    >>> # Create a customer
    >>> customer = client.create_customer(
    ...     onchain_address="0x123...",
    ...     external_customer_id="user_123"
    ... )
    >>>
    >>> # Create a charge
    >>> result = client.charge(
    ...     customer_id=customer.id,
    ...     meter="api_calls",
    ...     quantity=1
    ... )
    >>> print(f"Charged: {result.charge.amount_usdc} USDC")

Async Usage:
    >>> from drip import AsyncDrip
    >>>
    >>> async with AsyncDrip(api_key="sk_test_...") as client:
    ...     customer = await client.create_customer(
    ...         onchain_address="0x123..."
    ...     )

FastAPI Integration:
    >>> from fastapi import FastAPI
    >>> from drip.middleware.fastapi import DripMiddleware
    >>>
    >>> app = FastAPI()
    >>> app.add_middleware(DripMiddleware, meter="api_calls", quantity=1)

Flask Integration:
    >>> from flask import Flask
    >>> from drip.middleware.flask import drip_middleware
    >>>
    >>> app = Flask(__name__)
    >>>
    >>> @app.route("/api/endpoint")
    >>> @drip_middleware(meter="api_calls", quantity=1)
    >>> def endpoint():
    ...     return {"success": True}

Environment Variables:
    DRIP_API_KEY: Your Drip API key (alternative to passing api_key)
    DRIP_API_URL: Custom API base URL (defaults to https://api.drippay.dev/v1)
"""

from ._version import __version__
from .client import AsyncDrip, Drip

# ============================================================================
# Pre-initialized Singleton
# ============================================================================

_singleton: Drip | None = None


def _get_singleton() -> Drip:
    """Get the singleton Drip client instance."""
    global _singleton
    if _singleton is None:
        _singleton = Drip()  # Reads DRIP_API_KEY from environment
    return _singleton


class _DripProxy:
    """
    Lazy proxy for the Drip singleton.

    Allows `from drip import drip` to work without initializing
    until first use. Reads DRIP_API_KEY from environment variables.

    Example:
        >>> from drip import drip
        >>>
        >>> # One line to track usage (customer_id from create_customer())
        >>> drip.track_usage(customer_id=customer.id, meter="api_calls", quantity=1)
        >>>
        >>> # One line to charge
        >>> drip.charge(customer_id=customer.id, meter="api_calls", quantity=1)
    """

    def __getattr__(self, name: str):
        return getattr(_get_singleton(), name)


drip = _DripProxy()
from .errors import (
    DripAPIError,
    DripAuthenticationError,
    DripError,
    DripMiddlewareError,
    DripMiddlewareErrorCode,
    DripNetworkError,
    DripPaymentRequiredError,
    DripRateLimitError,
    DripValidationError,
)
from .models import (
    BalanceResult,
    Charge,
    ChargeInfo,
    ChargeParams,
    ChargeAsyncResult,
    ChargeResult,
    ChargeStatus,
    CheckoutParams,
    CheckoutResult,
    CostEstimateLineItem,
    CostEstimateResponse,
    CreateCustomerParams,
    CreateWebhookParams,
    CreateWebhookResponse,
    CreateWorkflowParams,
    Customer,
    CustomerStatus,
    DeleteWebhookResponse,
    DripConfig,
    EmitEventParams,
    EmitEventsBatchResult,
    EndRunParams,
    EndRunResult,
    EventResult,
    EventTrace,
    ExecutionEvent,
    HypotheticalUsageItem,
    IdempotencyKeyParams,
    ListChargesOptions,
    ListChargesResponse,
    ListEventsResponse,
    ListCustomersOptions,
    CreateSubscriptionParams,
    ListCustomersResponse,
    ListMetersResponse,
    ListSubscriptionsOptions,
    ListSubscriptionsResponse,
    ListWebhooksResponse,
    ListWorkflowsResponse,
    Meter,
    ProductSurface,
    RecordRunEvent,
    RecordRunParams,
    RecordRunResult,
    RetryOptions,
    RotateWebhookSecretResponse,
    RunDetails,
    RunDetailsLinks,
    RunDetailsTotals,
    RunResult,
    RunStatus,
    RunTimeline,
    StartRunParams,
    Subscription,
    SubscriptionInterval,
    SubscriptionStatus,
    TestWebhookResponse,
    TimelineEvent,
    TimelineRunInfo,
    TimelineTotals,
    TrackUsageBatchResult,
    TrackUsageResult,
    Webhook,
    WebhookEventType,
    WebhookStats,
    Workflow,
    WrapApiCallResult,
    X402PaymentProof,
    X402PaymentRequest,
    EntitlementCheckResult,
    CustomerSpendingCap,
    ListSpendingCapsResponse,
    SpendingCapType,
)
from .resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
    MetricsCollector,
    RateLimiter,
    RateLimiterConfig,
    RequestMetrics,
    ResilienceConfig,
    ResilienceManager,
    RetryConfig,
    RetryExhausted,
    calculate_backoff,
    with_retry,
    with_retry_async,
)
from .stream import StreamMeter, StreamMeterFlushResult, StreamMeterOptions
from .utils import (
    current_timestamp,
    current_timestamp_ms,
    format_usdc_amount,
    generate_idempotency_key,
    generate_nonce,
    generate_webhook_signature,
    is_valid_hex,
    normalize_address,
    parse_usdc_amount,
    verify_webhook_signature,
)

__all__ = [
    # Version
    "__version__",
    # Client classes
    "Drip",
    "AsyncDrip",
    # Pre-initialized singleton
    "drip",
    # StreamMeter
    "StreamMeter",
    "StreamMeterOptions",
    "StreamMeterFlushResult",
    # Errors
    "DripError",
    "DripAPIError",
    "DripAuthenticationError",
    "DripNetworkError",
    "DripRateLimitError",
    "DripPaymentRequiredError",
    "DripValidationError",
    "DripMiddlewareError",
    "DripMiddlewareErrorCode",
    # Enums
    "ChargeStatus",
    "CustomerStatus",
    "ProductSurface",
    "RunStatus",
    "WebhookEventType",
    # Configuration
    "DripConfig",
    # Customer models
    "CreateCustomerParams",
    "Customer",
    "ListCustomersOptions",
    "ListCustomersResponse",
    "BalanceResult",
    # Charge models
    "ChargeParams",
    "ChargeInfo",
    "ChargeResult",
    "Charge",
    "ListChargesOptions",
    "ListChargesResponse",
    # Track usage (no billing)
    "TrackUsageBatchResult",
    "TrackUsageResult",
    # Wrap API Call
    "WrapApiCallResult",
    "RetryOptions",
    # Cost Estimation
    "HypotheticalUsageItem",
    "CostEstimateLineItem",
    "CostEstimateResponse",
    # Checkout models
    "CheckoutParams",
    "CheckoutResult",
    # Webhook models
    "CreateWebhookParams",
    "CreateWebhookResponse",
    "Webhook",
    "WebhookStats",
    "ListWebhooksResponse",
    "DeleteWebhookResponse",
    "TestWebhookResponse",
    "RotateWebhookSecretResponse",
    # Subscription models
    "CreateSubscriptionParams",
    "Subscription",
    "SubscriptionInterval",
    "SubscriptionStatus",
    "ListSubscriptionsOptions",
    "ListSubscriptionsResponse",
    # Workflow & Run models
    "CreateWorkflowParams",
    "Workflow",
    "ListWorkflowsResponse",
    "StartRunParams",
    "RunResult",
    "RunDetails",
    "RunDetailsTotals",
    "RunDetailsLinks",
    "EndRunParams",
    "EndRunResult",
    "EmitEventParams",
    "EventResult",
    "EmitEventsBatchResult",
    "RunTimeline",
    "TimelineEvent",
    "TimelineRunInfo",
    "TimelineTotals",
    "RecordRunEvent",
    "RecordRunParams",
    "RecordRunResult",
    # Meter models
    "Meter",
    "ListMetersResponse",
    # x402 models
    "X402PaymentProof",
    "X402PaymentRequest",
    "IdempotencyKeyParams",
    # Entitlement models
    "EntitlementCheckResult",
    # Spending cap models
    "CustomerSpendingCap",
    "ListSpendingCapsResponse",
    "SpendingCapType",
    # Utility functions
    "generate_idempotency_key",
    "generate_webhook_signature",
    "verify_webhook_signature",
    "generate_nonce",
    "current_timestamp",
    "current_timestamp_ms",
    "is_valid_hex",
    "normalize_address",
    "format_usdc_amount",
    "parse_usdc_amount",
    # Resilience patterns
    "RateLimiter",
    "RateLimiterConfig",
    "RetryConfig",
    "RetryExhausted",
    "with_retry",
    "with_retry_async",
    "calculate_backoff",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpen",
    "CircuitState",
    "MetricsCollector",
    "RequestMetrics",
    "ResilienceConfig",
    "ResilienceManager",
]
