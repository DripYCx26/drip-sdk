"""Tests for the Drip client."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
import respx

from drip import (
    AsyncDrip,
    ChargeStatus,
    Drip,
    DripAPIError,
    DripAuthenticationError,
    DripNetworkError,
    DripPaymentRequiredError,
    DripRateLimitError,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def api_key() -> str:
    return "drip_sk_test_123"


@pytest.fixture
def base_url() -> str:
    return "https://api.drippay.dev/v1"


@pytest.fixture
def client(api_key: str, base_url: str) -> Drip:
    return Drip(api_key=api_key, base_url=base_url)


@pytest.fixture
def async_client(api_key: str, base_url: str) -> AsyncDrip:
    return AsyncDrip(api_key=api_key, base_url=base_url)


# =============================================================================
# Client Initialization Tests
# =============================================================================


class TestClientInitialization:
    def test_client_requires_api_key(self) -> None:
        """Client should raise error if no API key provided."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove DRIP_API_KEY if present
            os.environ.pop("DRIP_API_KEY", None)
            with pytest.raises(DripAuthenticationError):
                Drip()

    def test_client_accepts_api_key_param(self, api_key: str) -> None:
        """Client should accept API key as parameter."""
        client = Drip(api_key=api_key)
        assert client.config.api_key == api_key

    def test_client_reads_api_key_from_env(self) -> None:
        """Client should read API key from environment."""
        with patch.dict(os.environ, {"DRIP_API_KEY": "env_key_123"}):
            client = Drip()
            assert client.config.api_key == "env_key_123"

    def test_client_default_base_url(self, api_key: str) -> None:
        """Client should use default base URL."""
        client = Drip(api_key=api_key)
        assert client.config.base_url == "https://api.drippay.dev/v1"

    def test_client_custom_base_url(self, api_key: str) -> None:
        """Client should accept custom base URL."""
        client = Drip(api_key=api_key, base_url="https://custom.api.com")
        assert client.config.base_url == "https://custom.api.com"

    def test_client_context_manager(self, api_key: str) -> None:
        """Client should work as context manager."""
        with Drip(api_key=api_key) as client:
            assert client.config.api_key == api_key


class TestAsyncClientInitialization:
    def test_async_client_requires_api_key(self) -> None:
        """Async client should raise error if no API key provided."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DRIP_API_KEY", None)
            with pytest.raises(DripAuthenticationError):
                AsyncDrip()

    @pytest.mark.asyncio
    async def test_async_client_context_manager(self, api_key: str) -> None:
        """Async client should work as async context manager."""
        async with AsyncDrip(api_key=api_key) as client:
            assert client.config.api_key == api_key


# =============================================================================
# Customer API Tests
# =============================================================================


class TestCustomerAPI:
    @respx.mock
    def test_create_customer(self, client: Drip, base_url: str) -> None:
        """Should create a customer."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cus_123",
                    "businessId": "biz_456",
                    "externalCustomerId": "ext_789",
                    "onchainAddress": "0x1234567890abcdef",
                    "metadata": {"key": "value"},
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z",
                },
            )
        )

        customer = client.create_customer(
            onchain_address="0x1234567890abcdef",
            external_customer_id="ext_789",
            metadata={"key": "value"},
        )

        assert customer.id == "cus_123"
        assert customer.onchain_address == "0x1234567890abcdef"
        assert customer.external_customer_id == "ext_789"

    @respx.mock
    def test_get_customer(self, client: Drip, base_url: str) -> None:
        """Should get a customer by ID."""
        respx.get(f"{base_url}/customers/cus_123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cus_123",
                    "businessId": "biz_456",
                    "externalCustomerId": None,
                    "onchainAddress": "0x1234",
                    "metadata": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z",
                },
            )
        )

        customer = client.get_customer("cus_123")
        assert customer.id == "cus_123"

    @respx.mock
    def test_list_customers(self, client: Drip, base_url: str) -> None:
        """Should list customers."""
        respx.get(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "cus_1",
                            "businessId": "biz_1",
                            "externalCustomerId": None,
                            "onchainAddress": "0x1",
                            "metadata": None,
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        },
                        {
                            "id": "cus_2",
                            "businessId": "biz_1",
                            "externalCustomerId": None,
                            "onchainAddress": "0x2",
                            "metadata": None,
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        },
                    ],
                    "count": 2,
                },
            )
        )

        result = client.list_customers()
        assert result.count == 2
        assert len(result.data) == 2

    @respx.mock
    def test_get_balance(self, client: Drip, base_url: str) -> None:
        """Should get customer balance."""
        respx.get(f"{base_url}/customers/cus_123/balance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "customerId": "cus_123",
                    "onchainAddress": "0x1234567890abcdef1234567890abcdef12345678",
                    "balanceUsdc": "1000000",
                    "pendingChargesUsdc": "0",
                    "availableUsdc": "1000000",
                    "lastSyncedAt": "2024-01-01T00:00:00Z",
                },
            )
        )

        balance = client.get_balance("cus_123")
        assert balance.customer_id == "cus_123"
        assert balance.balance_usdc == "1000000"
        assert balance.available_usdc == "1000000"



