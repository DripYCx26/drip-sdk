"""Tests for wrap_api_call and cost estimation features."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from drip import (
    AsyncDrip,
    CostEstimateResponse,
    Drip,
    HypotheticalUsageItem,
    RetryOptions,
    WrapApiCallResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def api_key() -> str:
    return "sk_test_1234567890"


@pytest.fixture
def base_url() -> str:
    return "https://api.drippay.dev/v1"


@pytest.fixture
def client(api_key: str, base_url: str) -> Drip:
    return Drip(api_key=api_key, base_url=base_url)


@pytest.fixture
def async_client(api_key: str, base_url: str) -> AsyncDrip:
    return AsyncDrip(api_key=api_key, base_url=base_url)


@pytest.fixture
def mock_charge_result() -> dict[str, Any]:
    return {
        "success": True,
        "usageEventId": "usage_123",
        "isDuplicate": False,
        "charge": {
            "id": "chg_123",
            "amountUsdc": "0.001000",
            "amountToken": "1000000000000000",
            "txHash": "0x123abc",
            "status": "CONFIRMED",
        },
    }


@pytest.fixture
def mock_cost_estimate_response() -> dict[str, Any]:
    return {
        "businessId": "biz_123",
        "customerId": "cus_123",
        "periodStart": "2024-01-01T00:00:00Z",
        "periodEnd": "2024-01-31T23:59:59Z",
        "lineItems": [
            {
                "usageType": "api_calls",
                "quantity": "10000",
                "unitPrice": "0.001",
                "estimatedCostUsdc": "10.00",
                "eventCount": 10000,
                "hasPricingPlan": True,
            },
            {
                "usageType": "tokens",
                "quantity": "1000000",
                "unitPrice": "0.0001",
                "estimatedCostUsdc": "100.00",
                "eventCount": 500,
                "hasPricingPlan": True,
            },
        ],
        "subtotalUsdc": "110.00",
        "estimatedTotalUsdc": "110.00",
        "currency": "USDC",
        "isEstimate": True,
        "generatedAt": "2024-01-15T12:00:00Z",
    }


# =============================================================================
# wrap_api_call Tests (Sync)
# =============================================================================


class TestWrapApiCall:
    """Tests for synchronous wrap_api_call method."""

    @respx.mock
    def test_wraps_external_call_and_records_usage(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should call external API and record usage."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        # Mock external API call
        external_response = {"data": "response", "usage": {"total_tokens": 150}}
        external_call = MagicMock(return_value=external_response)

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=external_call,
            extract_usage=lambda r: r["usage"]["total_tokens"],
        )

        # External API was called once
        external_call.assert_called_once()

        # Result contains both API result and charge
        assert result.result == external_response
        assert result.charge.success is True
        assert result.charge.charge.amount_usdc == "0.001000"

    @respx.mock
    def test_generates_unique_idempotency_key(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should generate unique idempotency key if not provided."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"tokens": 100},
            extract_usage=lambda r: r["tokens"],
        )

        # Should have a generated idempotency key
        assert result.idempotency_key.startswith("wrap_")
        assert len(result.idempotency_key) > 10

    @respx.mock
    def test_uses_custom_idempotency_key(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should use custom idempotency key if provided."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"tokens": 100},
            extract_usage=lambda r: r["tokens"],
            idempotency_key="my_custom_key_123",
        )

        assert result.idempotency_key == "my_custom_key_123"

    @respx.mock
    def test_passes_metadata_to_charge(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should pass metadata to the charge call."""
        route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"tokens": 100},
            extract_usage=lambda r: r["tokens"],
            metadata={"model": "gpt-4", "prompt_id": "abc"},
        )

        # Check that metadata was passed
        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["metadata"] == {"model": "gpt-4", "prompt_id": "abc"}

    def test_raises_on_external_api_failure(self, client: Drip) -> None:
        """Should raise if external API call fails (no retry)."""

        def failing_call() -> dict[str, Any]:
            raise ValueError("OpenAI rate limit exceeded")

        with pytest.raises(ValueError, match="OpenAI rate limit exceeded"):
            client.wrap_api_call(
                customer_id="cus_123",
                meter="tokens",
                call=failing_call,
                extract_usage=lambda _: 0,
            )

    @respx.mock
    def test_handles_zero_usage(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should handle zero usage correctly."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"tokens": 0},
            extract_usage=lambda r: r["tokens"],
        )

        assert result.charge.success is True

    @respx.mock
    def test_handles_complex_usage_extraction(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should handle complex usage extraction logic."""
        route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {
                "usage": {"prompt_tokens": 50, "completion_tokens": 100}
            },
            extract_usage=lambda r: r["usage"]["prompt_tokens"]
            + r["usage"]["completion_tokens"],
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["quantity"] == 150

    @respx.mock
    def test_handles_nullable_usage_with_fallback(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should handle nullable usage with fallback."""
        route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"usage": None},
            extract_usage=lambda r: r["usage"]["total_tokens"]
            if r.get("usage")
            else 0,
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["quantity"] == 0

    @respx.mock
    def test_works_with_openai_like_response(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should work with OpenAI-like response structure."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        openai_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello!"}}
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "total_tokens": 18,
            },
        }

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: openai_response,
            extract_usage=lambda r: r["usage"]["total_tokens"],
            metadata={"model": "gpt-4"},
        )

        assert result.result["choices"][0]["message"]["content"] == "Hello!"

    @respx.mock
    def test_works_with_fixed_cost_api_calls(
        self, client: Drip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should work with fixed-cost API calls."""
        route = respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        result = client.wrap_api_call(
            customer_id="cus_123",
            meter="api_calls",
            call=lambda: {"status": "ok", "data": [1, 2, 3]},
            extract_usage=lambda _: 1,  # Fixed cost: 1 API call
        )

        assert result.result["status"] == "ok"

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["quantity"] == 1
        assert body["usageType"] == "api_calls"


# =============================================================================
# wrap_api_call Tests (Async)
# =============================================================================


class TestAsyncWrapApiCall:
    """Tests for async wrap_api_call method."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_wraps_async_external_call(
        self, async_client: AsyncDrip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should wrap async external API call."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        async def mock_api_call() -> dict[str, Any]:
            return {"data": "response", "usage": {"total_tokens": 150}}

        result = await async_client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=mock_api_call,
            extract_usage=lambda r: r["usage"]["total_tokens"],
        )

        assert result.result["data"] == "response"
        assert result.charge.success is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_handles_sync_callable_in_async_context(
        self, async_client: AsyncDrip, base_url: str, mock_charge_result: dict[str, Any]
    ) -> None:
        """Should handle sync callable in async context."""
        respx.post(f"{base_url}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result)
        )

        # Sync callable (returns value directly)
        result = await async_client.wrap_api_call(
            customer_id="cus_123",
            meter="tokens",
            call=lambda: {"tokens": 100},
            extract_usage=lambda r: r["tokens"],
        )

        assert result.result["tokens"] == 100


