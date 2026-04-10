"""
Stress Test for Drip Python SDK - Measure Maximum Transactions Per Second

This test measures how many charges the SDK can process per second
with mocked HTTP responses (testing SDK overhead, not network latency).
"""

import asyncio
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import httpx
import pytest
import respx

from drip import AsyncDrip, Drip

API_BASE_URL = "https://api.drippay.dev/v1"
API_KEY = "sk_test_stress1234"


def mock_charge_result(charge_id: str) -> dict:
    """Generate a minimal mock charge result for speed."""
    return {
        "success": True,
        "usageEventId": "usage_123",
        "isDuplicate": False,
        "charge": {
            "id": charge_id,
            "amountUsdc": "100",
            "amountToken": "100000000000000",
            "txHash": "0xabc123",
            "status": "CONFIRMED",
        },
    }


def mock_customer_response(customer_id: str) -> dict:
    """Generate a minimal mock customer response."""
    return {
        "id": customer_id,
        "businessId": "biz_test",
        "externalCustomerId": None,
        "onchainAddress": "0x1234",
        "metadata": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


@dataclass
class StressTestResult:
    """Results from a stress test run."""
    total_requests: int
    successful: int
    failed: int
    duration_seconds: float
    requests_per_second: float
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


class TestStressPerformance:
    """Stress tests for SDK performance."""

    @respx.mock
    def test_sequential_charge_throughput(self) -> None:
        """Measure sequential charge throughput (single thread)."""
        customer_id = "cus_stress_seq"

        # Mock endpoints
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Pre-register many mock responses
        for _ in range(1000):
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(f"chg_{uuid.uuid4().hex[:8]}"))
            )

        client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
        client.create_customer(onchain_address="0x1234")

        num_requests = 500
        latencies: list[float] = []

        start_time = time.perf_counter()

        for _i in range(num_requests):
            req_start = time.perf_counter()
            client.charge(
                customer_id=customer_id,
                meter="stress_test",
                quantity=1,
            )
            latencies.append((time.perf_counter() - req_start) * 1000)

        duration = time.perf_counter() - start_time
        rps = num_requests / duration

        result = StressTestResult(
            total_requests=num_requests,
            successful=num_requests,
            failed=0,
            duration_seconds=duration,
            requests_per_second=rps,
            avg_latency_ms=statistics.mean(latencies),
            min_latency_ms=min(latencies),
            max_latency_ms=max(latencies),
            p50_latency_ms=statistics.median(latencies),
            p95_latency_ms=sorted(latencies)[int(len(latencies) * 0.95)],
            p99_latency_ms=sorted(latencies)[int(len(latencies) * 0.99)],
        )

        print("\n" + "=" * 60)
        print("SEQUENTIAL CHARGE THROUGHPUT TEST")
        print("=" * 60)
        print(f"Total Requests:     {result.total_requests}")
        print(f"Successful:         {result.successful}")
        print(f"Failed:             {result.failed}")
        print(f"Duration:           {result.duration_seconds:.2f}s")
        print(f"Throughput:         {result.requests_per_second:.1f} req/s")
        print(f"Avg Latency:        {result.avg_latency_ms:.3f}ms")
        print(f"Min Latency:        {result.min_latency_ms:.3f}ms")
        print(f"Max Latency:        {result.max_latency_ms:.3f}ms")
        print(f"P50 Latency:        {result.p50_latency_ms:.3f}ms")
        print(f"P95 Latency:        {result.p95_latency_ms:.3f}ms")
        print(f"P99 Latency:        {result.p99_latency_ms:.3f}ms")
        print("=" * 60)

        assert result.requests_per_second > 100, f"Sequential throughput too low: {result.requests_per_second}"

    @respx.mock
    def test_concurrent_charge_throughput(self) -> None:
        """Measure concurrent charge throughput (multi-threaded)."""
        customer_id = "cus_stress_conc"

        # Mock endpoints
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Pre-register many mock responses
        for _ in range(2000):
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(f"chg_{uuid.uuid4().hex[:8]}"))
            )

        client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
        client.create_customer(onchain_address="0x1234")

        num_requests = 1000
        num_workers = 10
        latencies: list[float] = []
        successful = 0
        failed = 0

        def charge_worker() -> tuple[bool, float]:
            req_start = time.perf_counter()
            try:
                client.charge(
                    customer_id=customer_id,
                    meter="stress_test",
                    quantity=1,
                )
                return True, (time.perf_counter() - req_start) * 1000
            except Exception:
                return False, (time.perf_counter() - req_start) * 1000

        start_time = time.perf_counter()

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(charge_worker) for _ in range(num_requests)]
            for future in as_completed(futures):
                success, latency = future.result()
                latencies.append(latency)
                if success:
                    successful += 1
                else:
                    failed += 1

        duration = time.perf_counter() - start_time
        rps = num_requests / duration

        result = StressTestResult(
            total_requests=num_requests,
            successful=successful,
            failed=failed,
            duration_seconds=duration,
            requests_per_second=rps,
            avg_latency_ms=statistics.mean(latencies),
            min_latency_ms=min(latencies),
            max_latency_ms=max(latencies),
            p50_latency_ms=statistics.median(latencies),
            p95_latency_ms=sorted(latencies)[int(len(latencies) * 0.95)],
            p99_latency_ms=sorted(latencies)[int(len(latencies) * 0.99)],
        )

        print("\n" + "=" * 60)
        print(f"CONCURRENT CHARGE THROUGHPUT TEST ({num_workers} workers)")
        print("=" * 60)
        print(f"Total Requests:     {result.total_requests}")
        print(f"Successful:         {result.successful}")
        print(f"Failed:             {result.failed}")
        print(f"Duration:           {result.duration_seconds:.2f}s")
        print(f"Throughput:         {result.requests_per_second:.1f} req/s")
        print(f"Avg Latency:        {result.avg_latency_ms:.3f}ms")
        print(f"Min Latency:        {result.min_latency_ms:.3f}ms")
        print(f"Max Latency:        {result.max_latency_ms:.3f}ms")
        print(f"P50 Latency:        {result.p50_latency_ms:.3f}ms")
        print(f"P95 Latency:        {result.p95_latency_ms:.3f}ms")
        print(f"P99 Latency:        {result.p99_latency_ms:.3f}ms")
        print("=" * 60)

        assert result.successful == num_requests, f"Some requests failed: {result.failed}"
        assert result.requests_per_second > 500, f"Concurrent throughput too low: {result.requests_per_second}"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_charge_throughput(self) -> None:
        """Measure async charge throughput."""
        customer_id = "cus_stress_async"

        # Mock endpoints
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Pre-register many mock responses
        for _ in range(2000):
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(f"chg_{uuid.uuid4().hex[:8]}"))
            )

        async with AsyncDrip(api_key=API_KEY, base_url=API_BASE_URL) as client:
            await client.create_customer(onchain_address="0x1234")

            num_requests = 1000
            batch_size = 100  # Process in batches to avoid overwhelming
            latencies: list[float] = []
            successful = 0
            failed = 0

            async def charge_task() -> tuple[bool, float]:
                req_start = time.perf_counter()
                try:
                    await client.charge(
                        customer_id=customer_id,
                        meter="stress_test",
                        quantity=1,
                    )
                    return True, (time.perf_counter() - req_start) * 1000
                except Exception:
                    return False, (time.perf_counter() - req_start) * 1000

            start_time = time.perf_counter()

            # Process in batches
            for batch_start in range(0, num_requests, batch_size):
                batch_end = min(batch_start + batch_size, num_requests)
                tasks = [charge_task() for _ in range(batch_end - batch_start)]
                results = await asyncio.gather(*tasks)

                for success, latency in results:
                    latencies.append(latency)
                    if success:
                        successful += 1
                    else:
                        failed += 1

            duration = time.perf_counter() - start_time
            rps = num_requests / duration

            result = StressTestResult(
                total_requests=num_requests,
                successful=successful,
                failed=failed,
                duration_seconds=duration,
                requests_per_second=rps,
                avg_latency_ms=statistics.mean(latencies),
                min_latency_ms=min(latencies),
                max_latency_ms=max(latencies),
                p50_latency_ms=statistics.median(latencies),
                p95_latency_ms=sorted(latencies)[int(len(latencies) * 0.95)],
                p99_latency_ms=sorted(latencies)[int(len(latencies) * 0.99)],
            )

            print("\n" + "=" * 60)
            print(f"ASYNC CHARGE THROUGHPUT TEST (batch size: {batch_size})")
            print("=" * 60)
            print(f"Total Requests:     {result.total_requests}")
            print(f"Successful:         {result.successful}")
            print(f"Failed:             {result.failed}")
            print(f"Duration:           {result.duration_seconds:.2f}s")
            print(f"Throughput:         {result.requests_per_second:.1f} req/s")
            print(f"Avg Latency:        {result.avg_latency_ms:.3f}ms")
            print(f"Min Latency:        {result.min_latency_ms:.3f}ms")
            print(f"Max Latency:        {result.max_latency_ms:.3f}ms")
            print(f"P50 Latency:        {result.p50_latency_ms:.3f}ms")
            print(f"P95 Latency:        {result.p95_latency_ms:.3f}ms")
            print(f"P99 Latency:        {result.p99_latency_ms:.3f}ms")
            print("=" * 60)

            assert result.successful == num_requests, f"Some requests failed: {result.failed}"
            assert result.requests_per_second > 500, f"Async throughput too low: {result.requests_per_second}"

    @respx.mock
    def test_high_concurrency_stress(self) -> None:
        """Push concurrency limits with many workers."""
        customer_id = "cus_stress_high"

        # Mock endpoints
        respx.post(f"{API_BASE_URL}/customers").mock(
            return_value=httpx.Response(200, json=mock_customer_response(customer_id))
        )

        # Pre-register many mock responses
        for _ in range(5000):
            respx.post(f"{API_BASE_URL}/usage").mock(
                return_value=httpx.Response(200, json=mock_charge_result(f"chg_{uuid.uuid4().hex[:8]}"))
            )

        client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
        client.create_customer(onchain_address="0x1234")

        num_requests = 2000
        num_workers = 50
        successful = 0
        failed = 0

        def charge_worker() -> bool:
            try:
                client.charge(
                    customer_id=customer_id,
                    meter="stress_test",
                    quantity=1,
                )
                return True
            except Exception:
                return False

        start_time = time.perf_counter()

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(charge_worker) for _ in range(num_requests)]
            for future in as_completed(futures):
                if future.result():
                    successful += 1
                else:
                    failed += 1

        duration = time.perf_counter() - start_time
        rps = num_requests / duration
        success_rate = (successful / num_requests) * 100

        print("\n" + "=" * 60)
        print(f"HIGH CONCURRENCY STRESS TEST ({num_workers} workers)")
        print("=" * 60)
        print(f"Total Requests:     {num_requests}")
        print(f"Successful:         {successful}")
        print(f"Failed:             {failed}")
        print(f"Success Rate:       {success_rate:.1f}%")
        print(f"Duration:           {duration:.2f}s")
        print(f"Throughput:         {rps:.1f} req/s")
        print("=" * 60)

        assert success_rate >= 99, f"Success rate too low: {success_rate}%"
        assert rps > 1000, f"High concurrency throughput too low: {rps}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