# =============================================================================
# Charge API Tests
# =============================================================================


class TestChargeAPI:
    @respx.mock
    def test_charge(self, client: Drip, base_url: str) -> None:
        """Should create a charge."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "usage_123",
                    "isDuplicate": False,
                    "charge": {
                        "id": "chg_123",
                        "amountUsdc": "100",
                        "amountToken": "100000000000000",
                        "txHash": "0xabc",
                        "status": "CONFIRMED",
                    },
                },
            )
        )

        result = client.charge(
            customer_id="cus_123",
            meter="api_calls",
            quantity=1,
        )

        assert result.success is True
        assert result.charge.id == "chg_123"
        assert result.charge.status == ChargeStatus.CONFIRMED

    @respx.mock
    def test_charge_with_idempotency(self, client: Drip, base_url: str) -> None:
        """Should create a charge with idempotency key."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "usage_123",
                    "isDuplicate": True,
                    "charge": {
                        "id": "chg_123",
                        "amountUsdc": "100",
                        "amountToken": "100000000000000",
                        "txHash": "0xabc",
                        "status": "CONFIRMED",
                    },
                },
            )
        )

        result = client.charge(
            customer_id="cus_123",
            meter="api_calls",
            quantity=1,
            idempotency_key="idem_123",
        )

        assert result.is_duplicate is True

    @respx.mock
    def test_get_charge(self, client: Drip, base_url: str) -> None:
        """Should get charge details."""
        respx.get(f"{base_url}/charges/chg_123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chg_123",
                    "usageId": "usage_123",
                    "customerId": "cus_123",
                    "customer": {
                        "id": "cus_123",
                        "onchainAddress": "0x123",
                        "externalCustomerId": None,
                    },
                    "usageEvent": {
                        "id": "usage_123",
                        "type": "api_calls",
                        "quantity": "1",
                        "metadata": None,
                    },
                    "amountUsdc": "100",
                    "amountToken": "100000000000000",
                    "txHash": "0xabc",
                    "blockNumber": "12345",
                    "status": "CONFIRMED",
                    "failureReason": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "confirmedAt": "2024-01-01T00:00:01Z",
                },
            )
        )

        charge = client.get_charge("chg_123")
        assert charge.id == "chg_123"
        assert charge.customer.id == "cus_123"


# =============================================================================
# Webhook API Tests
# =============================================================================


