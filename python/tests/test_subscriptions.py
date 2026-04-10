"""Tests for the Drip subscription billing API."""

from __future__ import annotations

import httpx
import pytest
import respx

from drip import (
    AsyncDrip,
    Drip,
    ListSubscriptionsResponse,
    Subscription,
    SubscriptionInterval,
    SubscriptionStatus,
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


MOCK_SUBSCRIPTION_JSON = {
    "id": "sub_abc123",
    "businessId": "biz_456",
    "customerId": "cust_789",
    "name": "Pro Plan",
    "amountUsdc": "29.99",
    "interval": "MONTHLY",
    "currentPeriod": 1,
    "nextChargeAt": "2024-02-01T00:00:00Z",
    "lastChargedAt": "2024-01-01T00:00:00Z",
    "startDate": "2024-01-01T00:00:00Z",
    "endDate": None,
    "status": "ACTIVE",
    "cancelledAt": None,
    "createdAt": "2024-01-01T00:00:00Z",
    "updatedAt": "2024-01-01T00:00:00Z",
    "metadata": None,
}


# =============================================================================
# Subscription API Tests (Sync)
# =============================================================================


class TestCreateSubscription:
    @respx.mock
    def test_create_subscription(self, client: Drip, base_url: str) -> None:
        """Should create a subscription with correct POST request."""
        respx.post(f"{base_url}/subscriptions").mock(
            return_value=httpx.Response(200, json=MOCK_SUBSCRIPTION_JSON)
        )

        sub = client.create_subscription(
            customer_id="cust_789",
            name="Pro Plan",
            amount_usdc="29.99",
            interval="MONTHLY",
        )

        assert sub.id == "sub_abc123"
        assert sub.customer_id == "cust_789"
        assert sub.name == "Pro Plan"
        assert sub.amount_usdc == "29.99"
        assert sub.interval == SubscriptionInterval.MONTHLY
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.current_period == 1

    @respx.mock
    def test_create_subscription_with_optional_fields(
        self, client: Drip, base_url: str
    ) -> None:
        """Should create a subscription with optional start_date, end_date, and metadata."""
        mock_json = {
            **MOCK_SUBSCRIPTION_JSON,
            "startDate": "2024-06-01T00:00:00Z",
            "endDate": "2025-06-01T00:00:00Z",
            "metadata": {"tier": "enterprise"},
        }
        respx.post(f"{base_url}/subscriptions").mock(
            return_value=httpx.Response(200, json=mock_json)
        )

        sub = client.create_subscription(
            customer_id="cust_789",
            name="Pro Plan",
            amount_usdc="29.99",
            interval="MONTHLY",
            start_date="2024-06-01T00:00:00Z",
            end_date="2025-06-01T00:00:00Z",
            metadata={"tier": "enterprise"},
        )

        assert sub.start_date == "2024-06-01T00:00:00Z"
        assert sub.end_date == "2025-06-01T00:00:00Z"
        assert sub.metadata == {"tier": "enterprise"}


class TestGetSubscription:
    @respx.mock
    def test_get_subscription(self, client: Drip, base_url: str) -> None:
        """Should get a subscription by ID with correct GET request."""
        respx.get(f"{base_url}/subscriptions/sub_abc123").mock(
            return_value=httpx.Response(200, json=MOCK_SUBSCRIPTION_JSON)
        )

        sub = client.get_subscription("sub_abc123")

        assert sub.id == "sub_abc123"
        assert sub.name == "Pro Plan"
        assert sub.customer_id == "cust_789"
        assert sub.next_charge_at == "2024-02-01T00:00:00Z"


class TestListSubscriptions:
    @respx.mock
    def test_list_subscriptions_no_filters(self, client: Drip, base_url: str) -> None:
        """Should list subscriptions with no filters."""
        respx.get(f"{base_url}/subscriptions").mock(
            return_value=httpx.Response(
                200,
                json={"data": [MOCK_SUBSCRIPTION_JSON]},
            )
        )

        result = client.list_subscriptions()

        assert len(result.data) == 1
        assert result.data[0].id == "sub_abc123"

    @respx.mock
    def test_list_subscriptions_with_customer_id(
        self, client: Drip, base_url: str
    ) -> None:
        """Should list subscriptions filtered by customer ID."""
        respx.get(f"{base_url}/subscriptions?customerId=cust_789").mock(
            return_value=httpx.Response(
                200,
                json={"data": [MOCK_SUBSCRIPTION_JSON]},
            )
        )

        result = client.list_subscriptions(customer_id="cust_789")

        assert len(result.data) == 1
        assert result.data[0].customer_id == "cust_789"

    @respx.mock
    def test_list_subscriptions_with_status(
        self, client: Drip, base_url: str
    ) -> None:
        """Should list subscriptions filtered by status."""
        respx.get(f"{base_url}/subscriptions?status=ACTIVE").mock(
            return_value=httpx.Response(
                200,
                json={"data": [MOCK_SUBSCRIPTION_JSON]},
            )
        )

        result = client.list_subscriptions(status="ACTIVE")

        assert len(result.data) == 1
        assert result.data[0].status == SubscriptionStatus.ACTIVE

    @respx.mock
    def test_list_subscriptions_with_both_filters(
        self, client: Drip, base_url: str
    ) -> None:
        """Should list subscriptions filtered by both customer ID and status."""
        respx.get(
            f"{base_url}/subscriptions?customerId=cust_789&status=PAUSED"
        ).mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        result = client.list_subscriptions(customer_id="cust_789", status="PAUSED")

        assert len(result.data) == 0


class TestCancelSubscription:
    @respx.mock
    def test_cancel_subscription(self, client: Drip, base_url: str) -> None:
        """Should cancel a subscription with correct POST request."""
        cancelled_json = {
            **MOCK_SUBSCRIPTION_JSON,
            "status": "CANCELLED",
            "cancelledAt": "2024-01-15T00:00:00Z",
        }
        respx.post(f"{base_url}/subscriptions/sub_abc123/cancel").mock(
            return_value=httpx.Response(200, json=cancelled_json)
        )

        sub = client.cancel_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.CANCELLED
        assert sub.cancelled_at == "2024-01-15T00:00:00Z"


class TestPauseSubscription:
    @respx.mock
    def test_pause_subscription(self, client: Drip, base_url: str) -> None:
        """Should pause a subscription with correct POST request."""
        paused_json = {
            **MOCK_SUBSCRIPTION_JSON,
            "status": "PAUSED",
        }
        respx.post(f"{base_url}/subscriptions/sub_abc123/pause").mock(
            return_value=httpx.Response(200, json=paused_json)
        )

        sub = client.pause_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.PAUSED


class TestResumeSubscription:
    @respx.mock
    def test_resume_subscription(self, client: Drip, base_url: str) -> None:
        """Should resume a subscription with correct POST request."""
        resumed_json = {
            **MOCK_SUBSCRIPTION_JSON,
            "status": "ACTIVE",
            "nextChargeAt": "2024-02-15T00:00:00Z",
        }
        respx.post(f"{base_url}/subscriptions/sub_abc123/resume").mock(
            return_value=httpx.Response(200, json=resumed_json)
        )

        sub = client.resume_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.next_charge_at == "2024-02-15T00:00:00Z"


# =============================================================================
# Async Subscription API Tests
# =============================================================================


class TestAsyncSubscriptions:
    @respx.mock
    @pytest.mark.asyncio
    async def test_async_create_subscription(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should create subscription asynchronously."""
        respx.post(f"{base_url}/subscriptions").mock(
            return_value=httpx.Response(200, json=MOCK_SUBSCRIPTION_JSON)
        )

        sub = await async_client.create_subscription(
            customer_id="cust_789",
            name="Pro Plan",
            amount_usdc="29.99",
            interval="MONTHLY",
        )

        assert sub.id == "sub_abc123"
        assert sub.status == SubscriptionStatus.ACTIVE

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_get_subscription(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should get subscription asynchronously."""
        respx.get(f"{base_url}/subscriptions/sub_abc123").mock(
            return_value=httpx.Response(200, json=MOCK_SUBSCRIPTION_JSON)
        )

        sub = await async_client.get_subscription("sub_abc123")

        assert sub.id == "sub_abc123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_list_subscriptions(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should list subscriptions asynchronously."""
        respx.get(f"{base_url}/subscriptions").mock(
            return_value=httpx.Response(
                200,
                json={"data": [MOCK_SUBSCRIPTION_JSON]},
            )
        )

        result = await async_client.list_subscriptions()

        assert len(result.data) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_cancel_subscription(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should cancel subscription asynchronously."""
        cancelled_json = {
            **MOCK_SUBSCRIPTION_JSON,
            "status": "CANCELLED",
            "cancelledAt": "2024-01-15T00:00:00Z",
        }
        respx.post(f"{base_url}/subscriptions/sub_abc123/cancel").mock(
            return_value=httpx.Response(200, json=cancelled_json)
        )

        sub = await async_client.cancel_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.CANCELLED

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_pause_subscription(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should pause subscription asynchronously."""
        paused_json = {**MOCK_SUBSCRIPTION_JSON, "status": "PAUSED"}
        respx.post(f"{base_url}/subscriptions/sub_abc123/pause").mock(
            return_value=httpx.Response(200, json=paused_json)
        )

        sub = await async_client.pause_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.PAUSED

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_resume_subscription(
        self, async_client: AsyncDrip, base_url: str
    ) -> None:
        """Should resume subscription asynchronously."""
        respx.post(f"{base_url}/subscriptions/sub_abc123/resume").mock(
            return_value=httpx.Response(200, json=MOCK_SUBSCRIPTION_JSON)
        )

        sub = await async_client.resume_subscription("sub_abc123")

        assert sub.status == SubscriptionStatus.ACTIVE


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestSubscriptionModels:
    def test_subscription_interval_values(self) -> None:
        """SubscriptionInterval enum should have correct values."""
        assert SubscriptionInterval.DAILY == "DAILY"
        assert SubscriptionInterval.WEEKLY == "WEEKLY"
        assert SubscriptionInterval.MONTHLY == "MONTHLY"
        assert SubscriptionInterval.YEARLY == "YEARLY"

    def test_subscription_status_values(self) -> None:
        """SubscriptionStatus enum should have correct values."""
        assert SubscriptionStatus.ACTIVE == "ACTIVE"
        assert SubscriptionStatus.PAUSED == "PAUSED"
        assert SubscriptionStatus.CANCELLED == "CANCELLED"
        assert SubscriptionStatus.EXPIRED == "EXPIRED"

    def test_subscription_model_from_json(self) -> None:
        """Subscription model should parse from JSON correctly."""
        sub = Subscription.model_validate(MOCK_SUBSCRIPTION_JSON)

        assert sub.id == "sub_abc123"
        assert sub.business_id == "biz_456"
        assert sub.customer_id == "cust_789"
        assert sub.name == "Pro Plan"
        assert sub.amount_usdc == "29.99"
        assert sub.interval == SubscriptionInterval.MONTHLY
        assert sub.current_period == 1
        assert sub.next_charge_at == "2024-02-01T00:00:00Z"
        assert sub.last_charged_at == "2024-01-01T00:00:00Z"
        assert sub.start_date == "2024-01-01T00:00:00Z"
        assert sub.end_date is None
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.cancelled_at is None
        assert sub.metadata is None

    def test_list_subscriptions_response_model(self) -> None:
        """ListSubscriptionsResponse model should parse correctly."""
        result = ListSubscriptionsResponse.model_validate(
            {"data": [MOCK_SUBSCRIPTION_JSON, MOCK_SUBSCRIPTION_JSON]}
        )

        assert len(result.data) == 2
        assert all(isinstance(s, Subscription) for s in result.data)