# =============================================================================
# Cost Estimation Tests (Sync)
# =============================================================================


class TestEstimateFromUsage:
    """Tests for estimate_from_usage method."""

    @respx.mock
    def test_estimates_from_usage_with_dates(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from usage with datetime objects."""
        respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = client.estimate_from_usage(
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
        )

        assert isinstance(result, CostEstimateResponse)
        assert result.estimated_total_usdc == "110.00"
        assert len(result.line_items) == 2

    @respx.mock
    def test_estimates_from_usage_with_strings(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from usage with ISO strings."""
        respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = client.estimate_from_usage(
            period_start="2024-01-01T00:00:00Z",
            period_end="2024-01-31T23:59:59Z",
        )

        assert result.estimated_total_usdc == "110.00"

    @respx.mock
    def test_estimates_with_custom_pricing(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should support custom pricing overrides."""
        route = respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        client.estimate_from_usage(
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
            custom_pricing={"api_calls": "0.005", "tokens": "0.0001"},
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["customPricing"] == {"api_calls": "0.005", "tokens": "0.0001"}

    @respx.mock
    def test_estimates_with_customer_filter(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should filter by customer ID."""
        route = respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        client.estimate_from_usage(
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
            customer_id="cus_123",
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["customerId"] == "cus_123"

    @respx.mock
    def test_estimates_with_usage_types_filter(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should filter by usage types."""
        route = respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        client.estimate_from_usage(
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
            usage_types=["tokens", "api_calls"],
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["usageTypes"] == ["tokens", "api_calls"]


class TestEstimateFromHypothetical:
    """Tests for estimate_from_hypothetical method."""

    @respx.mock
    def test_estimates_hypothetical_with_models(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from hypothetical usage with Pydantic models."""
        respx.post(f"{base_url}/cost-estimate/hypothetical").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = client.estimate_from_hypothetical(
            items=[
                HypotheticalUsageItem(usage_type="api_calls", quantity=10000),
                HypotheticalUsageItem(usage_type="tokens", quantity=1000000),
            ]
        )

        assert isinstance(result, CostEstimateResponse)
        assert result.estimated_total_usdc == "110.00"

    @respx.mock
    def test_estimates_hypothetical_with_dicts(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from hypothetical usage with dicts."""
        respx.post(f"{base_url}/cost-estimate/hypothetical").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = client.estimate_from_hypothetical(
            items=[
                {"usageType": "api_calls", "quantity": 10000},
                {"usageType": "tokens", "quantity": 1000000},
            ]
        )

        assert result.estimated_total_usdc == "110.00"

    @respx.mock
    def test_estimates_hypothetical_with_custom_pricing(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should support custom pricing overrides."""
        route = respx.post(f"{base_url}/cost-estimate/hypothetical").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        client.estimate_from_hypothetical(
            items=[{"usageType": "api_calls", "quantity": 100000}],
            custom_pricing={"api_calls": "0.0005"},  # 50% discount
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["customPricing"] == {"api_calls": "0.0005"}

    @respx.mock
    def test_estimates_hypothetical_with_unit_price_override(
        self, client: Drip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should support per-item unit price override."""
        route = respx.post(f"{base_url}/cost-estimate/hypothetical").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        client.estimate_from_hypothetical(
            items=[
                HypotheticalUsageItem(
                    usage_type="api_calls",
                    quantity=10000,
                    unit_price_override="0.01",
                )
            ]
        )

        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["items"][0]["unitPriceOverride"] == "0.01"


# =============================================================================
# Cost Estimation Tests (Async)
# =============================================================================


class TestAsyncCostEstimation:
    """Tests for async cost estimation methods."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_estimate_from_usage(
        self, async_client: AsyncDrip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from usage asynchronously."""
        respx.post(f"{base_url}/cost-estimate/from-usage").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = await async_client.estimate_from_usage(
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
        )

        assert result.estimated_total_usdc == "110.00"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_estimate_from_hypothetical(
        self, async_client: AsyncDrip, base_url: str, mock_cost_estimate_response: dict[str, Any]
    ) -> None:
        """Should estimate costs from hypothetical usage asynchronously."""
        respx.post(f"{base_url}/cost-estimate/hypothetical").mock(
            return_value=httpx.Response(200, json=mock_cost_estimate_response)
        )

        result = await async_client.estimate_from_hypothetical(
            items=[
                {"usageType": "api_calls", "quantity": 10000},
                {"usageType": "tokens", "quantity": 1000000},
            ]
        )

        assert result.estimated_total_usdc == "110.00"


# =============================================================================
# Model Tests
# =============================================================================


class TestModels:
    """Tests for new Pydantic models."""

    def test_retry_options_defaults(self) -> None:
        """RetryOptions should have sensible defaults."""
        options = RetryOptions()
        assert options.max_attempts == 3
        assert options.base_delay_ms == 100
        assert options.max_delay_ms == 5000

    def test_retry_options_custom(self) -> None:
        """RetryOptions should accept custom values."""
        options = RetryOptions(
            max_attempts=5,
            base_delay_ms=200,
            max_delay_ms=10000,
        )
        assert options.max_attempts == 5
        assert options.base_delay_ms == 200
        assert options.max_delay_ms == 10000

    def test_hypothetical_usage_item(self) -> None:
        """HypotheticalUsageItem should work correctly."""
        item = HypotheticalUsageItem(usage_type="tokens", quantity=1000)
        assert item.usage_type == "tokens"
        assert item.quantity == 1000
        assert item.unit_price_override is None

        # Test with alias
        data = item.model_dump(by_alias=True)
        assert data["usageType"] == "tokens"

    def test_wrap_api_call_result(self, mock_charge_result: dict[str, Any]) -> None:
        """WrapApiCallResult should work correctly."""
        from drip import ChargeResult

        charge = ChargeResult.model_validate(mock_charge_result)
        result = WrapApiCallResult(
            result={"data": "test"},
            charge=charge,
            idempotency_key="wrap_123",
        )

        assert result.result == {"data": "test"}
        assert result.charge.success is True
        assert result.idempotency_key == "wrap_123"

    def test_cost_estimate_response(self, mock_cost_estimate_response: dict[str, Any]) -> None:
        """CostEstimateResponse should parse correctly."""
        response = CostEstimateResponse.model_validate(mock_cost_estimate_response)

        assert response.business_id == "biz_123"
        assert response.estimated_total_usdc == "110.00"
        assert len(response.line_items) == 2
        assert response.line_items[0].usage_type == "api_calls"
        assert response.line_items[0].has_pricing_plan is True
        assert response.currency == "USDC"
        assert response.is_estimate is True