class TestWebhookAPI:
    @respx.mock
    def test_create_webhook(self, client: Drip, base_url: str) -> None:
        """Should create a webhook."""
        respx.post(f"{base_url}/webhooks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "wh_123",
                    "url": "https://example.com/webhook",
                    "events": ["charge.succeeded"],
                    "description": "Test webhook",
                    "isActive": True,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z",
                    "secret": "whsec_abc123",
                    "message": "Webhook created",
                },
            )
        )

        result = client.create_webhook(
            url="https://example.com/webhook",
            events=["charge.succeeded"],
            description="Test webhook",
        )

        assert result.id == "wh_123"
        assert result.secret == "whsec_abc123"

    @respx.mock
    def test_list_webhooks(self, client: Drip, base_url: str) -> None:
        """Should list webhooks."""
        respx.get(f"{base_url}/webhooks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "wh_1",
                            "url": "https://example.com/wh1",
                            "events": ["charge.succeeded"],
                            "description": None,
                            "isActive": True,
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "count": 1,
                },
            )
        )

        result = client.list_webhooks()
        assert result.count == 1


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    @respx.mock
    def test_authentication_error(self, client: Drip, base_url: str) -> None:
        """Should raise DripAuthenticationError on 401."""
        respx.get(f"{base_url}/customers/cus_123").mock(
            return_value=httpx.Response(
                401,
                json={"message": "Invalid API key"},
            )
        )

        with pytest.raises(DripAuthenticationError):
            client.get_customer("cus_123")

    @respx.mock
    def test_payment_required_error(self, client: Drip, base_url: str) -> None:
        """Should raise DripPaymentRequiredError on 402."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                402,
                json={
                    "message": "Insufficient balance",
                    "paymentRequest": {
                        "amount": "1000",
                        "recipient": "0x123",
                    },
                },
            )
        )

        with pytest.raises(DripPaymentRequiredError) as exc_info:
            client.charge(customer_id="cus_123", meter="api_calls", quantity=1)

        assert exc_info.value.payment_request is not None

    @respx.mock
    def test_rate_limit_error(self, client: Drip, base_url: str) -> None:
        """Should raise DripRateLimitError on 429."""
        respx.get(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                429,
                json={"message": "Too many requests", "retryAfter": 60},
            )
        )

        with pytest.raises(DripRateLimitError) as exc_info:
            client.list_customers()

        assert exc_info.value.retry_after == 60

    @respx.mock
    def test_api_error(self, client: Drip, base_url: str) -> None:
        """Should raise DripAPIError on 4xx/5xx."""
        respx.get(f"{base_url}/customers/invalid").mock(
            return_value=httpx.Response(
                404,
                json={"message": "Customer not found", "code": "NOT_FOUND"},
            )
        )

        with pytest.raises(DripAPIError) as exc_info:
            client.get_customer("invalid")

        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "NOT_FOUND"

    @respx.mock
    def test_network_error(self, client: Drip, base_url: str) -> None:
        """Should raise DripNetworkError on connection issues."""
        respx.get(f"{base_url}/customers").mock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(DripNetworkError):
            client.list_customers()


# =============================================================================
# Async Client Tests
# =============================================================================


class TestAsyncClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_async_create_customer(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should create customer asynchronously."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cus_123",
                    "businessId": "biz_456",
                    "externalCustomerId": None,
                    "onchainAddress": "0x123",
                    "metadata": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z",
                },
            )
        )

        customer = await async_client.create_customer(onchain_address="0x123")
        assert customer.id == "cus_123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_charge(self, async_client: AsyncDrip, base_url: str) -> None:
        """Should create charge asynchronously."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "usage_123",
                    "isDuplicate": False,
                    "charge": {
                        "id": "chg_123",
                        "amountUsdc": "100",
                        "amountToken": "100000000000000",
                        "txHash": "0xabc",
                        "status": "CONFIRMED",
                    },
                },
            )
        )

        result = await async_client.charge(
            customer_id="cus_123",
            meter="api_calls",
            quantity=1,
        )

        assert result.success is True


# =============================================================================
# Utility Method Tests
# =============================================================================


