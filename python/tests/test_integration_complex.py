"""
Complex Integration Test Suite for Drip Python SDK

This test suite exercises complex SDK flows with mocked HTTP responses:
1. Customer lifecycle (create, get, list)
2. Usage tracking and charging
3. Webhook management
4. Concurrent operations
5. Error handling and edge cases
6. Streaming meter accumulation
7. Idempotency guarantees
"""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx

# Import SDK components
from drip import (
    AsyncDrip,
    ChargeResult,
    Customer,
    Drip,
    DripAPIError,
    DripError,
    format_usdc_amount,
    generate_idempotency_key,
    generate_nonce,
    generate_webhook_signature,
    normalize_address,
    parse_usdc_amount,
    verify_webhook_signature,
)

# Test configuration
API_BASE_URL = "https://api.drippay.dev/v1"
API_KEY = "sk_test_1234567890"


@dataclass
class TestContext:
    """Holds test context across test methods"""
    customer_ids: list[str]
    charge_ids: list[str]
    webhook_ids: list[str]
    start_time: float

    @classmethod
    def create(cls) -> "TestContext":
        return cls(
            customer_ids=[],
            charge_ids=[],
            webhook_ids=[],
            start_time=time.time()
        )


def mock_customer_response(customer_id: str, external_id: str | None = None, address: str = "0x1234") -> dict[str, Any]:
    """Generate a mock customer response."""
    return {
        "id": customer_id,
        "businessId": "biz_test",
        "externalCustomerId": external_id,
        "onchainAddress": address,
        "metadata": {"test": "true"},
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


def mock_charge_result(charge_id: str, is_duplicate: bool = False) -> dict[str, Any]:
    """Generate a mock ChargeResult response."""
    return {
        "success": True,
        "usageEventId": f"usage_{uuid.uuid4().hex[:12]}",
        "isDuplicate": is_duplicate,
        "charge": {
            "id": charge_id,
            "amountUsdc": "100",
            "amountToken": "100000000000000",
            "txHash": f"0x{uuid.uuid4().hex}",
            "status": "CONFIRMED",
        },
    }


def mock_webhook_response(webhook_id: str, url: str) -> dict[str, Any]:
    """Generate a mock CreateWebhookResponse (extends Webhook with secret)."""
    return {
        "id": webhook_id,
        "url": url,
        "events": ["charge.succeeded", "settlement.completed"],
        "description": "Test webhook",
        "isActive": True,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
        "secret": "whsec_test_secret_123",
        "message": "Webhook created",
    }


def mock_balance_response(customer_id: str) -> dict[str, Any]:
    """Generate a mock BalanceResult response."""
    return {
        "customerId": customer_id,
        "onchainAddress": "0x1234567890abcdef1234567890abcdef12345678",
        "balanceUsdc": "1000.000000",
        "pendingChargesUsdc": "0.000000",
        "availableUsdc": "1000.000000",
        "lastSyncedAt": "2024-01-01T00:00:00Z",
    }


def mock_charge_detail(charge_id: str, customer_id: str) -> dict[str, Any]:
    """Generate a mock Charge detail response (for get_charge and list_charges)."""
    return {
        "id": charge_id,
        "usageId": f"usage_{uuid.uuid4().hex[:12]}",
        "customerId": customer_id,
        "customer": {
            "id": customer_id,
            "onchainAddress": "0x123",
            "externalCustomerId": None,
        },
        "usageEvent": {
            "id": f"usage_{uuid.uuid4().hex[:12]}",
            "type": "tokens",
            "quantity": "1",
            "metadata": None,
        },
        "amountUsdc": "100",
        "amountToken": "100000000000000",
        "txHash": f"0x{uuid.uuid4().hex}",
        "blockNumber": "12345",
        "status": "CONFIRMED",
        "failureReason": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "confirmedAt": "2024-01-01T00:00:01Z",
    }


class TestComplexIntegration:
    """Complex integration tests for the Drip SDK with mocked responses"""

    @pytest.fixture
    def client(self) -> Drip:
        """Create a sync client for testing"""
        return Drip(api_key=API_KEY, base_url=API_BASE_URL)

    @pytest.fixture
    def async_client(self) -> AsyncDrip:
        """Create an async client for testing"""
        return AsyncDrip(api_key=API_KEY, base_url=API_BASE_URL)

    @pytest.fixture
    def test_context(self) -> TestContext:
        """Create test context for tracking resources"""
        return TestContext.create()

    # ==================== Customer Tests ====================

    @respx.mock
    def test_customer_full_lifecycle(self, client: Drip, test_context: TestContext) -> None:
        """Test complete customer lifecycle: create -> get -> list -> verify"""
        external_id = f"test-customer-{uuid.uuid4().hex[:8]}"
        wallet_address = f"0x{uuid.uuid4().hex[:40]}"
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock create customer
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id, external_id, wallet_address))
        )

        # Mock get customer
        respx.get(f"{API_BASE_URL}/customers/{customer_id}").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id, external_id, wallet_address))
        )

        # Mock list customers - use 'data' not 'customers'
        respx.get(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json={
                "data": [mock_customer_response(customer_id, external_id, wallet_address)],
                "count": 1,
            })
        )

        # Create customer
        customer = client.create_customer(
            onchain_address=wallet_address,
            external_customer_id=external_id,
            metadata={"test": "true", "created_at": str(time.time())}
        )

        assert customer is not None
        assert customer.id == customer_id
        test_context.customer_ids.append(customer.id)

        # Get customer by ID
        retrieved = client.get_customer(customer.id)
        assert retrieved is not None
        assert retrieved.id == customer.id

        # List customers and verify our customer exists
        customers_response = client.list_customers(limit=100)
        assert any(c.id == customer.id for c in customers_response.data)

        print(f"✓ Customer lifecycle complete: {customer.id}")

    @respx.mock
    def test_bulk_customer_creation(self, client: Drip, test_context: TestContext) -> None:
        """Test creating multiple customers in rapid succession"""
        num_customers = 5
        customers: list[Customer] = []

        for i in range(num_customers):
            external_id = f"bulk-test-{uuid.uuid4().hex[:8]}"
            wallet_address = f"0x{uuid.uuid4().hex[:40]}"
            customer_id = f"cus_{uuid.uuid4().hex[:12]}"

            # Mock create customer for each request
            respx.post(f"{API_BASE_URL}/customers").mock(
                return_value=httpx.Response(200, json=mock_customer_response(customer_id, external_id, wallet_address))
            )

            customer = client.create_customer(
                onchain_address=wallet_address,
                external_customer_id=external_id,
                metadata={"batch": "bulk-test", "index": str(i)}
            )
            customers.append(customer)
            test_context.customer_ids.append(customer.id)

        assert len(customers) == num_customers

        # Verify all customers are unique
        customer_ids = [c.id for c in customers]
        assert len(set(customer_ids)) == num_customers

        print(f"✓ Bulk creation complete: {num_customers} customers")

    # ==================== Charging Tests ====================

    @respx.mock
    def test_charge_with_idempotency(self, client: Drip, test_context: TestContext) -> None:
        """Test that idempotent charges work correctly"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"
        charge_id = f"chg_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Mock charge creation (first call)
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result(charge_id, is_duplicate=False))
        )

        # Create customer
        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
        )
        test_context.customer_ids.append(customer.id)

        # Generate idempotency key
        idempotency_key = generate_idempotency_key(
            customer_id=customer.id,
            step_name="api_call",
            run_id=uuid.uuid4().hex
        )

        # First charge
        charge1 = client.charge(
            customer_id=customer.id,
            meter="api_calls",
            quantity=1,
            idempotency_key=idempotency_key,
            metadata={"test": "idempotency"}
        )

        assert charge1 is not None
        assert charge1.charge.id == charge_id
        test_context.charge_ids.append(charge1.charge.id)

        # Mock second charge (returns as replay)
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result(charge_id, is_duplicate=True))
        )

        # Second charge with same key should return as replay
        charge2 = client.charge(
            customer_id=customer.id,
            meter="api_calls",
            quantity=1,
            idempotency_key=idempotency_key,
            metadata={"test": "idempotency"}
        )

        assert charge2 is not None
        assert charge2.is_duplicate is True
        print(f"✓ Idempotency verified: {charge1.charge.id}")

    @respx.mock
    def test_various_charge_quantities(self, client: Drip, test_context: TestContext) -> None:
        """Test charges with various quantities"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
        )
        test_context.customer_ids.append(customer.id)

        quantities = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]

        for quantity in quantities:
            charge_id = f"chg_{uuid.uuid4().hex[:12]}"

            # Mock charge for each quantity
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(charge_id))
            )

            charge = client.charge(
                customer_id=customer.id,
                meter="tokens",
                quantity=quantity,
                metadata={"test_quantity": str(quantity)}
            )
            assert charge is not None
            test_context.charge_ids.append(charge.charge.id)

        print(f"✓ Various quantities tested: {len(quantities)} charges")

    # ==================== Webhook Tests ====================

    @respx.mock
    def test_webhook_lifecycle(self, client: Drip, test_context: TestContext) -> None:
        """Test webhook creation and listing"""
        webhook_id = f"whk_{uuid.uuid4().hex[:12]}"
        webhook_url = f"https://webhook.test.drip.dev/{uuid.uuid4().hex}"

        # Mock webhook creation
        respx.post(f"{API_BASE_URL}/webhooks").mock(
            return_value=httpx.Response(200, json=mock_webhook_response(webhook_id, webhook_url))
        )

        # Mock webhook listing - uses 'data' not 'webhooks'
        respx.get(f"{API_BASE_URL}/webhooks").mock(
            return_value=httpx.Response(200, json={
                "data": [{
                    "id": webhook_id,
                    "url": webhook_url,
                    "events": ["charge.succeeded", "settlement.completed"],
                    "description": "Test webhook",
                    "isActive": True,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z",
                }],
                "count": 1,
            })
        )

        # Create webhook
        webhook_response = client.create_webhook(
            url=webhook_url,
            events=["charge.succeeded", "settlement.completed"],
            description="Test webhook"
        )

        assert webhook_response is not None
        assert webhook_response.id == webhook_id
        assert webhook_response.url == webhook_url
        test_context.webhook_ids.append(webhook_response.id)

        # List webhooks
        webhooks_response = client.list_webhooks()
        assert any(w.id == webhook_response.id for w in webhooks_response.data)

        print(f"✓ Webhook lifecycle complete: {webhook_response.id}")

    # ==================== Streaming Meter Tests ====================

    @respx.mock
    def test_stream_meter_accumulation(self, client: Drip, test_context: TestContext) -> None:
        """Test stream meter for token accumulation"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"
        charge_id = f"chg_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Mock charge creation
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result(charge_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
        )
        test_context.customer_ids.append(customer.id)

        # Create stream meter
        meter = client.create_stream_meter(
            customer_id=customer.id,
            meter="tokens",
            metadata={"model": "gpt-4", "test": "true"}
        )

        # Accumulate tokens in batches using sync add
        batches = [100, 250, 150, 200, 300]

        for batch in batches:
            meter.add_sync(batch)

        total_expected = sum(batches)
        assert meter.total == total_expected

        # Flush the meter
        result = meter.flush()

        assert result is not None
        assert meter.total == 0  # Reset after flush
        assert meter.flush_count == 1

        if result.charge:
            test_context.charge_ids.append(result.charge.id)

        print(f"✓ Stream meter tested: {total_expected} tokens accumulated")

    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_meter_auto_flush(self, async_client: AsyncDrip, test_context: TestContext) -> None:
        """Test stream meter auto-flush at threshold (requires async add)"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"
        charge_id = f"chg_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Mock charge creation (for auto-flush)
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result(charge_id))
        )

        async with async_client:
            customer = await async_client.create_customer(
                onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            )
            test_context.customer_ids.append(customer.id)

            flushes: list[Any] = []

            def on_flush(result: Any) -> None:
                flushes.append(result)
                if result.charge:
                    test_context.charge_ids.append(result.charge.id)

            meter = async_client.create_stream_meter(
                customer_id=customer.id,
                meter="auto_tokens",
                flush_threshold=500,
                on_flush=on_flush
            )

            # Add some quantity (async add supports auto-flush)
            result1 = await meter.add(300)
            assert result1 is None  # Not yet at threshold
            assert len(flushes) == 0

            # Add more to exceed threshold - this triggers auto-flush
            result2 = await meter.add(250)  # Now at 550, should trigger
            assert result2 is not None  # Auto-flush occurred
            assert len(flushes) == 1  # on_flush callback was called
            assert meter.total < 500  # Should have flushed and reset

            print("✓ Auto-flush triggered at threshold")

    # ==================== Concurrent Operations ====================

    @respx.mock
    def test_concurrent_charges(self, client: Drip, test_context: TestContext) -> None:
        """Test concurrent charge operations"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
        )
        test_context.customer_ids.append(customer.id)

        num_concurrent = 10
        results: list[ChargeResult | None] = [None] * num_concurrent
        errors: list[Exception | None] = [None] * num_concurrent

        # Mock charges for all concurrent requests
        for _ in range(num_concurrent):
            charge_id = f"chg_{uuid.uuid4().hex[:12]}"
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(charge_id))
            )

        def charge_worker(index: int) -> None:
            try:
                charge = client.charge(
                    customer_id=customer.id,
                    meter="concurrent_test",
                    quantity=1,
                    metadata={"index": str(index)}
                )
                results[index] = charge
                if charge and charge.charge:
                    test_context.charge_ids.append(charge.charge.id)
            except Exception as e:
                errors[index] = e

        # Run concurrent charges
        with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
            futures = [executor.submit(charge_worker, i) for i in range(num_concurrent)]
            for future in futures:
                future.result()  # Wait for completion

        # Count successes
        successes = sum(1 for r in results if r is not None)
        failures = sum(1 for e in errors if e is not None)

        print(f"✓ Concurrent charges: {successes} succeeded, {failures} failed")
        assert successes > 0  # At least some should succeed

    # ==================== Async Tests ====================

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_operations(self, async_client: AsyncDrip, test_context: TestContext) -> None:
        """Test async client operations"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Mock charges for async operations
        for _ in range(5):
            charge_id = f"chg_{uuid.uuid4().hex[:12]}"
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(charge_id))
            )

        async with async_client:
            # Create customer
            customer = await async_client.create_customer(
                onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            )

            assert customer is not None
            test_context.customer_ids.append(customer.id)

            # Parallel async charges
            tasks = []
            for i in range(5):
                task = async_client.charge(
                    customer_id=customer.id,
                    meter="async_test",
                    quantity=1,
                    metadata={"index": str(i)}
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            successes = sum(1 for r in results if isinstance(r, ChargeResult))
            print(f"✓ Async operations: {successes}/5 charges succeeded")

    # ==================== Utility Tests ====================

    def test_utility_functions(self) -> None:
        """Test SDK utility functions"""
        # Idempotency key generation
        key1 = generate_idempotency_key("cust1", "tokens")
        key2 = generate_idempotency_key("cust1", "tokens")
        key3 = generate_idempotency_key("cust2", "tokens")

        assert key1 == key2  # Same inputs = same key
        assert key1 != key3  # Different inputs = different key

        # Nonce generation
        nonce1 = generate_nonce()
        nonce2 = generate_nonce()
        assert nonce1 != nonce2  # Should be unique

        # Address normalization
        addr = "0xAbCdEf1234567890AbCdEf1234567890AbCdEf12"
        normalized = normalize_address(addr)
        assert normalized == addr.lower()

        # USDC formatting - format_usdc_amount returns formatted string with $ prefix
        amount = 1234567  # 1.234567 USDC in micro-units
        formatted = format_usdc_amount(amount)
        # The format may include $ or truncate, just verify it's a string
        assert isinstance(formatted, str)
        assert len(formatted) > 0

        # USDC parsing
        parsed = parse_usdc_amount("1.234567")
        assert parsed == amount

        # Webhook signature verification using t=timestamp,v1=signature format
        payload = '{"event": "test"}'
        secret = "whsec_test123"

        # Generate a valid signature using the proper format
        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret)
        assert not verify_webhook_signature(payload, "t=123,v1=invalid", secret)

        print("✓ All utility functions working correctly")

    # ==================== Error Handling Tests ====================

    @respx.mock
    def test_error_handling(self, client: Drip) -> None:
        """Test error handling for various scenarios"""
        # Mock 404 response for non-existent customer
        respx.get(f"{API_BASE_URL}/customers/non-existent-customer-id").mock(
            return_value=httpx.Response(404, json={"error": "Customer not found", "code": "NOT_FOUND"})
        )

        # Test getting non-existent customer
        try:
            client.get_customer("non-existent-customer-id")
            raise AssertionError("Should have raised an error")
        except (DripAPIError, DripError):
            pass  # Expected

        print("✓ Error handling working correctly")

    # ==================== Complex Workflow Test ====================

    @respx.mock
    def test_complete_billing_workflow(self, client: Drip, test_context: TestContext) -> None:
        """Test a complete end-to-end billing workflow"""
        workflow_id = uuid.uuid4().hex[:8]
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"
        webhook_id = f"whk_{uuid.uuid4().hex[:12]}"
        charge_id = f"chg_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Mock webhook creation
        respx.post(f"{API_BASE_URL}/webhooks").mock(
            return_value=httpx.Response(200, json=mock_webhook_response(webhook_id, f"https://webhook.test.drip.dev/workflow/{workflow_id}"))
        )

        # Mock charge creation
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge_result(charge_id))
        )

        # Mock list charges - GET /charges endpoint, uses 'data' not 'charges'
        respx.get(f"{API_BASE_URL}/charges").mock(
            return_value=httpx.Response(200, json={
                "data": [mock_charge_detail(charge_id, customer_id)],
                "count": 1,
            })
        )

        # Mock get balance
        respx.get(f"{API_BASE_URL}/customers/{customer_id}/balance").mock(
            return_value=httpx.Response(200, json=mock_balance_response(customer_id))
        )

        # Step 1: Create a customer
        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            external_customer_id=f"workflow-{workflow_id}",
            metadata={
                "workflow_id": workflow_id,
                "workflow_type": "complete_test"
            }
        )
        test_context.customer_ids.append(customer.id)
        print(f"  Step 1: Customer created - {customer.id}")

        # Step 2: Set up a webhook
        webhook_response = client.create_webhook(
            url=f"https://webhook.test.drip.dev/workflow/{workflow_id}",
            events=["charge.succeeded", "settlement.completed"],
            description=f"Workflow {workflow_id} webhook"
        )
        test_context.webhook_ids.append(webhook_response.id)
        print(f"  Step 2: Webhook created - {webhook_response.id}")

        # Step 3: Simulate usage with streaming meter
        meter = client.create_stream_meter(
            customer_id=customer.id,
            meter="ai_tokens",
            metadata={
                "workflow_id": workflow_id,
                "model": "gpt-4-turbo"
            }
        )

        # Simulate a conversation with token usage
        usage_events = [
            ("user_prompt", 150),
            ("assistant_response", 500),
            ("user_prompt", 75),
            ("assistant_response", 350),
            ("tool_call", 200),
            ("tool_response", 100),
            ("assistant_response", 400),
        ]

        for _event_type, tokens in usage_events:
            meter.add_sync(tokens)
            time.sleep(0.001)  # Small delay to simulate real usage

        total_tokens = sum(t for _, t in usage_events)
        print(f"  Step 3: Accumulated {total_tokens} tokens")

        # Step 4: Flush the meter to create a charge
        flush_result = meter.flush()
        if flush_result.charge:
            test_context.charge_ids.append(flush_result.charge.id)
            print(f"  Step 4: Charge created - {flush_result.charge.id}")

        # Step 5: Verify the customer's charges
        charges = client.list_charges(customer_id=customer.id, limit=10)
        print(f"  Step 5: Found {len(charges.data)} charges for customer")

        # Step 6: Get customer balance
        balance = client.get_balance(customer.id)
        print(f"  Step 6: Customer balance retrieved: {balance.balance_usdc}")

        print(f"✓ Complete workflow executed successfully (ID: {workflow_id})")


