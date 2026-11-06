"""
Drip SDK data models using Pydantic.

This module contains all the data models and types used by the Drip SDK,
mirroring the TypeScript SDK's type definitions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class ChargeStatus(str, Enum):
    """Status of a charge."""

    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"  # Recorded off-chain, awaiting batch settlement
    PENDING = "PENDING"  # Settlement tx submitted but not confirmed
    CONFIRMED = "CONFIRMED"  # Transaction confirmed on-chain
    FAILED = "FAILED"  # Transaction failed
    REFUNDED = "REFUNDED"  # Charge was refunded
    VOIDED = "VOIDED"  # Charge voided before settlement
    ADJUSTED = "ADJUSTED"  # Charge adjusted (replaced by correction)


class CustomerStatus(str, Enum):
    """Status of a customer."""

    ACTIVE = "ACTIVE"
    LOW_BALANCE = "LOW_BALANCE"
    PAUSED = "PAUSED"


class RunStatus(str, Enum):
    """Status of an agent run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class ProductSurface(str, Enum):
    """Product surface type for workflows."""

    API = "API"
    RPC = "RPC"
    WEBHOOK = "WEBHOOK"
    AGENT = "AGENT"
    PIPELINE = "PIPELINE"
    CUSTOM = "CUSTOM"


class WebhookEventType(str, Enum):
    """Types of webhook events that can be subscribed to."""

    CUSTOMER_BALANCE_LOW = "customer.balance.low"
    USAGE_RECORDED = "usage.recorded"
    CHARGE_SUCCEEDED = "charge.succeeded"
    CHARGE_FAILED = "charge.failed"
    CUSTOMER_DEPOSIT_CONFIRMED = "customer.deposit.confirmed"
    CUSTOMER_WITHDRAW_CONFIRMED = "customer.withdraw.confirmed"
    CUSTOMER_USAGE_CAP_REACHED = "customer.usage_cap.reached"
    WEBHOOK_ENDPOINT_UNHEALTHY = "webhook.endpoint.unhealthy"
    CUSTOMER_SPENDING_WARNING = "customer.spending.warning"
    CUSTOMER_SPENDING_BLOCKED = "customer.spending.blocked"
    CUSTOMER_SPENDING_EXCEEDED = "customer.spending.exceeded"
    CUSTOMER_CREATED = "customer.created"
    API_KEY_CREATED = "api_key.created"
    PRICING_PLAN_UPDATED = "pricing_plan.updated"
    TRANSACTION_CREATED = "transaction.created"
    TRANSACTION_PENDING = "transaction.pending"
    TRANSACTION_CONFIRMED = "transaction.confirmed"
    TRANSACTION_FAILED = "transaction.failed"
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    SUBSCRIPTION_PAUSED = "subscription.paused"
    SUBSCRIPTION_RESUMED = "subscription.resumed"
    SUBSCRIPTION_TRIAL_ENDED = "subscription.trial_ended"
    SUBSCRIPTION_PAYMENT_FAILED = "subscription.payment_failed"


# =============================================================================
# Configuration
# =============================================================================


class DripConfig(BaseModel):
    """Configuration for the Drip client."""

    model_config = ConfigDict(frozen=True)

    api_key: str = Field(..., description="API key from Drip dashboard")
    base_url: str = Field(
        default="https://api.drippay.dev/v1", description="Base URL for the Drip API"
    )
    timeout: float = Field(default=30.0, description="Request timeout in seconds")


# =============================================================================
# Customer Models
# =============================================================================


