"""
Drip SDK Core - Essential API for pilots and new integrations.

This SDK focuses on two core concepts:
- **Usage tracking**: track_usage() for recording usage without billing
- **Execution logging**: record_run() and related methods for tracking runs/events

For billing, webhooks, cost estimation, and advanced features:
    from drip import Drip

Quick Start:
    >>> from drip.core import Drip
    >>>
    >>> client = Drip(api_key="sk_test_...")
    >>>
    >>> # Verify connection
    >>> health = client.ping()
    >>> print(f"API healthy: {health['ok']}")
    >>>
    >>> # Track usage (no billing)
    >>> result = client.track_usage(
    ...     customer_id="cust_123",
    ...     meter="api_calls",
    ...     quantity=1
    ... )
    >>>
    >>> # Record a complete request/run with events
    >>> result = client.record_run(
    ...     customer_id="cust_123",
    ...     workflow="rpc-request",
    ...     events=[
    ...         {"event_type": "request.start"},
    ...         {"event_type": "eth_call", "quantity": 1},
    ...         {"event_type": "request.end"},
    ...     ],
    ...     status="COMPLETED"
    ... )
    >>> print(result.summary)
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, overload

import httpx

from .errors import (
    DripAuthenticationError,
    DripError,
    DripNetworkError,
    create_api_error_from_response,
)

# ============================================================================
# Configuration Types
# ============================================================================


@dataclass
class DripConfig:
    """Configuration for the Drip SDK client."""

    api_key: str
    """Your Drip API key."""

    base_url: str = "https://api.drippay.dev/v1"
    """Base URL for the Drip API."""

    timeout: float = 30.0
    """Request timeout in seconds."""


# ============================================================================
# Customer Types
# ============================================================================


class CustomerStatus(str, Enum):
    """Customer status values."""

    ACTIVE = "ACTIVE"
    LOW_BALANCE = "LOW_BALANCE"
    PAUSED = "PAUSED"


@dataclass
class Customer:
    """A Drip customer record."""

    id: str
    """Unique customer ID in Drip."""

    external_customer_id: str | None
    """Your external customer ID (if provided)."""

    onchain_address: str | None
    """Customer's on-chain address (may be None for off-chain-only customers)."""

    metadata: dict[str, Any] | None
    """Custom metadata."""

    created_at: str
    """ISO timestamp of creation."""

    updated_at: str
    """ISO timestamp of last update."""

    business_id: str | None = None
    """Your business ID (optional)."""


@dataclass
class ListCustomersResponse:
    """Response from listing customers."""

    data: list[Customer]
    """Array of customers."""

    count: int
    """Total count returned."""


# ============================================================================
# Usage Tracking Types
# ============================================================================


@dataclass
class TrackUsageResult:
    """Result of tracking usage (no billing)."""

    success: bool
    """Whether the usage was recorded."""

    usage_event_id: str
    """The usage event ID."""

    customer_id: str
    """Customer ID."""

    usage_type: str
    """Usage type that was recorded."""

    quantity: float
    """Quantity recorded."""

    is_internal: bool
    """Whether this customer is internal-only."""

    message: str
    """Confirmation message."""


@dataclass
class TrackUsageBatchResult:
    """Result of tracking usage in batch mode."""

    success: bool
    """Whether the usage was recorded."""

    mode: Literal["batch"]
    """The write mode used for this request."""

    customer_id: str
    """Customer ID."""

    usage_type: str
    """Usage type that was recorded."""

    quantity: float
    """Quantity recorded."""

    idempotency_key: str | None
    """Idempotency key for queued batch writes."""

    pending_events: int | None
    """Number of pending queued events for batch writes."""

    message: str
    """Confirmation message."""


# ============================================================================
# Run & Event Types
# ============================================================================