class TestUtilityMethods:
    def test_generate_idempotency_key(self) -> None:
        """Should generate deterministic idempotency keys."""
        key1 = Drip.generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_456",
        )
        key2 = Drip.generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_456",
        )
        key3 = Drip.generate_idempotency_key(
            customer_id="cus_123",
            step_name="different",
            run_id="run_456",
        )

        # Same inputs should produce same key
        assert key1 == key2
        # Different inputs should produce different key
        assert key1 != key3

    def test_verify_webhook_signature_valid(self) -> None:
        """Should verify valid webhook signatures using t=timestamp,v1=signature format."""
        from drip.utils import generate_webhook_signature

        secret = "whsec_test123"
        payload = '{"event": "charge.succeeded"}'
        # Use generate_webhook_signature to create proper format
        signature = generate_webhook_signature(payload, secret)

        assert Drip.verify_webhook_signature(payload, signature, secret) is True

    def test_verify_webhook_signature_invalid(self) -> None:
        """Should reject invalid webhook signatures."""
        secret = "whsec_test123"
        payload = '{"event": "charge.succeeded"}'
        signature = "t=123,v1=invalid"

        assert Drip.verify_webhook_signature(payload, signature, secret) is False

    def test_verify_webhook_signature_empty(self) -> None:
        """Should handle empty values."""
        assert Drip.verify_webhook_signature("", "sig", "secret") is False
        assert Drip.verify_webhook_signature("payload", "", "secret") is False
        assert Drip.verify_webhook_signature("payload", "sig", "") is False


# =============================================================================
# Resilience Integration Tests
# =============================================================================


class TestClientResilience:
    """Tests for resilience integration in clients."""

    def test_drip_with_resilience_enabled(self, api_key: str, base_url: str) -> None:
        """Client should work with resilience=True."""
        client = Drip(api_key=api_key, base_url=base_url, resilience=True)
        assert client.resilience is not None
        assert client.get_health() is not None
        assert client.get_metrics() is not None

    def test_drip_without_resilience(self, api_key: str, base_url: str) -> None:
        """Client should work without resilience (default)."""
        client = Drip(api_key=api_key, base_url=base_url)
        assert client.resilience is None
        assert client.get_health() is None
        assert client.get_metrics() is None

    def test_async_drip_with_resilience_enabled(
        self, api_key: str, base_url: str
    ) -> None:
        """Async client should work with resilience=True."""
        client = AsyncDrip(api_key=api_key, base_url=base_url, resilience=True)
        assert client.resilience is not None
        assert client.get_health() is not None
        assert client.get_metrics() is not None

    def test_async_drip_without_resilience(self, api_key: str, base_url: str) -> None:
        """Async client should work without resilience (default)."""
        client = AsyncDrip(api_key=api_key, base_url=base_url)
        assert client.resilience is None
        assert client.get_health() is None
        assert client.get_metrics() is None

    @respx.mock
    def test_resilience_collects_metrics(
        self, api_key: str, base_url: str
    ) -> None:
        """Resilient client should collect metrics on requests."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "u123",
                    "isDuplicate": False,
                    "charge": {
                        "id": "chg_123",
                        "amountUsdc": "100",
                        "amountToken": "100000000000000",
                        "txHash": "0xabc",
                        "status": "CONFIRMED",
                    },
                },
            )
        )

        client = Drip(api_key=api_key, base_url=base_url, resilience=True)
        client.charge(customer_id="cus_123", meter="api_calls", quantity=1)

        metrics = client.get_metrics()
        assert metrics is not None
        assert metrics["total_requests"] == 1
        assert metrics["total_successes"] == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_resilience_collects_metrics(
        self, api_key: str, base_url: str
    ) -> None:
        """Async resilient client should collect metrics on requests."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "u123",
                    "isDuplicate": False,
                    "charge": {
                        "id": "chg_123",
                        "amountUsdc": "100",
                        "amountToken": "100000000000000",
                        "txHash": "0xabc",
                        "status": "CONFIRMED",
                    },
                },
            )
        )

        async with AsyncDrip(
            api_key=api_key, base_url=base_url, resilience=True
        ) as client:
            await client.charge(customer_id="cus_123", meter="api_calls", quantity=1)

            metrics = client.get_metrics()
            assert metrics is not None
            assert metrics["total_requests"] == 1
            assert metrics["total_successes"] == 1


# =============================================================================
# Customer Resolution & user= DX Tests
# =============================================================================