class CreateCustomerParams(BaseModel):
    """Parameters for creating a new customer.

    At least one of ``onchain_address`` or ``external_customer_id`` is required.
    """

    onchain_address: str | None = Field(
        default=None, alias="onchainAddress", description="Customer's smart account address"
    )
    external_customer_id: str | None = Field(
        default=None, alias="externalCustomerId", description="Your internal customer ID"
    )
    is_internal: bool | None = Field(
        default=None, alias="isInternal", description="Mark as internal/non-billing customer"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Custom metadata")

    model_config = ConfigDict(populate_by_name=True)


class Customer(BaseModel):
    """Customer record."""

    id: str
    business_id: str | None = Field(default=None, alias="businessId")
    external_customer_id: str | None = Field(default=None, alias="externalCustomerId")
    onchain_address: str | None = Field(default=None, alias="onchainAddress")
    is_internal: bool | None = Field(default=None, alias="isInternal")
    status: CustomerStatus | None = None
    metadata: dict[str, Any] | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ListCustomersOptions(BaseModel):
    """Options for listing customers."""

    status: CustomerStatus | None = None
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListCustomersResponse(BaseModel):
    """Response from listing customers."""

    data: list[Customer]
    count: int


class BalanceResult(BaseModel):
    """Balance information for a customer."""

    customer_id: str = Field(alias="customerId")
    onchain_address: str = Field(alias="onchainAddress")
    balance_usdc: str = Field(alias="balanceUsdc", description="Balance in USDC (6 decimals)")
    pending_charges_usdc: str = Field(alias="pendingChargesUsdc")
    available_usdc: str = Field(alias="availableUsdc")
    last_synced_at: str | None = Field(alias="lastSyncedAt")

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Spending Cap Models
# =============================================================================


class SpendingCapType(str, Enum):
    """Type of spending cap."""

    DAILY_CHARGE_LIMIT = "DAILY_CHARGE_LIMIT"
    MONTHLY_CHARGE_LIMIT = "MONTHLY_CHARGE_LIMIT"
    SINGLE_CHARGE_LIMIT = "SINGLE_CHARGE_LIMIT"


class CustomerSpendingCap(BaseModel):
    """A per-customer spending cap with current usage tracking."""

    id: str
    cap_type: str = Field(alias="capType")
    limit_value: str = Field(alias="limitValue")
    current_usage: str | None = Field(default=None, alias="currentUsage")
    period_start: str | None = Field(default=None, alias="periodStart")
    is_active: bool | None = Field(default=None, alias="isActive")
    auto_block: bool = Field(alias="autoBlock")
    last_alert_level: str | None = Field(default=None, alias="lastAlertLevel")

    model_config = ConfigDict(populate_by_name=True)


class ListSpendingCapsResponse(BaseModel):
    """Response from listing spending caps."""

    caps: list[CustomerSpendingCap]


# =============================================================================
# Charge Models
# =============================================================================


class ChargeParams(BaseModel):
    """Parameters for creating a charge."""

    customer_id: str = Field(alias="customerId")
    meter: str = Field(..., description="Usage meter type (e.g., 'api_calls', 'tokens')")
    quantity: float
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ChargeInfo(BaseModel):
    """Charge information within a ChargeResult."""

    id: str
    amount_usdc: str = Field(alias="amountUsdc")
    amount_token: str | None = Field(default=None, alias="amountToken")
    tx_hash: str | None = Field(default=None, alias="txHash")
    status: ChargeStatus

    model_config = ConfigDict(populate_by_name=True)


class ChargeResult(BaseModel):
    """Result of a charge operation."""

    success: bool
    usage_event_id: str = Field(alias="usageEventId")
    is_duplicate: bool = Field(alias="isDuplicate", description="True if duplicate request matched by idempotencyKey")
    charge: ChargeInfo

    model_config = ConfigDict(populate_by_name=True)


class TrackUsageResult(BaseModel):
    """Result of tracking usage without billing."""

    success: bool
    usage_event_id: str = Field(alias="usageEventId")
    customer_id: str = Field(alias="customerId")
    usage_type: str = Field(alias="usageType")
    quantity: float
    is_internal: bool = Field(alias="isInternal")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class TrackUsageBatchResult(BaseModel):
    """Result of tracking usage in batch mode."""

    success: bool
    mode: Literal["batch"] = "batch"
    customer_id: str = Field(alias="customerId")
    usage_type: str = Field(alias="usageType")
    quantity: float
    idempotency_key: str = Field(alias="idempotencyKey")
    pending_events: int = Field(alias="pendingEvents")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class ChargeCustomer(BaseModel):
    """Customer info within a Charge."""

    id: str
    onchain_address: str | None = Field(default=None, alias="onchainAddress")
    external_customer_id: str | None = Field(default=None, alias="externalCustomerId")

    model_config = ConfigDict(populate_by_name=True)


class ChargeUsageEvent(BaseModel):
    """Usage event info within a Charge."""

    id: str
    type: str
    quantity: str
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class Charge(BaseModel):
    """Full charge record."""

    id: str
    usage_id: str = Field(alias="usageId")
    customer_id: str = Field(alias="customerId")
    customer: ChargeCustomer
    usage_event: ChargeUsageEvent = Field(alias="usageEvent")
    amount_usdc: str = Field(alias="amountUsdc")
    amount_token: str | None = Field(default=None, alias="amountToken")
    tx_hash: str | None = Field(default=None, alias="txHash")
    block_number: str | None = Field(default=None, alias="blockNumber")
    status: ChargeStatus
    failure_reason: str | None = Field(default=None, alias="failureReason")
    created_at: str = Field(alias="createdAt")
    confirmed_at: str | None = Field(default=None, alias="confirmedAt")

    model_config = ConfigDict(populate_by_name=True)


class ListChargesOptions(BaseModel):
    """Options for listing charges."""

    customer_id: str | None = Field(default=None, alias="customerId")
    status: ChargeStatus | None = None
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListChargesResponse(BaseModel):
    """Response from listing charges."""

    data: list[Charge]
    count: int




# =============================================================================
# Checkout Models
# =============================================================================


class CheckoutParams(BaseModel):
    """Parameters for creating a checkout session."""

    customer_id: str | None = Field(default=None, alias="customerId")
    external_customer_id: str | None = Field(default=None, alias="externalCustomerId")
    amount: int = Field(..., description="Amount in cents (5000 = $50.00)")
    return_url: str = Field(alias="returnUrl")
    cancel_url: str | None = Field(default=None, alias="cancelUrl")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class CheckoutResult(BaseModel):
    """Result of creating a checkout session."""

    id: str
    url: str = Field(..., description="Hosted checkout URL")
    expires_at: str = Field(alias="expiresAt")
    amount_usd: float = Field(alias="amountUsd")

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Webhook Models
# =============================================================================


class WebhookFilters(BaseModel):
    """Per-endpoint routing filters for webhooks.
    All specified criteria use AND logic — events must match ALL filters.
    """

    usage_types: list[str] | None = Field(default=None, alias="usageTypes")
    customer_ids: list[str] | None = Field(default=None, alias="customerIds")
    severities: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)


