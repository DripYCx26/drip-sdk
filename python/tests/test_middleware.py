"""Tests for middleware core functionality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from drip.errors import DripMiddlewareError, DripMiddlewareErrorCode
from drip.middleware.core import (
    generate_payment_request,
    generate_request_idempotency_key_sync,
    get_header,
    has_payment_proof_headers,
    is_valid_hex,
    parse_payment_proof,
    process_request_async,
    process_request_sync,
)
from drip.middleware.types import DripMiddlewareConfig
from drip.models import ChargeInfo, ChargeResult, ChargeStatus
from drip.utils import current_timestamp

# =============================================================================
# Mock Request
# =============================================================================


@dataclass
class MockRequest:
    """Mock HTTP request for testing."""

    method: str = "GET"
    url: str = "/api/test"
    headers: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}
        if self.query_params is None:
            self.query_params = {}


def _mock_customer_resolver(_request: Any) -> str:
    """Stub resolver that always returns a fixed customer ID."""
    return "cus_test_123"


def _make_config(**overrides: Any) -> DripMiddlewareConfig:
    """Build a DripMiddlewareConfig with sensible defaults for tests."""
    defaults: dict[str, Any] = {
        "meter": "api_calls",
        "quantity": 1.0,
        "customer_resolver": _mock_customer_resolver,
    }
    defaults.update(overrides)
    return DripMiddlewareConfig(**defaults)


def _make_charge_result(*, is_duplicate: bool = False) -> ChargeResult:
    """Build a ChargeResult for test assertions."""
    charge_info = ChargeInfo.model_validate({
        "id": "chg_test_001",
        "amountUsdc": "1000",
        "amountToken": "0",
        "txHash": "0xabc",
        "status": ChargeStatus.CONFIRMED,
    })
    return ChargeResult.model_validate({
        "success": True,
        "usageEventId": "evt_test_001",
        "isDuplicate": is_duplicate,
        "charge": charge_info,
    })


# =============================================================================
# Header Utilities Tests
# =============================================================================


class TestGetHeader:
    def test_get_header_exact_case(self) -> None:
        """Should find header with exact case match."""
        headers = {"Content-Type": "application/json"}
        assert get_header(headers, "Content-Type") == "application/json"

    def test_get_header_case_insensitive(self) -> None:
        """Should find header regardless of case."""
        headers = {"Content-Type": "application/json"}
        assert get_header(headers, "content-type") == "application/json"
        assert get_header(headers, "CONTENT-TYPE") == "application/json"

    def test_get_header_not_found(self) -> None:
        """Should return None for missing header."""
        headers = {"Content-Type": "application/json"}
        assert get_header(headers, "Authorization") is None

    def test_get_header_list_value(self) -> None:
        """Should return first value from list."""
        headers = {"Accept": ["application/json", "text/html"]}
        assert get_header(headers, "Accept") == "application/json"

    def test_get_header_empty_list(self) -> None:
        """Should return None for empty list."""
        headers = {"Accept": []}
        assert get_header(headers, "Accept") is None


class TestHasPaymentProofHeaders:
    def test_has_payment_proof_headers(self) -> None:
        """Should detect payment proof headers."""
        headers = {"X-Payment-Signature": "0xabc123"}
        assert has_payment_proof_headers(headers) is True

    def test_missing_payment_proof_headers(self) -> None:
        """Should return False without payment proof headers."""
        headers = {"Content-Type": "application/json"}
        assert has_payment_proof_headers(headers) is False

    def test_empty_signature(self) -> None:
        """Should return False for empty signature."""
        headers = {"X-Payment-Signature": ""}
        assert has_payment_proof_headers(headers) is False


# =============================================================================
# Payment Proof Parsing Tests
# =============================================================================


class TestParsePaymentProof:
    def test_parse_valid_payment_proof(self) -> None:
        """Should parse valid payment proof headers."""
        now = current_timestamp()
        headers = {
            "X-Payment-Signature": "0xabc123def456",
            "X-Payment-Session-Key": "sk_123",
            "X-Payment-Smart-Account": "0x1234567890abcdef1234567890abcdef12345678",
            "X-Payment-Timestamp": str(now),
            "X-Payment-Amount": "1000000",
            "X-Payment-Recipient": "0xrecipient",
            "X-Payment-Usage-Id": "usage_123",
            "X-Payment-Nonce": "nonce123",
        }

        proof = parse_payment_proof(headers)
        assert proof is not None
        assert proof.signature == "0xabc123def456"
        assert proof.session_key_id == "sk_123"
        assert proof.amount == "1000000"

    def test_parse_missing_signature(self) -> None:
        """Should return None for missing signature."""
        headers = {
            "X-Payment-Session-Key": "sk_123",
            "X-Payment-Smart-Account": "0x123",
        }
        assert parse_payment_proof(headers) is None

    def test_parse_invalid_signature(self) -> None:
        """Should return None for invalid hex signature."""
        now = current_timestamp()
        headers = {
            "X-Payment-Signature": "not-hex-zzz",
            "X-Payment-Session-Key": "sk_123",
            "X-Payment-Smart-Account": "0x1234567890abcdef1234567890abcdef12345678",
            "X-Payment-Timestamp": str(now),
            "X-Payment-Amount": "1000000",
            "X-Payment-Recipient": "0xrecipient",
            "X-Payment-Usage-Id": "usage_123",
            "X-Payment-Nonce": "nonce123",
        }
        assert parse_payment_proof(headers) is None

    def test_parse_expired_timestamp(self) -> None:
        """Should return None for expired timestamp (>5 minutes old)."""
        old_timestamp = current_timestamp() - 400  # 6+ minutes ago
        headers = {
            "X-Payment-Signature": "0xabc123",
            "X-Payment-Session-Key": "sk_123",
            "X-Payment-Smart-Account": "0x1234567890abcdef1234567890abcdef12345678",
            "X-Payment-Timestamp": str(old_timestamp),
            "X-Payment-Amount": "1000000",
            "X-Payment-Recipient": "0xrecipient",
            "X-Payment-Usage-Id": "usage_123",
            "X-Payment-Nonce": "nonce123",
        }
        assert parse_payment_proof(headers) is None

    def test_parse_invalid_timestamp(self) -> None:
        """Should return None for non-numeric timestamp."""
        headers = {
            "X-Payment-Signature": "0xabc123",
            "X-Payment-Session-Key": "sk_123",
            "X-Payment-Smart-Account": "0x1234567890abcdef1234567890abcdef12345678",
            "X-Payment-Timestamp": "invalid",
            "X-Payment-Amount": "1000000",
            "X-Payment-Recipient": "0xrecipient",
            "X-Payment-Usage-Id": "usage_123",
            "X-Payment-Nonce": "nonce123",
        }
        assert parse_payment_proof(headers) is None


# =============================================================================
# Payment Request Generation Tests
# =============================================================================


class TestGeneratePaymentRequest:
    def test_generate_payment_request(self) -> None:
        """Should generate payment request with headers."""
        headers, request = generate_payment_request(
            amount="1000000",
            recipient="0xrecipient",
            usage_id="usage_123",
            description="Test payment",
        )

        assert headers.x_payment_required == "true"
        assert headers.x_payment_amount == "1000000"
        assert headers.x_payment_recipient == "0xrecipient"
        assert headers.x_payment_usage_id == "usage_123"
        assert headers.x_payment_description == "Test payment"

        assert request.amount == "1000000"
        assert request.recipient == "0xrecipient"
        assert request.usage_id == "usage_123"
        assert request.description == "Test payment"
        assert len(request.nonce) > 0

    def test_generate_payment_request_expiration(self) -> None:
        """Should set correct expiration time."""
        now = current_timestamp()
        headers, request = generate_payment_request(
            amount="1000000",
            recipient="0x123",
            usage_id="usage_123",
            description="Test",
            expires_in_seconds=600,
        )

        # Should expire ~10 minutes from now
        assert request.expires_at >= now + 590
        assert request.expires_at <= now + 610

    def test_headers_to_dict(self) -> None:
        """Should convert headers to dictionary."""
        headers, _ = generate_payment_request(
            amount="1000000",
            recipient="0x123",
            usage_id="usage_123",
            description="Test",
        )

        header_dict = headers.to_dict()
        assert "X-Payment-Required" in header_dict
        assert "X-Payment-Amount" in header_dict
        assert header_dict["X-Payment-Required"] == "true"


# =============================================================================
# Hex Validation Tests
# =============================================================================


class TestIsValidHex:
    def test_valid_hex_lowercase(self) -> None:
        """Should accept lowercase hex."""
        assert is_valid_hex("abc123") is True

    def test_valid_hex_uppercase(self) -> None:
        """Should accept uppercase hex."""
        assert is_valid_hex("ABC123") is True

    def test_valid_hex_with_prefix(self) -> None:
        """Should accept hex with 0x prefix."""
        assert is_valid_hex("0xabc123") is True
        assert is_valid_hex("0XABC123") is True

    def test_invalid_hex(self) -> None:
        """Should reject non-hex strings."""
        assert is_valid_hex("xyz") is False
        assert is_valid_hex("0xghi") is False

    def test_empty_string(self) -> None:
        """Should reject empty string."""
        assert is_valid_hex("") is False


# =============================================================================
# Idempotency Key Generation Tests
# =============================================================================


class TestIdempotencyKeyGeneration:
    def test_default_returns_none_for_sdk_builtin(self) -> None:
        """Default key generation should return None to let SDK use its built-in counter."""
        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        key = generate_request_idempotency_key_sync(request, "cus_123", config)
        assert key is None

    def test_custom_generator_is_used(self) -> None:
        """Custom idempotency key generator should be called when provided."""
        def custom_key(_request: Any, customer_id: str) -> str:
            return f"custom-{customer_id}"

        config = _make_config(idempotency_key=custom_key)
        request = MockRequest(method="POST", url="/api/generate")

        key = generate_request_idempotency_key_sync(request, "cus_123", config)
        assert key == "custom-cus_123"

    def test_default_key_not_static_across_calls(self) -> None:
        """Without custom generator, None is returned so SDK generates unique keys."""
        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        key1 = generate_request_idempotency_key_sync(request, "cus_123", config)
        key2 = generate_request_idempotency_key_sync(request, "cus_123", config)

        # Both should be None, deferring to SDK's monotonic counter
        assert key1 is None
        assert key2 is None

    def test_async_generator_in_sync_raises(self) -> None:
        """Using an async idempotency key generator in sync context should raise."""
        async def async_key(_request: Any, customer_id: str) -> str:
            return f"async-{customer_id}"

        config = _make_config(idempotency_key=async_key)
        request = MockRequest(method="POST", url="/api/generate")

        with pytest.raises(DripMiddlewareError) as exc_info:
            generate_request_idempotency_key_sync(request, "cus_123", config)
        assert exc_info.value.middleware_code == DripMiddlewareErrorCode.CONFIGURATION_ERROR


# =============================================================================
# Duplicate Charge Rejection Tests
# =============================================================================


class TestDuplicateChargeRejection:
    def test_duplicate_charge_error_code_exists(self) -> None:
        """DUPLICATE_CHARGE error code should exist in the enum."""
        assert hasattr(DripMiddlewareErrorCode, "DUPLICATE_CHARGE")
        assert DripMiddlewareErrorCode.DUPLICATE_CHARGE == "DUPLICATE_CHARGE"

    @patch("drip.middleware.core.create_drip_client")
    def test_duplicate_charge_rejected_sync(self, mock_create_client: MagicMock) -> None:
        """process_request_sync must reject duplicate charges with 409."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=True)
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        result = process_request_sync(request, config)

        assert result.success is False
        assert result.context is None
        assert result.error is not None
        assert isinstance(result.error, DripMiddlewareError)
        assert result.error.middleware_code == DripMiddlewareErrorCode.DUPLICATE_CHARGE
        assert result.error.status_code == 409

    @patch("drip.middleware.core.create_drip_client")
    def test_non_duplicate_charge_succeeds_sync(self, mock_create_client: MagicMock) -> None:
        """process_request_sync must allow non-duplicate charges through."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=False)
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        result = process_request_sync(request, config)

        assert result.success is True
        assert result.context is not None
        assert result.context.customer_id == "cus_test_123"
        assert result.context.is_duplicate is False

    @patch("drip.middleware.core.create_drip_client")
    def test_duplicate_charge_calls_on_error(self, mock_create_client: MagicMock) -> None:
        """process_request_sync must call on_error callback for duplicate charges."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=True)
        mock_create_client.return_value = mock_client

        error_callback = MagicMock()
        config = _make_config(on_error=error_callback)
        request = MockRequest(method="POST", url="/api/generate")

        result = process_request_sync(request, config)

        assert result.success is False
        error_callback.assert_called_once()
        call_args = error_callback.call_args
        assert isinstance(call_args[0][0], DripMiddlewareError)
        assert call_args[0][0].middleware_code == DripMiddlewareErrorCode.DUPLICATE_CHARGE

    @patch("drip.middleware.core.create_drip_client")
    def test_non_duplicate_does_not_call_on_error(self, mock_create_client: MagicMock) -> None:
        """process_request_sync must NOT call on_error for non-duplicate charges."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=False)
        mock_create_client.return_value = mock_client

        error_callback = MagicMock()
        config = _make_config(on_error=error_callback)
        request = MockRequest(method="POST", url="/api/generate")

        result = process_request_sync(request, config)

        assert result.success is True
        error_callback.assert_not_called()

    @patch("drip.middleware.core.create_drip_client")
    def test_duplicate_charge_prevents_access_to_context(self, mock_create_client: MagicMock) -> None:
        """Duplicate charges must never produce a usable DripContext (billing bypass prevention)."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=True)
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        result = process_request_sync(request, config)

        # Context must be None so the request is never forwarded to the route handler
        assert result.context is None
        assert result.success is False

    @patch("drip.middleware.core.create_drip_client")
    def test_idempotency_key_none_passed_to_client(self, mock_create_client: MagicMock) -> None:
        """When no custom idempotency_key generator, None is passed to client.charge()."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=False)
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        process_request_sync(request, config)

        # Verify the charge call was made with idempotency_key=None
        mock_client.charge.assert_called_once()
        call_kwargs = mock_client.charge.call_args
        assert call_kwargs.kwargs.get("idempotency_key") is None or call_kwargs[1].get("idempotency_key") is None

    @patch("drip.middleware.core.create_drip_client")
    def test_repeated_identical_requests_each_get_unique_charge(self, mock_create_client: MagicMock) -> None:
        """Each request should pass idempotency_key=None so the client generates unique keys."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=False)
        mock_create_client.return_value = mock_client

        config = _make_config()

        # Simulate two identical requests
        for _ in range(2):
            request = MockRequest(method="POST", url="/api/generate")
            result = process_request_sync(request, config)
            assert result.success is True

        # Both calls should have idempotency_key=None (letting SDK auto-generate)
        assert mock_client.charge.call_count == 2
        for call in mock_client.charge.call_args_list:
            assert call.kwargs.get("idempotency_key") is None