class RunStatus(str, Enum):
    """Run status values."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


@dataclass
class RunResult:
    """Result of starting a run."""

    id: str
    customer_id: str
    workflow_id: str
    workflow_name: str
    status: RunStatus
    correlation_id: str | None
    created_at: str


@dataclass
class EndRunResult:
    """Result of ending a run."""

    id: str
    status: RunStatus
    ended_at: str | None
    duration_ms: int | None
    event_count: int
    total_cost_units: str | None


@dataclass
class EventResult:
    """Result of emitting an event."""

    id: str
    run_id: str
    event_type: str
    quantity: float
    cost_units: float | None
    is_duplicate: bool
    timestamp: str


@dataclass
class EmitEventsBatchResult:
    """Result of emitting multiple events."""

    success: bool
    created: int
    duplicates: int
    events: list[dict[str, Any]]


@dataclass
class RecordRunEvent:
    """A single event to record in a run."""

    event_type: str
    """Event type (e.g., 'request.start', 'llm.call')."""

    quantity: float | None = None
    """Quantity of units consumed."""

    units: str | None = None
    """Human-readable unit label."""

    description: str | None = None
    """Human-readable description."""

    cost_units: float | None = None
    """Cost in abstract units."""

    metadata: dict[str, Any] | None = None
    """Additional metadata."""


@dataclass
class RecordRunResultRun:
    """Run info from RecordRunResult."""

    id: str
    workflow_id: str
    workflow_name: str
    status: str
    duration_ms: int | None


@dataclass
class RecordRunResultEvents:
    """Events info from RecordRunResult."""

    created: int
    duplicates: int


@dataclass
class RecordRunResult:
    """Result of recording a run."""

    run: RecordRunResultRun
    """The created run."""

    events: RecordRunResultEvents
    """Summary of events created."""

    total_cost_units: str | None
    """Total cost computed."""

    summary: str
    """Human-readable summary."""


@dataclass
class TimelineEvent:
    """An event in a run timeline."""

    id: str
    event_type: str
    quantity: float
    units: str | None
    description: str | None
    cost_units: float | None
    timestamp: str
    correlation_id: str | None
    parent_event_id: str | None
    charge: dict[str, Any] | None


@dataclass
class RunTimelineRun:
    """Run info in timeline response."""

    id: str
    customer_id: str
    customer_name: str | None
    workflow_id: str
    workflow_name: str
    status: RunStatus
    started_at: str | None
    ended_at: str | None
    duration_ms: int | None
    error_message: str | None
    error_code: str | None
    correlation_id: str | None
    metadata: dict[str, Any] | None


@dataclass
class RunTimelineTotals:
    """Totals in timeline response."""

    event_count: int
    total_quantity: str
    total_cost_units: str
    total_charged_usdc: str


@dataclass
class RunTimeline:
    """Full run timeline response."""

    run: RunTimelineRun
    timeline: list[TimelineEvent]
    totals: RunTimelineTotals
    summary: str


# ============================================================================
# Internal Types (used by record_run)
# ============================================================================


@dataclass
class _Workflow:
    """Internal workflow type."""

    id: str
    name: str
    slug: str
    product_surface: str
    description: str | None
    is_active: bool
    created_at: str


def _normalize_events(events: list) -> list[dict[str, Any]]:
    """Normalize event dicts/dataclasses to camelCase for the API."""
    normalized = []
    for event in events:
        if isinstance(event, RecordRunEvent):
            evt: dict[str, Any] = {"eventType": event.event_type}
            if event.quantity is not None:
                evt["quantity"] = event.quantity
            if event.units:
                evt["units"] = event.units
            if event.description:
                evt["description"] = event.description
            if event.cost_units is not None:
                evt["costUnits"] = event.cost_units
            if event.metadata:
                evt["metadata"] = event.metadata
        else:
            evt = {
                "eventType": event.get("event_type", event.get("eventType", "")),
            }
            if "quantity" in event:
                evt["quantity"] = event["quantity"]
            for key in ("units", "description", "metadata"):
                if event.get(key) is not None:
                    evt[key] = event[key]
            cost = event.get("cost_units", event.get("costUnits"))
            if cost is not None:
                evt["costUnits"] = cost
        normalized.append(evt)
    return normalized


# ============================================================================
# Core SDK Class
# ============================================================================


class Drip:
    """
    Drip SDK Core - Essential API for pilots and new integrations.

    Two core concepts:
    - **Usage tracking**: `track_usage()` - record usage without billing
    - **Execution logging**: `record_run()` - track request/run lifecycle with events

    For billing (`charge()`), webhooks, and advanced features:
        from drip import Drip  # Full SDK

    Example:
        >>> from drip.core import Drip
        >>>
        >>> client = Drip(api_key="sk_test_...")
        >>>
        >>> # Verify connection
        >>> health = client.ping()
        >>> print(f"API healthy: {health['ok']}")
        >>>
        >>> # Track usage (no billing)
        >>> client.track_usage(
        ...     customer_id="cust_123",
        ...     meter="api_calls",
        ...     quantity=1
        ... )
        >>>
        >>> # Record a complete request/run
        >>> result = client.record_run(
        ...     customer_id="cust_123",
        ...     workflow="rpc-request",
        ...     events=[
        ...         {"event_type": "request.start"},
        ...         {"event_type": "eth_call", "quantity": 1},
        ...         {"event_type": "request.end"},
        ...     ],
        ...     status="COMPLETED"
        ... )
        >>> print(result.summary)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.drippay.dev/v1",
        timeout: float = 30.0,
    ) -> None:
        """
        Create a new Drip SDK client.

        Args:
            api_key: Your Drip API key. Falls back to DRIP_API_KEY env var.
            base_url: Base URL for the Drip API.
            timeout: Request timeout in seconds.

        Raises:
            ValueError: If no API key is provided.
        """
        resolved_api_key = api_key or os.environ.get("DRIP_API_KEY")
        if not resolved_api_key:
            msg = "Drip API key is required. Pass api_key or set DRIP_API_KEY."
            raise ValueError(msg)

        self._api_key = resolved_api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> Drip:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the Drip API."""
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.request(
                method=method,
                url=url,
                headers=headers,
                json=json,
            )
        except httpx.TimeoutException as e:
            raise DripNetworkError(f"Request timed out: {e}") from e
        except httpx.RequestError as e:
            raise DripNetworkError(f"Network error: {e}") from e

        if response.status_code == 204:
            return {"success": True}

        try:
            data = response.json()
        except Exception:
            data = {}

        if response.status_code == 401:
            raise DripAuthenticationError(
                data.get("message", "Authentication failed")
            )

        if not response.is_success:
            raise create_api_error_from_response(response.status_code, data)

        return data

    # ==========================================================================
    # Health Check
    # ==========================================================================

    def ping(self) -> dict[str, Any]:
        """
        Ping the Drip API to check connectivity and measure latency.

        Use this to verify:
        - API key is valid
        - Base URL is correct
        - Network connectivity works

        Returns:
            Dict with 'ok', 'status', 'latency_ms', 'timestamp'.

        Example:
            >>> health = client.ping()
            >>> if health['ok']:
            ...     print(f"API healthy, latency: {health['latency_ms']}ms")
        """
        # Construct health endpoint URL
        health_base_url = self._base_url
        if health_base_url.endswith("/v1"):
            health_base_url = health_base_url[:-3]
        health_base_url = health_base_url.rstrip("/")

        start = time.time()

        try:
            response = self._client.get(
                f"{health_base_url}/health",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            latency_ms = int((time.time() - start) * 1000)

            status = "unknown"
            timestamp = int(time.time() * 1000)

            try:
                data = response.json()
                if isinstance(data.get("status"), str):
                    status = data["status"]
                if isinstance(data.get("timestamp"), int):
                    timestamp = data["timestamp"]
            except Exception:
                status = "healthy" if response.is_success else f"error:{response.status_code}"

            if not response.is_success and status == "unknown":
                status = f"error:{response.status_code}"

            return {
                "ok": response.is_success and status == "healthy",
                "status": status,
                "latency_ms": latency_ms,
                "timestamp": timestamp,
            }
        except httpx.TimeoutException:
            raise DripNetworkError("Request timed out")
        except httpx.RequestError as e:
            raise DripNetworkError(f"Network error: {e}") from e

    # ==========================================================================
    # Customer Methods
    # ==========================================================================

    def create_customer(
        self,
        onchain_address: str | None = None,
        external_customer_id: str | None = None,
        is_internal: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Customer:
        """
        Create a new customer in your Drip account.

        At least one of ``onchain_address`` or ``external_customer_id`` is required.

        Args:
            onchain_address: The customer's on-chain wallet address (optional).
            external_customer_id: Your internal customer ID for reconciliation (optional).
            is_internal: Mark as internal/non-billing customer (optional, defaults to False).
            metadata: Additional metadata to store.

        Returns:
            The created Customer.

        Example:
            >>> customer = client.create_customer(
            ...     external_customer_id="user_123"
            ... )
        """
        payload: dict[str, Any] = {}
        if onchain_address:
            payload["onchainAddress"] = onchain_address
        if external_customer_id:
            payload["externalCustomerId"] = external_customer_id
        if is_internal is not None:
            payload["isInternal"] = is_internal
        if metadata:
            payload["metadata"] = metadata

        data = self._request("POST", "/customers", json=payload)

        return Customer(
            id=data["id"],
            business_id=data.get("businessId"),
            external_customer_id=data.get("externalCustomerId"),
            onchain_address=data.get("onchainAddress"),
            metadata=data.get("metadata"),
            created_at=data["createdAt"],
            updated_at=data["updatedAt"],
        )

    def get_customer(self, customer_id: str) -> Customer:
        """
        Retrieve a customer by their Drip ID.

        Args:
            customer_id: The Drip customer ID.

        Returns:
            The Customer details.
        """
        data = self._request("GET", f"/customers/{customer_id}")

        return Customer(
            id=data["id"],
            business_id=data.get("businessId"),
            external_customer_id=data.get("externalCustomerId"),
            onchain_address=data.get("onchainAddress"),
            metadata=data.get("metadata"),
            created_at=data["createdAt"],
            updated_at=data["updatedAt"],
        )

    def list_customers(
        self,
        limit: int | None = None,
        status: CustomerStatus | None = None,
    ) -> ListCustomersResponse:
        """
        List all customers for your business.

        Args:
            limit: Maximum number of customers to return (1-100).
            status: Filter by customer status.

        Returns:
            List of customers with count.
        """
        params: list[str] = []
        if limit:
            params.append(f"limit={limit}")
        if status:
            params.append(f"status={status.value}")

        path = "/customers"
        if params:
            path = f"{path}?{'&'.join(params)}"

        data = self._request("GET", path)

        customers = [
            Customer(
                id=c["id"],
                business_id=c.get("businessId"),
                external_customer_id=c.get("externalCustomerId"),
                onchain_address=c.get("onchainAddress"),
                metadata=c.get("metadata"),
                created_at=c["createdAt"],
                updated_at=c["updatedAt"],
            )
            for c in data.get("data", [])
        ]

        return ListCustomersResponse(data=customers, count=data.get("count", len(customers)))

    # ==========================================================================
    # Usage Tracking (No Billing)
    # ==========================================================================

    @overload
    def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch"] = "batch",
    ) -> TrackUsageBatchResult: ...

    @overload
    def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch", "sync"] = "sync",
    ) -> TrackUsageResult: ...

    def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch", "sync"] = "sync",
    ) -> TrackUsageResult | TrackUsageBatchResult:
        """
        Record usage for tracking WITHOUT billing.

        Use this for:
        - Pilot programs (track before billing)
        - Internal team usage
        - Pre-billing tracking before customer setup

        For actual billing, use `charge()` from the full SDK.

        Args:
            customer_id: The Drip customer ID.
            meter: The usage type (e.g., 'api_calls', 'tokens').
            quantity: The quantity of usage to record.
            idempotency_key: Unique key to prevent duplicate records.
            units: Human-readable unit label.
            description: Human-readable description.
            metadata: Additional metadata.
            mode: `sync` (default) for immediate persistence, or `batch`
                for high-throughput queued writes.

        Returns:
            The tracked usage event.

        Example:
            >>> result = client.track_usage(
            ...     customer_id="cust_123",
            ...     meter="api_calls",
            ...     quantity=100,
            ...     description="API calls during pilot"
            ... )
        """
        payload: dict[str, Any] = {
            "customerId": customer_id,
            "usageType": meter,
            "quantity": quantity,
        }
        payload["idempotencyKey"] = idempotency_key or str(uuid.uuid4())
        if units:
            payload["units"] = units
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata

        if mode not in ("batch", "sync"):
            raise DripError("mode must be 'batch' or 'sync'")
        path = "/usage/internal" if mode == "sync" else "/usage/internal/batch"
        data = self._request("POST", path, json=payload)

        if mode == "batch":
            return TrackUsageBatchResult(
                success=data.get("success", True),
                mode="batch",
                customer_id=data.get("customerId", customer_id),
                usage_type=data.get("usageType", meter),
                quantity=data.get("quantity", quantity),
                idempotency_key=data.get("idempotencyKey"),
                pending_events=data.get("pendingEvents"),
                message=data.get("message", "Usage tracked"),
            )

        return TrackUsageResult(
            success=data.get("success", True),
            usage_event_id=data.get("usageEventId", ""),
            customer_id=data.get("customerId", customer_id),
            usage_type=data.get("usageType", meter),
            quantity=data.get("quantity", quantity),
            is_internal=data.get("isInternal", True),
            message=data.get("message", "Usage tracked"),
        )

    # ==========================================================================
    # Private Workflow Methods (used by record_run)
    # ==========================================================================

    def _create_workflow(
        self,
        name: str,
        slug: str,
        product_surface: str = "CUSTOM",
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _Workflow:
        """Create a workflow (internal use)."""
        payload: dict[str, Any] = {
            "name": name,
            "slug": slug,
            "productSurface": product_surface,
        }
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata

        data = self._request("POST", "/workflows", json=payload)

        return _Workflow(
            id=data["id"],
            name=data["name"],
            slug=data["slug"],
            product_surface=data.get("productSurface", "CUSTOM"),
            description=data.get("description"),
            is_active=data.get("isActive", True),
            created_at=data["createdAt"],
        )

    def _list_workflows(self) -> list[_Workflow]:
        """List workflows (internal use)."""
        data = self._request("GET", "/workflows")

        return [
            _Workflow(
                id=w["id"],
                name=w["name"],
                slug=w["slug"],
                product_surface=w.get("productSurface", "CUSTOM"),
                description=w.get("description"),
                is_active=w.get("isActive", True),
                created_at=w["createdAt"],
            )
            for w in data.get("data", [])
        ]

    # ==========================================================================
    # Run & Event Methods (Execution Ledger)
    # ==========================================================================

    def start_run(
        self,
        customer_id: str,
        workflow_id: str,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunResult:
        """
        Start a new run for tracking execution.

        Args:
            customer_id: Customer ID this run belongs to.
            workflow_id: Workflow ID this run executes.
            external_run_id: Your external run ID for correlation.
            correlation_id: Correlation ID for distributed tracing.
            parent_run_id: Parent run ID for nested runs.
            metadata: Additional metadata.

        Returns:
            The started run.

        Example:
            >>> run = client.start_run(
            ...     customer_id="cust_123",
            ...     workflow_id="wf_xyz"
            ... )
            >>> # Emit events...
            >>> client.end_run(run.id, status="COMPLETED")
        """
        payload: dict[str, Any] = {
            "customerId": customer_id,
            "workflowId": workflow_id,
        }
        if external_run_id:
            payload["externalRunId"] = external_run_id
        if correlation_id:
            payload["correlationId"] = correlation_id
        if parent_run_id:
            payload["parentRunId"] = parent_run_id
        if metadata:
            payload["metadata"] = metadata

        data = self._request("POST", "/runs", json=payload)

        return RunResult(
            id=data["id"],
            customer_id=data["customerId"],
            workflow_id=data["workflowId"],
            workflow_name=data.get("workflowName", ""),
            status=RunStatus(data.get("status", "RUNNING")),
            correlation_id=data.get("correlationId"),
            created_at=data["createdAt"],
        )

    def end_run(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EndRunResult:
        """
        End a run with a final status.

        Args:
            run_id: The run ID to end.
            status: Final status ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT').
            error_message: Error message if failed.
            error_code: Error code for categorization.
            metadata: Additional metadata.

        Returns:
            Updated run info.
        """
        payload: dict[str, Any] = {"status": status}
        if error_message:
            payload["errorMessage"] = error_message
        if error_code:
            payload["errorCode"] = error_code
        if metadata:
            payload["metadata"] = metadata

        data = self._request("PATCH", f"/runs/{run_id}", json=payload)

        return EndRunResult(
            id=data["id"],
            status=RunStatus(data.get("status", status)),
            ended_at=data.get("endedAt"),
            duration_ms=data.get("durationMs"),
            event_count=data.get("eventCount", 0),
            total_cost_units=data.get("totalCostUnits"),
        )

    def get_run_timeline(self, run_id: str) -> RunTimeline:
        """
        Get a run's full timeline with events and computed totals.

        Args:
            run_id: The run ID.

        Returns:
            Full timeline with events and summary.

        Example:
            >>> timeline = client.get_run_timeline("run_123")
            >>> print(f"Status: {timeline.run.status}")
            >>> for event in timeline.timeline:
            ...     print(f"{event.event_type}: {event.quantity}")
        """
        data = self._request("GET", f"/runs/{run_id}/timeline")

        # Build run info from the timeline response
        run = RunTimelineRun(
            id=data.get("runId", run_id),
            customer_id=data.get("customerId", ""),
            customer_name=data.get("customerName"),
            workflow_id=data.get("workflowId", ""),
            workflow_name=data.get("workflowName", ""),
            status=RunStatus(data.get("status", "RUNNING")),
            started_at=data.get("startedAt"),
            ended_at=data.get("endedAt"),
            duration_ms=data.get("durationMs"),
            error_message=data.get("errorMessage"),
            error_code=data.get("errorCode"),
            correlation_id=data.get("correlationId"),
            metadata=data.get("metadata"),
        )

        events_data = data.get("events", [])
        timeline = [
            TimelineEvent(
                id=e["id"],
                event_type=e.get("eventType", e.get("actionName", "")),
                quantity=e.get("quantity", e.get("metadata", {}).get("quantity", 0) if isinstance(e.get("metadata"), dict) else 0),
                units=e.get("units", e.get("metadata", {}).get("units") if isinstance(e.get("metadata"), dict) else None),
                description=e.get("description", e.get("explanation")),
                cost_units=e.get("costUnits", e.get("costUsdc")),
                timestamp=e.get("timestamp", e.get("createdAt", "")),
                correlation_id=e.get("correlationId"),
                parent_event_id=e.get("parentEventId"),
                charge=e.get("charge"),
            )
            for e in events_data
        ]

        summary_data = data.get("summary", {})
        totals = RunTimelineTotals(
            event_count=summary_data.get("totalEvents", len(events_data)) if isinstance(summary_data, dict) else len(events_data),
            total_quantity=str(summary_data.get("totalQuantity", "0")) if isinstance(summary_data, dict) else "0",
            total_cost_units=str(summary_data.get("totalCostUnits", "0")) if isinstance(summary_data, dict) else "0",
            total_charged_usdc=str(summary_data.get("totalChargedUsdc", "0")) if isinstance(summary_data, dict) else "0",
        )

        summary_str = ""
        if isinstance(summary_data, dict):
            total_events = summary_data.get("totalEvents", len(events_data))
            summary_str = f"{total_events} events"
        elif isinstance(summary_data, str):
            summary_str = summary_data

        return RunTimeline(
            run=run,
            timeline=timeline,
            totals=totals,
            summary=summary_str,
        )

    def emit_event(
        self,
        run_id: str,
        event_type: str,
        quantity: float | None = None,
        units: str | None = None,
        description: str | None = None,
        cost_units: float | None = None,
        cost_currency: str | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
        span_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        """
        Emit an event to a run.

        Args:
            run_id: Run ID to attach this event to.
            event_type: Event type (e.g., 'llm.call', 'request.start').
            quantity: Quantity of units consumed.
            units: Human-readable unit label.
            description: Human-readable description.
            cost_units: Cost in abstract units.
            cost_currency: Currency for cost (e.g., 'USD').
            correlation_id: Correlation ID for tracing.
            parent_event_id: Parent event ID for trace tree.
            span_id: Span ID for distributed tracing.
            idempotency_key: Idempotency key.
            metadata: Additional metadata.

        Returns:
            The created event.

        Example:
            >>> client.emit_event(
            ...     run_id=run.id,
            ...     event_type="llm.call",
            ...     quantity=1500,
            ...     units="tokens"
            ... )
        """
        payload: dict[str, Any] = {
            "runId": run_id,
            "eventType": event_type,
        }
        if quantity is not None:
            payload["quantity"] = quantity
        if units:
            payload["units"] = units
        if description:
            payload["description"] = description
        if cost_units is not None:
            payload["costUnits"] = cost_units
        if cost_currency:
            payload["costCurrency"] = cost_currency
        if correlation_id:
            payload["correlationId"] = correlation_id
        if parent_event_id:
            payload["parentEventId"] = parent_event_id
        if span_id:
            payload["spanId"] = span_id
        payload["idempotencyKey"] = idempotency_key or str(uuid.uuid4())
        if metadata:
            payload["metadata"] = metadata

        data = self._request("POST", "/run-events", json=payload)

        return EventResult(
            id=data["id"],
            run_id=data["runId"],
            event_type=data["eventType"],
            quantity=data.get("quantity", 0),
            cost_units=data.get("costUnits"),
            is_duplicate=data.get("isDuplicate", False),
            timestamp=data["timestamp"],
        )

    def emit_events_batch(
        self,
        events: list[dict[str, Any]],
    ) -> EmitEventsBatchResult:
        """
        Emit multiple events in a single request.

        Args:
            events: Array of events to emit. Each event should have
                   'runId', 'eventType', and optionally 'quantity', 'units', etc.

        Returns:
            Summary of created events.

        Example:
            >>> result = client.emit_events_batch([
            ...     {"runId": run.id, "eventType": "step1", "quantity": 1},
            ...     {"runId": run.id, "eventType": "llm.call", "quantity": 1500},
            ... ])
        """
        normalized = [
            {**evt, "idempotencyKey": evt.get("idempotencyKey") or str(uuid.uuid4())}
            for evt in events
        ]
        data = self._request("POST", "/run-events/batch", json={"events": normalized})

        return EmitEventsBatchResult(
            success=data.get("success", True),
            created=data.get("created", 0),
            duplicates=data.get("duplicates", 0),
            events=data.get("events", []),
        )

    def record_run(
        self,
        customer_id: str,
        workflow: str,
        events: list[dict[str, Any] | RecordRunEvent],
        status: str,
        error_message: str | None = None,
        error_code: str | None = None,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RecordRunResult:
        """
        Record a complete request/run in a single call.

        Server-side endpoint handles workflow resolution, run creation,
        event emission, and run completion atomically.

        Args:
            customer_id: Customer ID this run belongs to.
            workflow: Workflow identifier (e.g., 'rpc-request', 'api-request').
                     Auto-creates if it doesn't exist.
            events: Events that occurred during the run.
            status: Final status ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT').
            error_message: Error message if status is FAILED.
            error_code: Error code if status is FAILED.
            external_run_id: Your external run ID for correlation.
            correlation_id: Correlation ID for distributed tracing.
            metadata: Additional metadata.

        Returns:
            The created run with event summary.

        Example:
            >>> result = client.record_run(
            ...     customer_id="cust_123",
            ...     workflow="rpc-request",
            ...     events=[
            ...         {"event_type": "request.start"},
            ...         {"event_type": "eth_call", "quantity": 1},
            ...         {"event_type": "request.end"},
            ...     ],
            ...     status="COMPLETED"
            ... )
            >>> print(result.summary)
        """
        normalized_events = _normalize_events(events)

        body: dict[str, Any] = {
            "customerId": customer_id,
            "workflow": workflow,
            "events": normalized_events,
            "status": status,
        }
        if error_message:
            body["errorMessage"] = error_message
        if error_code:
            body["errorCode"] = error_code
        if external_run_id:
            body["externalRunId"] = external_run_id
        if correlation_id:
            body["correlationId"] = correlation_id
        if metadata:
            body["metadata"] = metadata

        # Try single-call endpoint; fall back to 4-step if server returns 404
        try:
            data = self._request("POST", "/runs/record", json=body)
            run_data = data.get("run", {})
            events_data = data.get("events", {})
            return RecordRunResult(
                run=RecordRunResultRun(
                    id=run_data.get("id", ""),
                    workflow_id=run_data.get("workflowId", ""),
                    workflow_name=run_data.get("workflowName", ""),
                    status=run_data.get("status", status),
                    duration_ms=run_data.get("durationMs"),
                ),
                events=RecordRunResultEvents(
                    created=events_data.get("created", 0),
                    duplicates=events_data.get("duplicates", 0),
                ),
                total_cost_units=data.get("totalCostUnits"),
                summary=data.get("summary", ""),
            )
        except DripError as e:
            if e.status_code != 404:
                raise

        return self._record_run_fallback(
            customer_id, workflow, normalized_events, status,
            error_message, error_code, external_run_id, correlation_id, metadata,
        )

    def _record_run_fallback(
        self,
        customer_id: str,
        workflow: str,
        events: list[dict[str, Any]],
        status: str,
        error_message: str | None,
        error_code: str | None,
        external_run_id: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> RecordRunResult:
        """4-step orchestration fallback for servers without POST /runs/record."""
        start = time.monotonic()

        # Step 1: Resolve workflow
        workflows = self._list_workflows()
        match = next(
            (w for w in workflows if w.slug == workflow or w.id == workflow),
            None,
        )
        if match:
            workflow_id = match.id
            workflow_name = match.name
        else:
            pretty = workflow.replace("_", " ").replace("-", " ").title()
            created = self._create_workflow(name=pretty, slug=workflow)
            workflow_id = created.id
            workflow_name = created.name

        # Step 2: Start run
        run = self.start_run(
            customer_id=customer_id,
            workflow_id=workflow_id,
            external_run_id=external_run_id,
            correlation_id=correlation_id,
            metadata=metadata,
        )

        # Step 3: Emit events
        events_created = 0
        events_duplicates = 0
        if events:
            batch: list[dict[str, Any]] = []
            for i, evt in enumerate(events):
                entry: dict[str, Any] = {
                    "runId": run.id,
                    "eventType": evt["eventType"],
                    "quantity": evt.get("quantity", 1),
                }
                for key in ("units", "description", "costUnits", "metadata"):
                    if key in evt:
                        entry[key] = evt[key]
                if external_run_id:
                    entry["idempotencyKey"] = f"{external_run_id}:{evt['eventType']}:{i}"
                batch.append(entry)
            result = self.emit_events_batch(batch)
            events_created = result.created
            events_duplicates = result.duplicates

        # Step 4: End run
        end_result = self.end_run(
            run_id=run.id,
            status=status,
            error_message=error_message,
            error_code=error_code,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        dur = end_result.duration_ms if end_result.duration_ms is not None else elapsed_ms
        icon = "\u2713" if status == "COMPLETED" else "\u2717" if status == "FAILED" else "\u25CB"

        return RecordRunResult(
            run=RecordRunResultRun(
                id=run.id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                status=end_result.status.value if hasattr(end_result.status, 'value') else status,
                duration_ms=dur,
            ),
            events=RecordRunResultEvents(created=events_created, duplicates=events_duplicates),
            total_cost_units=end_result.total_cost_units,
            summary=f"{icon} {workflow_name}: {events_created} events recorded ({dur}ms)",
        )


# ============================================================================
# Async Core SDK Class
# ============================================================================


class AsyncDrip:
    """
    Async version of Drip SDK Core.

    Same API as Drip but with async/await support.

    Example:
        >>> from drip.core import AsyncDrip
        >>>
        >>> async with AsyncDrip(api_key="sk_test_...") as client:
        ...     health = await client.ping()
        ...     print(f"API healthy: {health['ok']}")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.drippay.dev/v1",
        timeout: float = 30.0,
    ) -> None:
        """Create a new async Drip SDK client."""
        resolved_api_key = api_key or os.environ.get("DRIP_API_KEY")
        if not resolved_api_key:
            msg = "Drip API key is required. Pass api_key or set DRIP_API_KEY."
            raise ValueError(msg)

        self._api_key = resolved_api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> AsyncDrip:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the Drip API."""
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                json=json,
            )
        except httpx.TimeoutException as e:
            raise DripNetworkError(f"Request timed out: {e}") from e
        except httpx.RequestError as e:
            raise DripNetworkError(f"Network error: {e}") from e

        if response.status_code == 204:
            return {"success": True}

        try:
            data = response.json()
        except Exception:
            data = {}

        if response.status_code == 401:
            raise DripAuthenticationError(data.get("message", "Authentication failed"))

        if not response.is_success:
            raise create_api_error_from_response(response.status_code, data)

        return data

    async def ping(self) -> dict[str, Any]:
        """Ping the Drip API to check connectivity."""
        health_base_url = self._base_url
        if health_base_url.endswith("/v1"):
            health_base_url = health_base_url[:-3]
        health_base_url = health_base_url.rstrip("/")

        start = time.time()

        try:
            response = await self._client.get(
                f"{health_base_url}/health",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            latency_ms = int((time.time() - start) * 1000)

            status = "unknown"
            timestamp = int(time.time() * 1000)

            try:
                data = response.json()
                if isinstance(data.get("status"), str):
                    status = data["status"]
                if isinstance(data.get("timestamp"), int):
                    timestamp = data["timestamp"]
            except Exception:
                status = "healthy" if response.is_success else f"error:{response.status_code}"

            return {
                "ok": response.is_success and status == "healthy",
                "status": status,
                "latency_ms": latency_ms,
                "timestamp": timestamp,
            }
        except httpx.TimeoutException:
            raise DripNetworkError("Request timed out")
        except httpx.RequestError as e:
            raise DripNetworkError(f"Network error: {e}") from e

    async def create_customer(
        self,
        onchain_address: str | None = None,
        external_customer_id: str | None = None,
        is_internal: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Customer:
        """
        Create a new customer.

        At least one of ``onchain_address`` or ``external_customer_id`` is required.
        """
        payload: dict[str, Any] = {}
        if onchain_address:
            payload["onchainAddress"] = onchain_address
        if external_customer_id:
            payload["externalCustomerId"] = external_customer_id
        if is_internal is not None:
            payload["isInternal"] = is_internal
        if metadata:
            payload["metadata"] = metadata

        data = await self._request("POST", "/customers", json=payload)

        return Customer(
            id=data["id"],
            business_id=data.get("businessId"),
            external_customer_id=data.get("externalCustomerId"),
            onchain_address=data.get("onchainAddress"),
            metadata=data.get("metadata"),
            created_at=data["createdAt"],
            updated_at=data["updatedAt"],
        )

    async def get_customer(self, customer_id: str) -> Customer:
        """Retrieve a customer by ID."""
        data = await self._request("GET", f"/customers/{customer_id}")

        return Customer(
            id=data["id"],
            business_id=data.get("businessId"),
            external_customer_id=data.get("externalCustomerId"),
            onchain_address=data.get("onchainAddress"),
            metadata=data.get("metadata"),
            created_at=data["createdAt"],
            updated_at=data["updatedAt"],
        )

    async def list_customers(
        self,
        limit: int | None = None,
        status: CustomerStatus | None = None,
    ) -> ListCustomersResponse:
        """List all customers."""
        params: list[str] = []
        if limit:
            params.append(f"limit={limit}")
        if status:
            params.append(f"status={status.value}")

        path = "/customers"
        if params:
            path = f"{path}?{'&'.join(params)}"

        data = await self._request("GET", path)

        customers = [
            Customer(
                id=c["id"],
                business_id=c.get("businessId"),
                external_customer_id=c.get("externalCustomerId"),
                onchain_address=c.get("onchainAddress"),
                metadata=c.get("metadata"),
                created_at=c["createdAt"],
                updated_at=c["updatedAt"],
            )
            for c in data.get("data", [])
        ]

        return ListCustomersResponse(data=customers, count=data.get("count", len(customers)))

    @overload
    async def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch"] = "batch",
    ) -> TrackUsageBatchResult: ...

    @overload
    async def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch", "sync"] = "sync",
    ) -> TrackUsageResult: ...

    async def track_usage(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        units: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        mode: Literal["batch", "sync"] = "sync",
    ) -> TrackUsageResult | TrackUsageBatchResult:
        """Record usage for tracking WITHOUT billing."""
        payload: dict[str, Any] = {
            "customerId": customer_id,
            "usageType": meter,
            "quantity": quantity,
        }
        if idempotency_key:
            payload["idempotencyKey"] = idempotency_key
        if units:
            payload["units"] = units
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata

        if mode not in ("batch", "sync"):
            raise DripError("mode must be 'batch' or 'sync'")
        path = "/usage/internal" if mode == "sync" else "/usage/internal/batch"
        data = await self._request("POST", path, json=payload)

        if mode == "batch":
            return TrackUsageBatchResult(
                success=data.get("success", True),
                mode="batch",
                customer_id=data.get("customerId", customer_id),
                usage_type=data.get("usageType", meter),
                quantity=data.get("quantity", quantity),
                idempotency_key=data.get("idempotencyKey"),
                pending_events=data.get("pendingEvents"),
                message=data.get("message", "Usage tracked"),
            )

        return TrackUsageResult(
            success=data.get("success", True),
            usage_event_id=data.get("usageEventId", ""),
            customer_id=data.get("customerId", customer_id),
            usage_type=data.get("usageType", meter),
            quantity=data.get("quantity", quantity),
            is_internal=data.get("isInternal", True),
            message=data.get("message", "Usage tracked"),
        )

    async def _create_workflow(
        self,
        name: str,
        slug: str,
        product_surface: str = "CUSTOM",
    ) -> _Workflow:
        """Create a workflow (internal)."""
        payload = {"name": name, "slug": slug, "productSurface": product_surface}
        data = await self._request("POST", "/workflows", json=payload)

        return _Workflow(
            id=data["id"],
            name=data["name"],
            slug=data["slug"],
            product_surface=data.get("productSurface", "CUSTOM"),
            description=data.get("description"),
            is_active=data.get("isActive", True),
            created_at=data["createdAt"],
        )

    async def _list_workflows(self) -> list[_Workflow]:
        """List workflows (internal)."""
        data = await self._request("GET", "/workflows")

        return [
            _Workflow(
                id=w["id"],
                name=w["name"],
                slug=w["slug"],
                product_surface=w.get("productSurface", "CUSTOM"),
                description=w.get("description"),
                is_active=w.get("isActive", True),
                created_at=w["createdAt"],
            )
            for w in data.get("data", [])
        ]

    async def start_run(
        self,
        customer_id: str,
        workflow_id: str,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunResult:
        """Start a new run."""
        payload: dict[str, Any] = {"customerId": customer_id, "workflowId": workflow_id}
        if external_run_id:
            payload["externalRunId"] = external_run_id
        if correlation_id:
            payload["correlationId"] = correlation_id
        if parent_run_id:
            payload["parentRunId"] = parent_run_id
        if metadata:
            payload["metadata"] = metadata

        data = await self._request("POST", "/runs", json=payload)

        return RunResult(
            id=data["id"],
            customer_id=data["customerId"],
            workflow_id=data["workflowId"],
            workflow_name=data.get("workflowName", ""),
            status=RunStatus(data.get("status", "RUNNING")),
            correlation_id=data.get("correlationId"),
            created_at=data["createdAt"],
        )

    async def end_run(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EndRunResult:
        """End a run."""
        payload: dict[str, Any] = {"status": status}
        if error_message:
            payload["errorMessage"] = error_message
        if error_code:
            payload["errorCode"] = error_code
        if metadata:
            payload["metadata"] = metadata

        data = await self._request("PATCH", f"/runs/{run_id}", json=payload)

        return EndRunResult(
            id=data["id"],
            status=RunStatus(data.get("status", status)),
            ended_at=data.get("endedAt"),
            duration_ms=data.get("durationMs"),
            event_count=data.get("eventCount", 0),
            total_cost_units=data.get("totalCostUnits"),
        )

    async def get_run_timeline(self, run_id: str) -> RunTimeline:
        """Get a run's full timeline."""
        data = await self._request("GET", f"/runs/{run_id}")

        run_data = data.get("run", data)
        run = RunTimelineRun(
            id=run_data["id"],
            customer_id=run_data["customerId"],
            customer_name=run_data.get("customerName"),
            workflow_id=run_data["workflowId"],
            workflow_name=run_data.get("workflowName", ""),
            status=RunStatus(run_data.get("status", "RUNNING")),
            started_at=run_data.get("startedAt"),
            ended_at=run_data.get("endedAt"),
            duration_ms=run_data.get("durationMs"),
            error_message=run_data.get("errorMessage"),
            error_code=run_data.get("errorCode"),
            correlation_id=run_data.get("correlationId"),
            metadata=run_data.get("metadata"),
        )

        timeline = [
            TimelineEvent(
                id=e["id"],
                event_type=e["eventType"],
                quantity=e.get("quantity", 0),
                units=e.get("units"),
                description=e.get("description"),
                cost_units=e.get("costUnits"),
                timestamp=e["timestamp"],
                correlation_id=e.get("correlationId"),
                parent_event_id=e.get("parentEventId"),
                charge=e.get("charge"),
            )
            for e in data.get("timeline", [])
        ]

        totals_data = data.get("totals", {})
        totals = RunTimelineTotals(
            event_count=totals_data.get("eventCount", 0),
            total_quantity=totals_data.get("totalQuantity", "0"),
            total_cost_units=totals_data.get("totalCostUnits", "0"),
            total_charged_usdc=totals_data.get("totalChargedUsdc", "0"),
        )

        return RunTimeline(run=run, timeline=timeline, totals=totals, summary=data.get("summary", ""))

    async def emit_event(
        self,
        run_id: str,
        event_type: str,
        quantity: float | None = None,
        units: str | None = None,
        description: str | None = None,
        cost_units: float | None = None,
        cost_currency: str | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
        span_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        """Emit an event to a run."""
        payload: dict[str, Any] = {"runId": run_id, "eventType": event_type}
        if quantity is not None:
            payload["quantity"] = quantity
        if units:
            payload["units"] = units
        if description:
            payload["description"] = description
        if cost_units is not None:
            payload["costUnits"] = cost_units
        if cost_currency:
            payload["costCurrency"] = cost_currency
        if correlation_id:
            payload["correlationId"] = correlation_id
        if parent_event_id:
            payload["parentEventId"] = parent_event_id
        if span_id:
            payload["spanId"] = span_id
        if idempotency_key:
            payload["idempotencyKey"] = idempotency_key
        if metadata:
            payload["metadata"] = metadata

        data = await self._request("POST", "/run-events", json=payload)

        return EventResult(
            id=data["id"],
            run_id=data["runId"],
            event_type=data["eventType"],
            quantity=data.get("quantity", 0),
            cost_units=data.get("costUnits"),
            is_duplicate=data.get("isDuplicate", False),
            timestamp=data["timestamp"],
        )

    async def emit_events_batch(self, events: list[dict[str, Any]]) -> EmitEventsBatchResult:
        """Emit multiple events in a single request."""
        data = await self._request("POST", "/run-events/batch", json={"events": events})

        return EmitEventsBatchResult(
            success=data.get("success", True),
            created=data.get("created", 0),
            duplicates=data.get("duplicates", 0),
            events=data.get("events", []),
        )

    async def record_run(
        self,
        customer_id: str,
        workflow: str,
        events: list[dict[str, Any] | RecordRunEvent],
        status: str,
        error_message: str | None = None,
        error_code: str | None = None,
        external_run_id: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RecordRunResult:
        """Record a complete request/run in a single call."""
        normalized_events = _normalize_events(events)

        body: dict[str, Any] = {
            "customerId": customer_id,
            "workflow": workflow,
            "events": normalized_events,
            "status": status,
        }
        if error_message:
            body["errorMessage"] = error_message
        if error_code:
            body["errorCode"] = error_code
        if external_run_id:
            body["externalRunId"] = external_run_id
        if correlation_id:
            body["correlationId"] = correlation_id
        if metadata:
            body["metadata"] = metadata

        # Try single-call endpoint; fall back to 4-step if server returns 404
        try:
            data = await self._request("POST", "/runs/record", json=body)
            run_data = data.get("run", {})
            events_data = data.get("events", {})
            return RecordRunResult(
                run=RecordRunResultRun(
                    id=run_data.get("id", ""),
                    workflow_id=run_data.get("workflowId", ""),
                    workflow_name=run_data.get("workflowName", ""),
                    status=run_data.get("status", status),
                    duration_ms=run_data.get("durationMs"),
                ),
                events=RecordRunResultEvents(
                    created=events_data.get("created", 0),
                    duplicates=events_data.get("duplicates", 0),
                ),
                total_cost_units=data.get("totalCostUnits"),
                summary=data.get("summary", ""),
            )
        except DripError as e:
            if e.status_code != 404:
                raise

        return await self._record_run_fallback(
            customer_id, workflow, normalized_events, status,
            error_message, error_code, external_run_id, correlation_id, metadata,
        )

    async def _record_run_fallback(
        self,
        customer_id: str,
        workflow: str,
        events: list[dict[str, Any]],
        status: str,
        error_message: str | None,
        error_code: str | None,
        external_run_id: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> RecordRunResult:
        """4-step orchestration fallback for servers without POST /runs/record."""
        start = time.monotonic()

        # Step 1: Resolve workflow
        workflows = await self._list_workflows()
        match = next(
            (w for w in workflows if w.slug == workflow or w.id == workflow),
            None,
        )
        if match:
            workflow_id = match.id
            workflow_name = match.name
        else:
            pretty = workflow.replace("_", " ").replace("-", " ").title()
            created = await self._create_workflow(name=pretty, slug=workflow)
            workflow_id = created.id
            workflow_name = created.name

        # Step 2: Start run
        run = await self.start_run(
            customer_id=customer_id,
            workflow_id=workflow_id,
            external_run_id=external_run_id,
            correlation_id=correlation_id,
            metadata=metadata,
        )

        # Step 3: Emit events
        events_created = 0
        events_duplicates = 0
        if events:
            batch: list[dict[str, Any]] = []
            for i, evt in enumerate(events):
                entry: dict[str, Any] = {
                    "runId": run.id,
                    "eventType": evt["eventType"],
                    "quantity": evt.get("quantity", 1),
                }
                for key in ("units", "description", "costUnits", "metadata"):
                    if key in evt:
                        entry[key] = evt[key]
                if external_run_id:
                    entry["idempotencyKey"] = f"{external_run_id}:{evt['eventType']}:{i}"
                batch.append(entry)
            result = await self.emit_events_batch(batch)
            events_created = result.created
            events_duplicates = result.duplicates

        # Step 4: End run
        end_result = await self.end_run(
            run_id=run.id,
            status=status,
            error_message=error_message,
            error_code=error_code,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        dur = end_result.duration_ms if end_result.duration_ms is not None else elapsed_ms
        icon = "\u2713" if status == "COMPLETED" else "\u2717" if status == "FAILED" else "\u25CB"

        return RecordRunResult(
            run=RecordRunResultRun(
                id=run.id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                status=end_result.status.value if hasattr(end_result.status, 'value') else status,
                duration_ms=dur,
            ),
            events=RecordRunResultEvents(created=events_created, duplicates=events_duplicates),
            total_cost_units=end_result.total_cost_units,
            summary=f"{icon} {workflow_name}: {events_created} events recorded ({dur}ms)",
        )