class CreateWebhookParams(BaseModel):
    """Parameters for creating a webhook."""

    url: str = Field(..., description="HTTPS endpoint")
    events: list[WebhookEventType]
    description: str | None = None
    filters: WebhookFilters | None = None


class UpdateWebhookParams(BaseModel):
    """Parameters for updating a webhook."""

    url: str | None = None
    events: list[WebhookEventType] | None = None
    description: str | None = None
    is_active: bool | None = Field(default=None, alias="isActive")
    filters: WebhookFilters | None = None

    model_config = ConfigDict(populate_by_name=True)


class WebhookStats(BaseModel):
    """Webhook delivery statistics."""

    total: int = Field(default=0, description="Total events sent")
    delivered: int = Field(default=0, description="Successfully delivered")
    failed: int = Field(default=0, description="Failed after retries")
    pending: int = Field(default=0, description="Currently retrying")

    model_config = ConfigDict(populate_by_name=True)


class Webhook(BaseModel):
    """Webhook record."""

    id: str
    url: str
    events: list[str]
    description: str | None = None
    filters: WebhookFilters | None = None
    is_active: bool = Field(alias="isActive")
    health_status: str = Field(default="HEALTHY", alias="healthStatus")
    consecutive_failures: int = Field(default=0, alias="consecutiveFailures")
    last_health_change: str | None = Field(default=None, alias="lastHealthChange")
    created_at: str = Field(alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    stats: WebhookStats | None = None

    model_config = ConfigDict(populate_by_name=True)


class CreateWebhookResponse(Webhook):
    """Response from creating a webhook (includes secret)."""

    secret: str = Field(..., description="Only returned once - store securely!")
    message: str


class ListWebhooksResponse(BaseModel):
    """Response from listing webhooks."""

    data: list[Webhook]
    count: int


class DeleteWebhookResponse(BaseModel):
    """Response from deleting a webhook."""

    success: bool = Field(default=True)
    message: str | None = Field(default=None)


class TestWebhookResponse(BaseModel):
    """Response from testing a webhook."""

    message: str
    delivery_id: str | None = Field(alias="deliveryId")
    status: str
    response_code: int | None = Field(default=None, alias="responseCode")

    model_config = ConfigDict(populate_by_name=True)


class RotateWebhookSecretResponse(BaseModel):
    """Response from rotating a webhook secret."""

    secret: str
    message: str


# =============================================================================
# Workflow & Run Models
# =============================================================================


class CreateWorkflowParams(BaseModel):
    """Parameters for creating a workflow."""

    name: str
    slug: str = Field(..., description="URL-safe identifier")
    product_surface: ProductSurface | None = Field(default=None, alias="productSurface")
    description: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class Workflow(BaseModel):
    """Workflow record."""

    id: str
    name: str
    slug: str
    product_surface: str = Field(alias="productSurface")
    chain: str | None = None
    description: str | None = None
    is_active: bool = Field(alias="isActive")
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class ListWorkflowsResponse(BaseModel):
    """Response from listing workflows."""

    data: list[Workflow]
    count: int


class StartRunParams(BaseModel):
    """Parameters for starting an agent run."""

    customer_id: str = Field(alias="customerId")
    workflow_id: str = Field(alias="workflowId")
    external_run_id: str | None = Field(default=None, alias="externalRunId")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    parent_run_id: str | None = Field(default=None, alias="parentRunId")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class RunResult(BaseModel):
    """Result of starting a run."""

    id: str
    customer_id: str = Field(alias="customerId")
    workflow_id: str = Field(alias="workflowId")
    workflow_name: str = Field(alias="workflowName")
    status: RunStatus
    correlation_id: str | None = Field(alias="correlationId")
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class RunDetailsTotals(BaseModel):
    """Aggregate totals for a run."""

    event_count: int = Field(alias="eventCount")
    total_quantity: str = Field(alias="totalQuantity")
    total_cost_units: str = Field(alias="totalCostUnits")

    model_config = ConfigDict(populate_by_name=True)


class RunDetailsLinks(BaseModel):
    """HATEOAS links for a run."""

    timeline: str


class RunDetails(BaseModel):
    """Detailed run information from GET /runs/:id."""

    id: str
    customer_id: str = Field(alias="customerId")
    customer_name: str | None = Field(default=None, alias="customerName")
    workflow_id: str = Field(alias="workflowId")
    workflow_name: str = Field(alias="workflowName")
    status: RunStatus
    started_at: str | None = Field(default=None, alias="startedAt")
    ended_at: str | None = Field(default=None, alias="endedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    error_message: str | None = Field(default=None, alias="errorMessage")
    error_code: str | None = Field(default=None, alias="errorCode")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    metadata: dict[str, Any] | None = None
    totals: RunDetailsTotals
    links: RunDetailsLinks = Field(alias="_links")

    model_config = ConfigDict(populate_by_name=True)


class EndRunParams(BaseModel):
    """Parameters for ending a run."""

    status: Literal["COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"]
    error_message: str | None = Field(default=None, alias="errorMessage")
    error_code: str | None = Field(default=None, alias="errorCode")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class EndRunResult(BaseModel):
    """Result of ending a run."""

    id: str
    status: RunStatus
    ended_at: str = Field(alias="endedAt")
    duration_ms: int | None = Field(alias="durationMs")
    event_count: int = Field(alias="eventCount")
    total_cost_units: str | None = Field(alias="totalCostUnits")

    model_config = ConfigDict(populate_by_name=True)


class EmitEventParams(BaseModel):
    """Parameters for emitting an event within a run."""

    run_id: str = Field(alias="runId")
    event_type: str = Field(alias="eventType")
    quantity: float | None = None
    units: str | None = None
    description: str | None = None
    cost_units: float | None = Field(default=None, alias="costUnits")
    cost_currency: str | None = Field(default=None, alias="costCurrency")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    parent_event_id: str | None = Field(default=None, alias="parentEventId")
    span_id: str | None = Field(default=None, alias="spanId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class EventResult(BaseModel):
    """Result of emitting an event."""

    id: str
    run_id: str = Field(alias="runId")
    event_type: str = Field(alias="eventType")
    quantity: float
    cost_units: float | None = Field(alias="costUnits")
    is_duplicate: bool = Field(alias="isDuplicate")
    timestamp: str

    model_config = ConfigDict(populate_by_name=True)


class BatchEventItem(BaseModel):
    """A single event result from batch emission."""

    id: str
    event_type: str = Field(alias="eventType")
    is_duplicate: bool = Field(default=False, alias="isDuplicate")
    skipped: bool | None = None
    reason: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class EmitEventsBatchResult(BaseModel):
    """Result of batch event emission."""

    success: bool
    created: int
    duplicates: int
    skipped: int | None = None
    events: list[BatchEventItem]


class TimelineEventCharge(BaseModel):
    """Charge info within a timeline event."""

    id: str
    amount_usdc: str = Field(alias="amountUsdc")
    status: str

    model_config = ConfigDict(populate_by_name=True)


class TimelineEvent(BaseModel):
    """Event in a run timeline."""

    id: str
    event_type: str = Field(alias="eventType")
    quantity: float
    units: str | None = None
    description: str | None = None
    cost_units: float | None = Field(alias="costUnits")
    timestamp: str
    correlation_id: str | None = Field(alias="correlationId")
    parent_event_id: str | None = Field(alias="parentEventId")
    charge: TimelineEventCharge | None = None

    model_config = ConfigDict(populate_by_name=True)


class TimelineRunInfo(BaseModel):
    """Run info within a timeline."""

    id: str
    customer_id: str = Field(alias="customerId")
    customer_name: str | None = Field(alias="customerName")
    workflow_id: str = Field(alias="workflowId")
    workflow_name: str = Field(alias="workflowName")
    status: RunStatus
    started_at: str | None = Field(alias="startedAt")
    ended_at: str | None = Field(alias="endedAt")
    duration_ms: int | None = Field(alias="durationMs")
    error_message: str | None = Field(alias="errorMessage")
    error_code: str | None = Field(alias="errorCode")
    correlation_id: str | None = Field(alias="correlationId")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class TimelineTotals(BaseModel):
    """Totals for a run timeline."""

    event_count: int = Field(alias="eventCount")
    total_quantity: str = Field(alias="totalQuantity")
    total_cost_units: str = Field(alias="totalCostUnits")
    total_charged_usdc: str = Field(alias="totalChargedUsdc")

    model_config = ConfigDict(populate_by_name=True)


class RunTimeline(BaseModel):
    """Full timeline for a run."""

    run: TimelineRunInfo
    timeline: list[TimelineEvent]
    totals: TimelineTotals
    summary: str


# =============================================================================
# Record Run (Simplified API)
# =============================================================================


class RecordRunEvent(BaseModel):
    """Event to record within a run (simplified API)."""

    event_type: str = Field(alias="eventType")
    quantity: float | None = None
    units: str | None = None
    description: str | None = None
    cost_units: float | None = Field(default=None, alias="costUnits")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class RecordRunParams(BaseModel):
    """Parameters for the simplified record_run API."""

    customer_id: str = Field(alias="customerId")
    workflow: str = Field(..., description="Workflow ID or slug (auto-creates if slug)")
    events: list[RecordRunEvent]
    status: Literal["COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"]
    error_message: str | None = Field(default=None, alias="errorMessage")
    error_code: str | None = Field(default=None, alias="errorCode")
    external_run_id: str | None = Field(default=None, alias="externalRunId")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class RecordRunResultRun(BaseModel):
    """Run info in record run result."""

    id: str
    workflow_id: str = Field(alias="workflowId")
    workflow_name: str = Field(alias="workflowName")
    status: RunStatus
    duration_ms: int | None = Field(alias="durationMs")

    model_config = ConfigDict(populate_by_name=True)


class RecordRunResultEvents(BaseModel):
    """Event stats in record run result."""

    created: int
    duplicates: int


class RecordRunResult(BaseModel):
    """Result of the simplified record_run API."""

    run: RecordRunResultRun
    events: RecordRunResultEvents
    total_cost_units: str | None = Field(alias="totalCostUnits")
    summary: str

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Meter Models
# =============================================================================


class Meter(BaseModel):
    """Usage meter configuration."""

    id: str
    name: str
    meter: str = Field(..., description="Use this in charge() calls")
    unit_price_usd: str = Field(alias="unitPriceUsd")
    is_active: bool = Field(alias="isActive")

    model_config = ConfigDict(populate_by_name=True)


class ListMetersResponse(BaseModel):
    """Response from listing meters."""

    data: list[Meter]
    count: int


# =============================================================================
# x402 Payment Protocol Models
# =============================================================================


class X402PaymentProof(BaseModel):
    """Payment proof submitted by client for x402 protocol."""

    signature: str
    session_key_id: str = Field(alias="sessionKeyId")
    smart_account: str = Field(alias="smartAccount")
    timestamp: int
    amount: str
    recipient: str
    usage_id: str = Field(alias="usageId")
    nonce: str

    model_config = ConfigDict(populate_by_name=True)


class X402PaymentRequest(BaseModel):
    """Payment request returned in 402 response."""

    amount: str
    recipient: str
    usage_id: str = Field(alias="usageId")
    description: str
    expires_at: int = Field(alias="expiresAt")
    nonce: str
    timestamp: int

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Idempotency Key Generation
# =============================================================================


class IdempotencyKeyParams(BaseModel):
    """Parameters for generating idempotency keys."""

    customer_id: str = Field(alias="customerId")
    run_id: str | None = Field(default=None, alias="runId")
    step_name: str = Field(alias="stepName")
    sequence: int | None = None

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Retry Configuration
# =============================================================================


class RetryOptions(BaseModel):
    """Retry options for API calls with exponential backoff."""

    max_attempts: int = Field(default=3, ge=1, description="Maximum number of retry attempts")
    base_delay_ms: int = Field(
        default=100, ge=0, alias="baseDelayMs", description="Base delay between retries in ms"
    )
    max_delay_ms: int = Field(
        default=5000, ge=0, alias="maxDelayMs", description="Maximum delay between retries in ms"
    )

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Wrap API Call Models
# =============================================================================


class WrapApiCallResult(BaseModel):
    """Result of wrapping an external API call with usage recording."""

    result: Any = Field(..., description="The result from the external API call")
    charge: ChargeResult = Field(..., description="The charge result from Drip")
    idempotency_key: str = Field(
        alias="idempotencyKey", description="The idempotency key used (useful for debugging)"
    )

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Cost Estimation Models
# =============================================================================


class HypotheticalUsageItem(BaseModel):
    """A usage item for hypothetical cost estimation."""

    usage_type: str = Field(alias="usageType", description="The usage type (e.g., 'api_call', 'token')")
    quantity: float = Field(..., description="The quantity of usage")
    unit_price_override: str | None = Field(
        default=None, alias="unitPriceOverride", description="Override unit price for this item"
    )

    model_config = ConfigDict(populate_by_name=True)


class CostEstimateLineItem(BaseModel):
    """A line item in the cost estimate."""

    usage_type: str = Field(alias="usageType", description="The usage type")
    quantity: str = Field(..., description="Total quantity")
    unit_price: str = Field(alias="unitPrice", description="Unit price used")
    estimated_cost_usdc: str = Field(alias="estimatedCostUsdc", description="Estimated cost in USDC")
    event_count: int | None = Field(
        default=None, alias="eventCount", description="Number of events (for usage-based estimates)"
    )
    has_pricing_plan: bool = Field(
        alias="hasPricingPlan", description="Whether a pricing plan was found for this usage type"
    )

    model_config = ConfigDict(populate_by_name=True)


class CostEstimateResponse(BaseModel):
    """Response from cost estimation."""

    business_id: str | None = Field(default=None, alias="businessId", description="Business ID")
    customer_id: str | None = Field(
        default=None, alias="customerId", description="Customer ID (if filtered)"
    )
    period_start: str | None = Field(
        default=None, alias="periodStart", description="Period start (for usage-based estimates)"
    )
    period_end: str | None = Field(
        default=None, alias="periodEnd", description="Period end (for usage-based estimates)"
    )
    line_items: list[CostEstimateLineItem] = Field(
        alias="lineItems", description="Breakdown by usage type"
    )
    subtotal_usdc: str = Field(alias="subtotalUsdc", description="Subtotal in USDC")
    estimated_total_usdc: str = Field(
        alias="estimatedTotalUsdc", description="Total estimated cost in USDC"
    )
    currency: Literal["USDC"] = Field(default="USDC", description="Currency (always USDC)")
    is_estimate: Literal[True] = Field(
        default=True, alias="isEstimate", description="Indicates this is an estimate"
    )
    generated_at: str = Field(alias="generatedAt", description="When the estimate was generated")

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Subscription Models
# =============================================================================


class SubscriptionStatus(str, Enum):
    """Status of a subscription."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PAST_DUE = "PAST_DUE"
    TRIALING = "TRIALING"


class SubscriptionInterval(str, Enum):
    """Billing interval for a subscription."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"


class Subscription(BaseModel):
    """Subscription record."""

    id: str
    business_id: str = Field(alias="businessId")
    customer_id: str = Field(alias="customerId")
    name: str
    description: str | None = None
    interval: SubscriptionInterval
    price_usdc: str = Field(alias="priceUsdc")
    status: SubscriptionStatus
    current_period_start: str = Field(alias="currentPeriodStart")
    current_period_end: str = Field(alias="currentPeriodEnd")
    cancelled_at: str | None = Field(default=None, alias="cancelledAt")
    cancel_at_period_end: bool = Field(alias="cancelAtPeriodEnd")
    paused_at: str | None = Field(default=None, alias="pausedAt")
    resumes_at: str | None = Field(default=None, alias="resumesAt")
    trial_start: str | None = Field(default=None, alias="trialStart")
    trial_end: str | None = Field(default=None, alias="trialEnd")
    included_usage: int | None = Field(default=None, alias="includedUsage")
    overage_unit_type: str | None = Field(default=None, alias="overageUnitType")
    metadata: dict[str, Any] | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CreateSubscriptionParams(BaseModel):
    """Parameters for creating a subscription."""

    customer_id: str = Field(alias="customerId", description="The customer to subscribe")
    name: str = Field(..., description="Human-readable subscription name")
    price_usdc: float = Field(alias="priceUsdc", description="Price per period in USDC")
    interval: SubscriptionInterval = Field(..., description="Billing interval")
    description: str | None = Field(default=None, description="Optional description")
    metadata: dict[str, Any] | None = Field(default=None, description="Custom metadata")
    trial_days: int | None = Field(default=None, alias="trialDays", description="Trial period in days")
    included_usage: int | None = Field(default=None, alias="includedUsage", description="Included usage units per period")
    overage_unit_type: str | None = Field(default=None, alias="overageUnitType", description="Usage type for overage metering")

    model_config = ConfigDict(populate_by_name=True)


class ListSubscriptionsOptions(BaseModel):
    """Options for listing subscriptions."""

    customer_id: str | None = Field(default=None, alias="customerId")
    status: SubscriptionStatus | None = None


class ListSubscriptionsResponse(BaseModel):
    """Response from listing subscriptions."""

    data: list[Subscription]
    count: int


# =============================================================================
# Entitlement Models
# =============================================================================


class ChargeAsyncResult(BaseModel):
    """Result of an async charge operation (returns 202 immediately)."""

    success: bool
    usage_event_id: str = Field(alias="usageEventId")
    is_duplicate: bool = Field(alias="isDuplicate")
    charge: ChargeInfo
    message: str

    model_config = ConfigDict(populate_by_name=True)


class ExecutionEvent(BaseModel):
    """An execution event record."""

    id: str
    customer_id: str = Field(alias="customerId")
    run_id: str | None = Field(default=None, alias="runId")
    event_type: str = Field(alias="eventType")
    outcome: str
    explanation: str | None = None
    created_at: str = Field(alias="createdAt")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ListEventsResponse(BaseModel):
    """Paginated list of events."""

    data: list[ExecutionEvent]
    pagination: dict[str, object] | None = None
    total: int | None = None
    limit: int | None = None
    offset: int | None = None

    model_config = ConfigDict(populate_by_name=True)


class EventTrace(BaseModel):
    """Causality trace for an event."""

    event: ExecutionEvent | dict[str, Any] | None = None
    event_id: str | None = Field(default=None, alias="eventId")
    ancestors: list[ExecutionEvent] = []
    children: list[ExecutionEvent] = []
    retry_chain: list[ExecutionEvent] | dict[str, Any] | None = Field(default=None, alias="retryChain")
    retries: dict[str, Any] | list[Any] | None = None
    anomalies: list[Any] | None = None
    has_failures: bool | None = Field(default=None, alias="hasFailures")

    model_config = ConfigDict(populate_by_name=True)


class EntitlementCheckResult(BaseModel):
    """Result of an entitlement check."""

    allowed: bool = Field(description="Whether the customer is allowed to use this feature")
    feature_key: str = Field(alias="featureKey", description="The feature that was checked")
    remaining: float = Field(description="Remaining quota in the current period (-1 if unlimited)")
    limit: float = Field(description="The limit for this period (-1 if unlimited)")
    unlimited: bool = Field(description="Whether the customer has unlimited access")
    period: Literal["DAILY", "MONTHLY"] = Field(description="The period this limit applies to")
    period_resets_at: str = Field(
        alias="periodResetsAt", description="When the current period resets (ISO timestamp)"
    )
    reason: str | None = Field(default=None, description="Reason for denial (only when allowed=False)")

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Entitlement Plan Types
# =============================================================================


class EntitlementPlan(BaseModel):
    """An entitlement plan."""

    id: str
    name: str
    slug: str
    description: str | None = None
    is_default: bool = Field(alias="isDefault", default=False)
    is_active: bool = Field(alias="isActive", default=True)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class EntitlementRule(BaseModel):
    """A feature rule within an entitlement plan."""

    id: str
    plan_id: str = Field(alias="planId")
    feature_key: str = Field(alias="featureKey")
    limit_type: Literal["COUNT", "AMOUNT"] = Field(alias="limitType")
    period: Literal["DAILY", "MONTHLY"]
    limit_value: float = Field(alias="limitValue")
    unlimited: bool = False

    model_config = ConfigDict(populate_by_name=True)


class CustomerEntitlement(BaseModel):
    """A customer's entitlement assignment including usage."""

    plan_id: str = Field(alias="planId")
    plan_name: str = Field(alias="planName")
    plan_slug: str = Field(alias="planSlug")
    rules: list[EntitlementRule] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# Contract Types
# =============================================================================


class ContractPriceOverride(BaseModel):
    """A price override within a contract."""

    unit_type: str = Field(alias="unitType")
    unit_price_usdc: str = Field(alias="unitPriceUsdc")

    model_config = ConfigDict(populate_by_name=True)


class Contract(BaseModel):
    """A per-customer pricing contract."""

    id: str
    customer_id: str = Field(alias="customerId")
    name: str
    status: Literal["ACTIVE", "EXPIRED", "CANCELLED"]
    start_date: str = Field(alias="startDate")
    end_date: str | None = Field(alias="endDate", default=None)
    minimum_usdc: str | None = Field(alias="minimumUsdc", default=None)
    maximum_usdc: str | None = Field(alias="maximumUsdc", default=None)
    discount_pct: float | None = Field(alias="discountPct", default=None)
    prepaid_amount_usdc: str | None = Field(alias="prepaidAmountUsdc", default=None)
    prepaid_balance_usdc: str | None = Field(alias="prepaidBalanceUsdc", default=None)
    prepaid_rollover: bool = Field(alias="prepaidRollover", default=False)
    included_units: dict[str, int] | None = Field(alias="includedUnits", default=None)
    metadata: dict[str, Any] | None = None
    overrides: list[ContractPriceOverride] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)