# =============================================================================
# Customer Resolver Input Spoofing Protection
# =============================================================================


class TestCustomerResolverSpoofingProtection:
    @patch("drip.middleware.core.create_drip_client")
    def test_sync_resolver_cannot_read_spoofed_customer_headers(self, mock_create_client: MagicMock) -> None:
        """Spoofable billing identity headers must be stripped before resolver executes."""
        mock_client = MagicMock()
        mock_client.charge.return_value = _make_charge_result(is_duplicate=False)
        mock_create_client.return_value = mock_client

        request = MockRequest(
            method="POST",
            url="/api/generate",
            headers={
                "X-Drip-Customer-Id": "cus_victim_header",
                "X-Customer-Id": "cus_victim_alias",
                "Authorization": "Bearer trusted-token",
            },
        )

        def resolver(req: Any) -> str:
            # Identity headers are blocked from resolver access.
            assert req.headers.get("x-drip-customer-id") is None
            assert req.headers.get("X-Customer-Id") is None
            # Non-identity auth headers remain available and case-insensitive.
            assert req.headers.get("authorization") == "Bearer trusted-token"
            return "cus_authenticated_123"

        config = _make_config(customer_resolver=resolver)
        result = process_request_sync(request, config)

        assert result.success is True
        mock_client.charge.assert_called_once()
        assert mock_client.charge.call_args.kwargs["customer_id"] == "cus_authenticated_123"

    @pytest.mark.asyncio
    @patch("drip.middleware.core.create_async_drip_client")
    async def test_async_resolver_cannot_read_spoofed_customer_query_params(
        self,
        mock_create_client: MagicMock,
    ) -> None:
        """Spoofable customer_id query parameters must be stripped before resolver executes."""
        mock_client = MagicMock()

        async def mock_charge(**_kwargs: Any) -> ChargeResult:
            return _make_charge_result(is_duplicate=False)

        mock_client.charge = mock_charge
        mock_create_client.return_value = mock_client

        request = MockRequest(
            method="POST",
            url="/api/generate?customer_id=cus_victim_query&foo=bar",
            query_params={
                "customer_id": "cus_victim_query",
                "customerId": "cus_victim_alt",
                "foo": "bar",
            },
        )
        # Flask-style args should also be sanitized.
        request.args = dict(request.query_params)

        async def resolver(req: Any) -> str:
            assert req.query_params.get("customer_id") is None
            assert req.query_params.get("customerId") is None
            assert req.query_params.get("foo") == "bar"
            assert req.args.get("customer_id") is None
            assert req.args.get("foo") == "bar"
            return "cus_authenticated_async"

        config = _make_config(customer_resolver=resolver)
        result = await process_request_async(request, config)

        assert result.success is True
        assert result.context is not None
        assert result.context.customer_id == "cus_authenticated_async"


