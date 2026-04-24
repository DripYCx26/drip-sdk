"""
Drip middleware core logic.

This module contains framework-agnostic middleware logic for processing
requests, resolving customers, handling payments, and creating charges.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

from ..client import AsyncDrip, Drip
from ..errors import (
    DripMiddlewareError,
    DripMiddlewareErrorCode,
    DripPaymentRequiredError,
)
from ..models import ChargeResult, X402PaymentProof, X402PaymentRequest
from ..utils import current_timestamp, generate_nonce
from .types import (
    DripContext,
    DripMiddlewareConfig,
    PaymentRequestHeaders,
    ProcessRequestResult,
)

T = TypeVar("T")

# =============================================================================
# Billing Identity Headers (MUST NOT be trusted from clients)
# =============================================================================

BILLING_IDENTITY_HEADERS: tuple[str, ...] = (
    "x-drip-customer-id",
    "x-customer-id",
)

BILLING_IDENTITY_QUERY_PARAMS: tuple[str, ...] = (
    "customer_id",
    "customerid",
    "drip_customer_id",
    "dripcustomerid",
)


class _SanitizedMapping(Mapping[str, Any]):
    """Read-only mapping with blocked keys removed."""

    def __init__(
        self,
        source: Any,
        *,
        blocked_keys: set[str],
        case_insensitive_lookup: bool = False,
    ) -> None:
        self._case_insensitive_lookup = case_insensitive_lookup
        self._items: list[tuple[str, Any]] = []
        self._lookup: dict[str, Any] = {}
        self._lists: dict[str, list[Any]] = {}

        if source is None:
            return

        if hasattr(source, "multi_items"):
            raw_items = list(source.multi_items())
        elif hasattr(source, "items"):
            raw_items = list(source.items())
        else:
            raw_items = list(dict(source).items())

        for key, value in raw_items:
            key_str = str(key)
            normalized = key_str.lower()
            if normalized in blocked_keys:
                continue

            self._items.append((key_str, value))

            lookup_key = normalized if case_insensitive_lookup else key_str
            self._lookup[lookup_key] = value
            self._lists.setdefault(lookup_key, []).append(value)

    def __getitem__(self, key: str) -> Any:
        lookup_key = key.lower() if self._case_insensitive_lookup else key
        return self._lookup[lookup_key]

    def __iter__(self):
        for key, _ in self._items:
            yield key

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        lookup_key = key.lower() if self._case_insensitive_lookup else key
        return self._lookup.get(lookup_key, default)

    def items(self):  # type: ignore[override]
        return list(self._items)

    def getlist(self, key: str) -> list[Any]:
        lookup_key = key.lower() if self._case_insensitive_lookup else key
        return list(self._lists.get(lookup_key, []))


class _CustomerResolverRequestView:
    """
    Sanitized request view for customer resolver callbacks.

    Security: strips client-controlled billing identity fields so resolver
    implementations cannot accidentally trust spoofable values.
    """

    def __init__(self, request: Any) -> None:
        self._request = request
        self.headers = _SanitizedMapping(
            getattr(request, "headers", {}),
            blocked_keys=set(BILLING_IDENTITY_HEADERS),
            case_insensitive_lookup=True,
        )

        if hasattr(request, "query_params"):
            self.query_params = _SanitizedMapping(
                getattr(request, "query_params", {}),
                blocked_keys=set(BILLING_IDENTITY_QUERY_PARAMS),
            )

        if hasattr(request, "args"):
            self.args = _SanitizedMapping(
                getattr(request, "args", {}),
                blocked_keys=set(BILLING_IDENTITY_QUERY_PARAMS),
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)


# =============================================================================
# Header Utilities
# =============================================================================


def get_header(headers: dict[str, Any], name: str) -> str | None:
    """
    Get a header value case-insensitively.

    Args:
        headers: Dictionary of headers.
        name: Header name to look for.

    Returns:
        Header value or None if not found.
    """
    name_lower = name.lower()

    for key, value in headers.items():
        if key.lower() == name_lower:
            if isinstance(value, list):
                return value[0] if value else None
            return str(value) if value is not None else None

    return None


def has_payment_proof_headers(headers: dict[str, Any]) -> bool:
    """
    Check if request has x402 payment proof headers.

    Args:
        headers: Request headers.

    Returns:
        True if payment proof headers are present.
    """
    signature = get_header(headers, "X-Payment-Signature")
    return signature is not None and len(signature) > 0


# =============================================================================
# Payment Proof Parsing
# =============================================================================


def is_valid_hex(value: str) -> bool:
    """Check if a string is valid hex."""
    if not value:
        return False
    clean = value[2:] if value.lower().startswith("0x") else value
    return bool(re.match(r"^[0-9a-fA-F]+$", clean))


def parse_payment_proof(headers: dict[str, Any]) -> X402PaymentProof | None:
    """
    Parse x402 payment proof from request headers.

    Args:
        headers: Request headers.

    Returns:
        X402PaymentProof if valid headers present, None otherwise.
    """
    signature = get_header(headers, "X-Payment-Signature")
    session_key_id = get_header(headers, "X-Payment-Session-Key")
    smart_account = get_header(headers, "X-Payment-Smart-Account")
    timestamp_str = get_header(headers, "X-Payment-Timestamp")
    amount = get_header(headers, "X-Payment-Amount")
    recipient = get_header(headers, "X-Payment-Recipient")
    usage_id = get_header(headers, "X-Payment-Usage-Id")
    nonce = get_header(headers, "X-Payment-Nonce")

    # Validate required fields
    if not all([signature, session_key_id, smart_account, timestamp_str, amount, recipient, usage_id, nonce]):
        return None

    # Validate hex values
    if not is_valid_hex(signature):  # type: ignore[arg-type]
        return None
    if not is_valid_hex(smart_account):  # type: ignore[arg-type]
        return None

    # Parse timestamp
    try:
        timestamp = int(timestamp_str)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None

    # Check timestamp freshness (5 minutes)
    now = current_timestamp()
    if abs(now - timestamp) > 300:
        return None

    return X402PaymentProof.model_validate({
        "signature": signature,
        "sessionKeyId": session_key_id,
        "smartAccount": smart_account,
        "timestamp": timestamp,
        "amount": amount,
        "recipient": recipient,
        "usageId": usage_id,
        "nonce": nonce,
    })


# =============================================================================
# Payment Request Generation
# =============================================================================


def generate_payment_request(
    amount: str,
    recipient: str,
    usage_id: str,
    description: str,
    expires_in_seconds: int = 300,
) -> tuple[PaymentRequestHeaders, X402PaymentRequest]:
    """
    Generate a 402 payment request.

    Args:
        amount: Amount in smallest unit.
        recipient: Payment recipient address.
        usage_id: Usage ID for tracking.
        description: Human-readable description.
        expires_in_seconds: Expiration time (default 5 minutes).

    Returns:
        Tuple of (headers, payment_request).
    """
    now = current_timestamp()
    nonce = generate_nonce(16)
    expires_at = now + expires_in_seconds

    headers = PaymentRequestHeaders(
        x_payment_required="true",
        x_payment_amount=amount,
        x_payment_recipient=recipient,
        x_payment_usage_id=usage_id,
        x_payment_description=description,
        x_payment_expires=str(expires_at),
        x_payment_nonce=nonce,
        x_payment_timestamp=str(now),
    )

    payment_request = X402PaymentRequest.model_validate({
        "amount": amount,
        "recipient": recipient,
        "usageId": usage_id,
        "description": description,
        "expiresAt": expires_at,
        "nonce": nonce,
        "timestamp": now,
    })

    return headers, payment_request


# =============================================================================
# Customer Resolution
# =============================================================================


def _normalize_resolved_customer_id(result: Any) -> str:
    """Validate and normalize a customer ID returned by a resolver."""
    if result is None:
        raise DripMiddlewareError(
            "Customer resolver returned no customer ID.",
            DripMiddlewareErrorCode.CUSTOMER_RESOLUTION_FAILED,
            status_code=400,
        )

    customer_id = str(result).strip()
    if not customer_id:
        raise DripMiddlewareError(
            "Customer resolver returned an empty customer ID.",
            DripMiddlewareErrorCode.CUSTOMER_RESOLUTION_FAILED,
            status_code=400,
        )
    return customer_id


def resolve_customer_id_sync(
    request: Any,
    config: DripMiddlewareConfig,
) -> str:
    """
    Resolve customer ID from request (sync version).

    Args:
        request: The HTTP request.
        config: Middleware configuration.

    Returns:
        Customer ID.

    Raises:
        DripMiddlewareError: If customer cannot be resolved.
    """
    resolver = config.customer_resolver

    if not callable(resolver):
        raise DripMiddlewareError(
            "customer_resolver must be a callable that resolves the customer ID "
            "from a verified authentication source (e.g., session token, JWT). "
            "Trusting unauthenticated client headers or query parameters is insecure.",
            DripMiddlewareErrorCode.CONFIGURATION_ERROR,
            status_code=500,
        )

    try:
        resolver_request = _CustomerResolverRequestView(request)
        result = resolver(resolver_request)
        # Handle coroutines in sync context
        if asyncio.iscoroutine(result):
            raise DripMiddlewareError(
                "Async customer resolver used in sync context",
                DripMiddlewareErrorCode.CONFIGURATION_ERROR,
                status_code=500,
            )
        return _normalize_resolved_customer_id(result)
    except DripMiddlewareError:
        raise
    except Exception as e:
        logger.exception("Customer resolution failed: %s", e)
        raise DripMiddlewareError(
            "Customer resolution failed.",
            DripMiddlewareErrorCode.CUSTOMER_RESOLUTION_FAILED,
            status_code=500,
        ) from e


async def resolve_customer_id_async(
    request: Any,
    config: DripMiddlewareConfig,
) -> str:
    """
    Resolve customer ID from request (async version).

    Args:
        request: The HTTP request.
        config: Middleware configuration.

    Returns:
        Customer ID.

    Raises:
        DripMiddlewareError: If customer cannot be resolved.
    """
    resolver = config.customer_resolver

    if not callable(resolver):
        raise DripMiddlewareError(
            "customer_resolver must be a callable that resolves the customer ID "
            "from a verified authentication source (e.g., session token, JWT). "
            "Trusting unauthenticated client headers or query parameters is insecure.",
            DripMiddlewareErrorCode.CONFIGURATION_ERROR,
            status_code=500,
        )

    try:
        resolver_request = _CustomerResolverRequestView(request)
        result = resolver(resolver_request)
        if asyncio.iscoroutine(result):
            result = await result
        return _normalize_resolved_customer_id(result)
    except DripMiddlewareError:
        raise
    except Exception as e:
        logger.exception("Customer resolution failed: %s", e)
        raise DripMiddlewareError(
            "Customer resolution failed.",
            DripMiddlewareErrorCode.CUSTOMER_RESOLUTION_FAILED,
            status_code=500,
        ) from e


# =============================================================================
# Quantity Resolution
# =============================================================================


def resolve_quantity_sync(request: Any, config: DripMiddlewareConfig) -> float:
    """Resolve quantity (sync version)."""
    quantity = config.quantity

    if callable(quantity):
        result = quantity(request)
        if asyncio.iscoroutine(result):
            raise DripMiddlewareError(
                "Async quantity resolver used in sync context",
                DripMiddlewareErrorCode.CONFIGURATION_ERROR,
                status_code=500,
            )
        # Result is guaranteed to be float at this point
        return float(result)  # type: ignore[arg-type]

    return float(quantity)


async def resolve_quantity_async(request: Any, config: DripMiddlewareConfig) -> float:
    """Resolve quantity (async version)."""
    quantity = config.quantity

    if callable(quantity):
        result = quantity(request)
        if asyncio.iscoroutine(result):
            result = await result
        return float(result)  # type: ignore[arg-type]

    return float(quantity)


# =============================================================================
# Idempotency Key Generation
# =============================================================================


def generate_request_idempotency_key_sync(
    request: Any,
    customer_id: str,
    config: DripMiddlewareConfig,
) -> str | None:
    """Generate idempotency key for request (sync version).

    Returns None when no custom generator is configured, allowing the
    core SDK's built-in counter-based key (which includes per-call
    uniqueness) to be used instead.
    """
    if config.idempotency_key:
        result = config.idempotency_key(request, customer_id)
        if asyncio.iscoroutine(result):
            raise DripMiddlewareError(
                "Async idempotency key generator used in sync context",
                DripMiddlewareErrorCode.CONFIGURATION_ERROR,
                status_code=500,
            )
        return str(result)

    # Let the core SDK generate a unique key with its monotonic counter
    return None


async def generate_request_idempotency_key_async(
    request: Any,
    customer_id: str,
    config: DripMiddlewareConfig,
) -> str | None:
    """Generate idempotency key for request (async version).

    Returns None when no custom generator is configured, allowing the
    core SDK's built-in counter-based key (which includes per-call
    uniqueness) to be used instead.
    """
    if config.idempotency_key:
        result = config.idempotency_key(request, customer_id)
        if asyncio.iscoroutine(result):
            result = await result
        return str(result)

    # Let the core SDK generate a unique key with its monotonic counter
    return None


# =============================================================================
# Metadata Resolution
# =============================================================================


def resolve_metadata_sync(
    request: Any,
    config: DripMiddlewareConfig,
) -> dict[str, Any] | None:
    """Resolve metadata (sync version)."""
    metadata = config.metadata

    if metadata is None:
        return None

    if callable(metadata):
        result = metadata(request)
        if asyncio.iscoroutine(result):
            raise DripMiddlewareError(
                "Async metadata resolver used in sync context",
                DripMiddlewareErrorCode.CONFIGURATION_ERROR,
                status_code=500,
            )
        return result

    return metadata


async def resolve_metadata_async(
    request: Any,
    config: DripMiddlewareConfig,
) -> dict[str, Any] | None:
    """Resolve metadata (async version)."""
    metadata = config.metadata

    if metadata is None:
        return None

    if callable(metadata):
        result = metadata(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    return metadata


# =============================================================================
# Client Creation
# =============================================================================


def create_drip_client(config: DripMiddlewareConfig) -> Drip:
    """Create a sync Drip client from config."""
    return Drip(
        api_key=config.api_key or os.environ.get("DRIP_API_KEY"),
        base_url=config.base_url or os.environ.get("DRIP_API_URL"),
    )


def create_async_drip_client(config: DripMiddlewareConfig) -> AsyncDrip:
    """Create an async Drip client from config."""
    return AsyncDrip(
        api_key=config.api_key or os.environ.get("DRIP_API_KEY"),
        base_url=config.base_url or os.environ.get("DRIP_API_URL"),
    )


# =============================================================================
# Request Processing
# =============================================================================


def process_request_sync(
    request: Any,
    config: DripMiddlewareConfig,
) -> ProcessRequestResult:
    """
    Process a request through the middleware (sync version).

    Args:
        request: The HTTP request.
        config: Middleware configuration.

    Returns:
        ProcessRequestResult with success status and context or error.
    """
    # Check for development mode skip
    if config.skip_in_development:
        env = os.environ.get("DRIP_ENV") or os.environ.get("NODE_ENV") or os.environ.get("ENVIRONMENT")
        if env in ("development", "dev", "local"):
            # Return mock successful response
            from ..models import ChargeInfo, ChargeStatus

            mock_charge_info = ChargeInfo.model_validate({
                "id": "dev_mock_charge",
                "amountUsdc": "0",
                "amountToken": "0",
                "txHash": "0x0",
                "status": ChargeStatus.CONFIRMED,
            })
            mock_charge = ChargeResult.model_validate({
                "success": True,
                "usageEventId": "dev_mock_usage",
                "isDuplicate": False,
                "charge": mock_charge_info,
            })
            client = create_drip_client(config)
            return ProcessRequestResult(
                success=True,
                context=DripContext(
                    drip=client,
                    customer_id="dev_mock_customer",
                    charge=mock_charge,
                    is_duplicate=False,
                ),
            )

    try:
        # Resolve customer ID
        customer_id = resolve_customer_id_sync(request, config)

        # Resolve quantity
        quantity = resolve_quantity_sync(request, config)

        # Generate idempotency key
        idempotency_key = generate_request_idempotency_key_sync(request, customer_id, config)

        # Resolve metadata
        metadata = resolve_metadata_sync(request, config)

        # Create client and charge
        client = create_drip_client(config)

        try:
            charge_result = client.charge(
                customer_id=customer_id,
                meter=config.meter,
                quantity=quantity,
                idempotency_key=idempotency_key,
                metadata=metadata,
            )
        except DripPaymentRequiredError as e:
            # Generate 402 response
            payment_request = e.payment_request or {}
            headers, pr = generate_payment_request(
                amount=payment_request.get("amount", "0"),
                recipient=payment_request.get("recipient", ""),
                usage_id=payment_request.get("usageId", ""),
                description=f"Payment for {config.meter}",
            )
            return ProcessRequestResult(
                success=False,
                payment_required=True,
                payment_request=pr,
                payment_headers=headers,
            )

        # Reject duplicate charges to prevent billing bypass
        if charge_result.is_duplicate:
            error = DripMiddlewareError(
                "Duplicate charge detected — request already billed",
                DripMiddlewareErrorCode.DUPLICATE_CHARGE,
                status_code=409,
            )
            if config.on_error:
                config.on_error(error, request)
            return ProcessRequestResult(success=False, error=error)

        # Call success callback if provided
        if config.on_charge:
            config.on_charge(charge_result, request)

        return ProcessRequestResult(
            success=True,
            context=DripContext(
                drip=client,
                customer_id=customer_id,
                charge=charge_result,
                is_duplicate=False,
            ),
        )

    except DripMiddlewareError as e:
        if config.on_error:
            config.on_error(e, request)
        return ProcessRequestResult(success=False, error=e)

    except Exception as e:
        logger.exception("Unexpected error in sync request processing: %s", e)
        error = DripMiddlewareError(
            "An internal error occurred while processing the request.",
            DripMiddlewareErrorCode.INTERNAL_ERROR,
            status_code=500,
        )
        if config.on_error:
            config.on_error(error, request)
        return ProcessRequestResult(success=False, error=error)


async def process_request_async(
    request: Any,
    config: DripMiddlewareConfig,
) -> ProcessRequestResult:
    """
    Process a request through the middleware (async version).

    Args:
        request: The HTTP request.
        config: Middleware configuration.

    Returns:
        ProcessRequestResult with success status and context or error.
    """
    # Check for development mode skip
    if config.skip_in_development:
        env = os.environ.get("DRIP_ENV") or os.environ.get("NODE_ENV") or os.environ.get("ENVIRONMENT")
        if env in ("development", "dev", "local"):
            from ..models import ChargeInfo, ChargeStatus

            mock_charge_info = ChargeInfo.model_validate({
                "id": "dev_mock_charge",
                "amountUsdc": "0",
                "amountToken": "0",
                "txHash": "0x0",
                "status": ChargeStatus.CONFIRMED,
            })
            mock_charge = ChargeResult.model_validate({
                "success": True,
                "usageEventId": "dev_mock_usage",
                "isDuplicate": False,
                "charge": mock_charge_info,
            })
            client = create_async_drip_client(config)
            return ProcessRequestResult(
                success=True,
                context=DripContext(
                    drip=client,
                    customer_id="dev_mock_customer",
                    charge=mock_charge,
                    is_duplicate=False,
                ),
            )

    try:
        # Resolve customer ID
        customer_id = await resolve_customer_id_async(request, config)

        # Resolve quantity
        quantity = await resolve_quantity_async(request, config)

        # Generate idempotency key
        idempotency_key = await generate_request_idempotency_key_async(request, customer_id, config)

        # Resolve metadata
        metadata = await resolve_metadata_async(request, config)

        # Create client and charge
        client = create_async_drip_client(config)

        try:
            charge_result = await client.charge(
                customer_id=customer_id,
                meter=config.meter,
                quantity=quantity,
                idempotency_key=idempotency_key,
                metadata=metadata,
            )
        except DripPaymentRequiredError as e:
            payment_request = e.payment_request or {}
            headers, pr = generate_payment_request(
                amount=payment_request.get("amount", "0"),
                recipient=payment_request.get("recipient", ""),
                usage_id=payment_request.get("usageId", ""),
                description=f"Payment for {config.meter}",
            )
            return ProcessRequestResult(
                success=False,
                payment_required=True,
                payment_request=pr,
                payment_headers=headers,
            )

        # Reject duplicate charges to prevent billing bypass
        if charge_result.is_duplicate:
            error = DripMiddlewareError(
                "Duplicate charge detected — request already billed",
                DripMiddlewareErrorCode.DUPLICATE_CHARGE,
                status_code=409,
            )
            if config.on_error:
                cb_result = config.on_error(error, request)
                if asyncio.iscoroutine(cb_result):
                    await cb_result
            return ProcessRequestResult(success=False, error=error)

        # Call success callback if provided
        if config.on_charge:
            result = config.on_charge(charge_result, request)
            if asyncio.iscoroutine(result):
                await result

        return ProcessRequestResult(
            success=True,
            context=DripContext(
                drip=client,
                customer_id=customer_id,
                charge=charge_result,
                is_duplicate=False,
            ),
        )

    except DripMiddlewareError as e:
        if config.on_error:
            result = config.on_error(e, request)
            if asyncio.iscoroutine(result):
                await result
        return ProcessRequestResult(success=False, error=e)

    except Exception as e:
        logger.exception("Unexpected error in async request processing: %s", e)
        error = DripMiddlewareError(
            "An internal error occurred while processing the request.",
            DripMiddlewareErrorCode.INTERNAL_ERROR,
            status_code=500,
        )
        if config.on_error:
            result = config.on_error(error, request)
            if asyncio.iscoroutine(result):
                await result
        return ProcessRequestResult(success=False, error=error)