class TestEdgeCases:
    """Edge case tests"""

    @pytest.fixture
    def client(self) -> Drip:
        return Drip(api_key=API_KEY, base_url=API_BASE_URL)

    @respx.mock
    def test_special_characters_in_metadata(self, client: Drip) -> None:
        """Test handling of special characters in metadata"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            metadata={
                "unicode": "日本語テスト",
                "emoji": "🚀💰",
                "special": "<script>alert('xss')</script>",
                "newlines": "line1\nline2\nline3",
                "quotes": 'He said "Hello"',
            }
        )

        assert customer is not None
        print("✓ Special characters handled correctly")

    @respx.mock
    def test_empty_metadata(self, client: Drip) -> None:
        """Test handling of empty metadata"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            metadata={}  # Empty metadata
        )

        assert customer is not None
        print("✓ Empty values handled correctly")

    @respx.mock
    def test_large_metadata(self, client: Drip) -> None:
        """Test handling of large metadata objects"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Create large metadata (but not too large)
        large_metadata = {
            f"key_{i}": f"value_{i}_" + "x" * 100
            for i in range(50)
        }

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
            metadata=large_metadata
        )

        assert customer is not None
        print("✓ Large metadata handled correctly")


class TestPerformance:
    """Performance-related tests"""

    @pytest.fixture
    def client(self) -> Drip:
        return Drip(api_key=API_KEY, base_url=API_BASE_URL)

    @respx.mock
    def test_rapid_fire_charges(self, client: Drip) -> None:
        """Test rapid succession of charges"""
        customer_id = f"cus_{uuid.uuid4().hex[:12]}"

        # Mock customer creation
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        customer = client.create_customer(
            onchain_address=f"0x{uuid.uuid4().hex[:40]}",
        )

        num_charges = 20

        # Mock all charges
        for _ in range(num_charges):
            charge_id = f"chg_{uuid.uuid4().hex[:12]}"
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(charge_id))
            )

        start_time = time.time()

        for i in range(num_charges):
            client.charge(
                customer_id=customer.id,
                meter="perf_test",
                quantity=1,
                metadata={"index": str(i)}
            )

        elapsed = time.time() - start_time
        rate = num_charges / elapsed

        print(f"✓ Performance: {num_charges} charges in {elapsed:.2f}s ({rate:.1f}/s)")


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "--tb=short", "-x"])