_CUSTOMER_RESPONSE = {
    "id": "cus_resolved",
    "businessId": "biz_1",
    "externalCustomerId": "my_user",
    "onchainAddress": "0xabc",
    "metadata": None,
    "createdAt": "2024-01-01T00:00:00Z",
    "updatedAt": "2024-01-01T00:00:00Z",
}

_CHARGE_RESPONSE = {
    "success": True,
    "usageEventId": "u1",
    "isDuplicate": False,
    "charge": {
        "id": "chg_1",
        "amountUsdc": "0.01",
        "amountToken": "10000000000000",
        "txHash": "0xdef",
        "status": "CONFIRMED",
    },
}


class TestUserParam:
    """Tests for the user= keyword argument on charge/track_usage/wrap_api_call."""

    @respx.mock
    def test_charge_with_user_creates_customer(self, client: Drip, base_url: str) -> None:
        """charge(user=...) should auto-create customer then charge."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        result = client.charge(user="my_user", meter="api_calls", quantity=1)
        assert result.charge.id == "chg_1"

    @respx.mock
    def test_charge_with_user_caches(self, client: Drip, base_url: str) -> None:
        """Second charge(user=...) should hit cache, not re-create."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        client.charge(user="my_user", meter="api_calls", quantity=1)
        client.charge(user="my_user", meter="api_calls", quantity=2)

        # Only one customer create call, two charges
        assert route.call_count == 2

    @respx.mock
    def test_charge_with_user_handles_409(self, client: Drip, base_url: str) -> None:
        """charge(user=...) with existing customer should resolve via 409."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                409,
                json={
                    "error": "Customer already exists",
                    "code": "DUPLICATE_CUSTOMER",
                    "existingCustomerId": "cus_existing",
                },
            )
        )
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        result = client.charge(user="my_user", meter="api_calls", quantity=1)
        assert result.charge.id == "chg_1"

    @respx.mock
    def test_charge_with_user_includes_resolved_id_in_idempotency_key(
        self, client: Drip, base_url: str
    ) -> None:
        """charge(user=...) must include resolved customer ID in the idempotency key.

        Regression test: previously the sync charge() passed the original
        customer_id (None) instead of resolved_id into the key generator,
        causing cross-user key collisions.
        """
        # Set up two different users resolving to different customer IDs.
        customer_a = {**_CUSTOMER_RESPONSE, "id": "cus_aaa", "externalCustomerId": "user_a"}
        customer_b = {**_CUSTOMER_RESPONSE, "id": "cus_bbb", "externalCustomerId": "user_b"}

        usage_route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        # Reset the module counter so both users start from the same sequence.
        import drip.client as _mod
        from drip.client import _call_counter_lock

        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=customer_a)
        )
        with _call_counter_lock:
            _mod._call_counter = 0
        client._customer_cache.clear()
        client.charge(user="user_a", meter="tokens", quantity=10)

        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=customer_b)
        )
        with _call_counter_lock:
            _mod._call_counter = 0
        client._customer_cache.clear()
        client.charge(user="user_b", meter="tokens", quantity=10)

        # Extract the idempotency keys sent to the server.
        import json as _json

        key_a = _json.loads(usage_route.calls[0].request.content)["idempotencyKey"]
        key_b = _json.loads(usage_route.calls[1].request.content)["idempotencyKey"]
        assert key_a != key_b, (
            "Different users with identical meter/quantity must produce different idempotency keys"
        )

    @respx.mock
    def test_charge_requires_user_or_customer_id(self, client: Drip) -> None:
        """charge() with neither user nor customer_id should error."""
        from drip.errors import DripError

        with pytest.raises(DripError, match="Either 'customer_id' or 'user' is required"):
            client.charge(meter="api_calls", quantity=1)

    @respx.mock
    def test_track_usage_with_user(self, client: Drip, base_url: str) -> None:
        """track_usage(user=...) should auto-resolve customer."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.post(f"{base_url}/usage/internal").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "track_1",
                    "isDuplicate": False,
                    "customerId": "cus_resolved",
                    "usageType": "api_calls",
                    "quantity": 1,
                    "isInternal": True,
                    "message": "Usage tracked",
                },
            )
        )

        result = client.track_usage(user="my_user", meter="api_calls", quantity=1)
        assert result.success is True
        assert result.usage_event_id == "track_1"

    @respx.mock
    def test_track_usage_defaults_to_sync_mode(self, client: Drip, base_url: str) -> None:
        """track_usage() should keep legacy sync semantics by default."""
        usage_route = respx.post(f"{base_url}/usage/internal").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "usageEventId": "track_sync_1",
                    "customerId": "cus_123",
                    "usageType": "api_calls",
                    "quantity": 1,
                    "isInternal": True,
                    "message": "Usage tracked",
                },
            )
        )

        result = client.track_usage(customer_id="cus_123", meter="api_calls", quantity=1)

        assert usage_route.called
        assert result.usage_event_id == "track_sync_1"

    @respx.mock
    def test_track_usage_batch_mode_is_explicit(self, client: Drip, base_url: str) -> None:
        """track_usage(mode='batch') should use the batched endpoint."""
        batch_route = respx.post(f"{base_url}/usage/internal/batch").mock(
            return_value=httpx.Response(
                202,
                json={
                    "success": True,
                    "customerId": "cus_123",
                    "usageType": "api_calls",
                    "quantity": 1,
                    "idempotencyKey": "track_batch_1",
                    "pendingEvents": 4,
                    "message": "Event queued for batched insert (~2s)",
                },
            )
        )

        result = client.track_usage(
            customer_id="cus_123",
            meter="api_calls",
            quantity=1,
            mode="batch",
        )

        assert batch_route.called
        assert result.idempotency_key == "track_batch_1"
        assert result.pending_events == 4

    @respx.mock
    def test_wrap_api_call_with_user(self, client: Drip, base_url: str) -> None:
        """wrap_api_call(user=...) should auto-resolve customer."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        result = client.wrap_api_call(
            user="my_user",
            meter="api_calls",
            call=lambda: {"data": "ok"},
            extract_usage=lambda _r: 5,
        )
        assert result.result == {"data": "ok"}
        assert result.charge.charge.id == "chg_1"


class TestGetOrCreateCustomer:
    """Tests for get_or_create_customer."""

    @respx.mock
    def test_creates_new(self, client: Drip, base_url: str) -> None:
        """get_or_create_customer should create when new."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )

        customer = client.get_or_create_customer("my_user")
        assert customer.id == "cus_resolved"

    @respx.mock
    def test_returns_existing(self, client: Drip, base_url: str) -> None:
        """get_or_create_customer should return existing on 409."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(
                409,
                json={
                    "error": "exists",
                    "code": "DUPLICATE_CUSTOMER",
                    "existingCustomerId": "cus_existing",
                },
            )
        )
        respx.get(f"{base_url}/customers/cus_existing").mock(
            return_value=httpx.Response(
                200,
                json={**_CUSTOMER_RESPONSE, "id": "cus_existing"},
            )
        )

        customer = client.get_or_create_customer("my_user")
        assert customer.id == "cus_existing"


class TestRunContextManager:
    """Tests for the run() context manager."""

    @respx.mock
    def test_run_completes(self, client: Drip, base_url: str) -> None:
        """run() should auto-complete on success."""
        # Mock customer creation
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        # Mock workflow list (empty => creates new)
        respx.get(f"{base_url}/workflows").mock(
            return_value=httpx.Response(200, json={"data": [], "count": 0})
        )
        # Mock workflow creation
        respx.post(f"{base_url}/workflows").mock(
            return_value=httpx.Response(
                200, json={"id": "wf_1", "name": "Test", "slug": "test"}
            )
        )
        # Mock start run
        respx.post(f"{base_url}/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "RUNNING",
                    "workflowId": "wf_1",
                    "customerId": "cus_resolved",
                    "workflowName": "Test",
                    "correlationId": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            )
        )
        # Mock emit event
        respx.post(f"{base_url}/run-events").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "evt_1",
                    "isDuplicate": False,
                    "runId": "run_1",
                    "eventType": "step.done",
                    "quantity": 1,
                    "costUnits": "0",
                    "timestamp": "2024-01-01T00:00:30Z",
                },
            )
        )
        # Mock end run (COMPLETED)
        end_route = respx.patch(f"{base_url}/runs/run_1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "COMPLETED",
                    "durationMs": 100,
                    "endedAt": "2024-01-01T00:01:00Z",
                    "eventCount": 1,
                    "totalCostUnits": "0",
                },
            )
        )

        with client.run(user="my_user", workflow="test") as run:
            run.event("step.done", 1)

        # Verify end_run was called with COMPLETED
        assert end_route.called
        last_req = end_route.calls.last.request
        import json

        body = json.loads(last_req.content)
        assert body["status"] == "COMPLETED"

    @respx.mock
    def test_run_fails_on_exception(self, client: Drip, base_url: str) -> None:
        """run() should auto-fail on exception."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.get(f"{base_url}/workflows").mock(
            return_value=httpx.Response(200, json={"data": [], "count": 0})
        )
        respx.post(f"{base_url}/workflows").mock(
            return_value=httpx.Response(
                200, json={"id": "wf_1", "name": "Test", "slug": "test"}
            )
        )
        respx.post(f"{base_url}/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "RUNNING",
                    "workflowId": "wf_1",
                    "customerId": "cus_resolved",
                    "workflowName": "Test",
                    "correlationId": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            )
        )
        end_route = respx.patch(f"{base_url}/runs/run_1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "FAILED",
                    "durationMs": 50,
                    "endedAt": "2024-01-01T00:01:00Z",
                    "eventCount": 0,
                    "totalCostUnits": "0",
                },
            )
        )

        with pytest.raises(ValueError, match="boom"), client.run(user="my_user", workflow="test"):
            raise ValueError("boom")

        # Verify end_run was called with FAILED
        assert end_route.called
        import json

        body = json.loads(end_route.calls.last.request.content)
        assert body["status"] == "FAILED"
        assert body["errorMessage"] == "boom"