# =============================================================================
# Async Duplicate Charge Rejection Tests
# =============================================================================


class TestAsyncDuplicateChargeRejection:
    @pytest.mark.asyncio
    @patch("drip.middleware.core.create_async_drip_client")
    async def test_duplicate_charge_rejected_async(self, mock_create_client: MagicMock) -> None:
        """process_request_async must reject duplicate charges with 409."""
        from drip.middleware.core import process_request_async

        mock_client = MagicMock()
        mock_client.charge = MagicMock()

        async def mock_charge(**_kwargs: Any) -> ChargeResult:
            return _make_charge_result(is_duplicate=True)

        mock_client.charge = mock_charge
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        result = await process_request_async(request, config)

        assert result.success is False
        assert result.context is None
        assert result.error is not None
        assert isinstance(result.error, DripMiddlewareError)
        assert result.error.middleware_code == DripMiddlewareErrorCode.DUPLICATE_CHARGE
        assert result.error.status_code == 409

    @pytest.mark.asyncio
    @patch("drip.middleware.core.create_async_drip_client")
    async def test_non_duplicate_charge_succeeds_async(self, mock_create_client: MagicMock) -> None:
        """process_request_async must allow non-duplicate charges through."""
        from drip.middleware.core import process_request_async

        mock_client = MagicMock()

        async def mock_charge(**_kwargs: Any) -> ChargeResult:
            return _make_charge_result(is_duplicate=False)

        mock_client.charge = mock_charge
        mock_create_client.return_value = mock_client

        config = _make_config()
        request = MockRequest(method="POST", url="/api/generate")

        result = await process_request_async(request, config)

        assert result.success is True
        assert result.context is not None
        assert result.context.is_duplicate is False