class TestAsyncUserParam:
    """Tests for user= on AsyncDrip."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_charge_with_user(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Async charge(user=...) should auto-create customer then charge."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=_CHARGE_RESPONSE)
        )

        result = await async_client.charge(user="my_user", meter="api_calls", quantity=1)
        assert result.charge.id == "chg_1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_get_or_create(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Async get_or_create_customer should work."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )

        customer = await async_client.get_or_create_customer("my_user")
        assert customer.id == "cus_resolved"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_run_context(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Async run() context manager should auto-complete."""
        respx.post(f"{base_url}/customers").mock(
            return_value=httpx.Response(200, json=_CUSTOMER_RESPONSE)
        )
        respx.get(f"{base_url}/workflows").mock(
            return_value=httpx.Response(200, json={"data": [], "count": 0})
        )
        respx.post(f"{base_url}/workflows").mock(
            return_value=httpx.Response(
                200, json={"id": "wf_1", "name": "Test", "slug": "test"}
            )
        )
        respx.post(f"{base_url}/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "RUNNING",
                    "workflowId": "wf_1",
                    "customerId": "cus_resolved",
                    "workflowName": "Test",
                    "correlationId": None,
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            )
        )
        respx.post(f"{base_url}/run-events").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "evt_1",
                    "isDuplicate": False,
                    "runId": "run_1",
                    "eventType": "step.done",
                    "quantity": 1,
                    "costUnits": "0",
                    "timestamp": "2024-01-01T00:00:30Z",
                },
            )
        )
        end_route = respx.patch(f"{base_url}/runs/run_1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "run_1",
                    "status": "COMPLETED",
                    "durationMs": 100,
                    "endedAt": "2024-01-01T00:01:00Z",
                    "eventCount": 1,
                    "totalCostUnits": "0",
                },
            )
        )

        async with async_client.run(user="my_user", workflow="test") as run:
            await run.event("step.done", 1)

        assert end_route.called
