"""
Find the SDK's transaction limit - push until it breaks.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import respx

from drip import Drip

API_BASE_URL = "https://api.drippay.dev/v1"
API_KEY = "sk_test_limit12345"


def mock_charge() -> dict:
    return {
        "success": True,
        "usageEventId": "u123",
        "isDuplicate": False,
        "charge": {
            "id": f"chg_{uuid.uuid4().hex[:8]}",
            "amountUsdc": "100",
            "amountToken": "100000000000000",
            "txHash": "0xabc",
            "status": "CONFIRMED",
        },
    }


def mock_customer() -> dict:
    return {
        "id": "cus_limit",
        "businessId": "biz",
        "externalCustomerId": None,
        "onchainAddress": "0x1234",
        "metadata": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


@respx.mock
def test_find_concurrent_limit():
    """Scale up workers until we find failures."""

    # Setup mocks
    respx.post(f"{API_BASE_URL}/customers").mock(
        return_value=httpx.Response(200, json=mock_customer())
    )
    for _ in range(100000):
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge())
        )

    client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
    client.create_customer(onchain_address="0x1234")

    print("\n" + "=" * 70)
    print("FINDING CONCURRENT TRANSACTION LIMIT")
    print("=" * 70)

    worker_counts = [10, 25, 50, 100, 200, 500, 1000]
    requests_per_test = 5000

    for num_workers in worker_counts:
        successful = 0
        failed = 0

        def charge():
            try:
                client.charge(customer_id="cus_limit", meter="test", quantity=1)
                return True
            except Exception:
                return False

        start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(charge) for _ in range(requests_per_test)]
            for f in as_completed(futures):
                if f.result():
                    successful += 1
                else:
                    failed += 1

        duration = time.perf_counter() - start
        rps = requests_per_test / duration
        fail_rate = (failed / requests_per_test) * 100

        status = "✓" if failed == 0 else "✗"
        print(f"{status} {num_workers:4} workers | {rps:8.0f} tx/s | {failed:4} failed ({fail_rate:.1f}%)")

        if fail_rate > 5:
            print(f"\n>>> LIMIT FOUND: {num_workers} workers causes >5% failures")
            break

    print("=" * 70)


@respx.mock
def test_find_sequential_limit():
    """Find max sequential throughput."""

    respx.post(f"{API_BASE_URL}/customers").mock(
        return_value=httpx.Response(200, json=mock_customer())
    )
    for _ in range(50000):
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge())
        )

    client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
    client.create_customer(onchain_address="0x1234")

    print("\n" + "=" * 70)
    print("FINDING SEQUENTIAL TRANSACTION LIMIT")
    print("=" * 70)

    test_sizes = [1000, 5000, 10000, 20000, 50000]

    for num_requests in test_sizes:
        successful = 0
        failed = 0

        start = time.perf_counter()

        for _ in range(num_requests):
            try:
                client.charge(customer_id="cus_limit", meter="test", quantity=1)
                successful += 1
            except Exception:
                failed += 1

        duration = time.perf_counter() - start
        rps = num_requests / duration

        status = "✓" if failed == 0 else "✗"
        print(f"{status} {num_requests:6} requests | {rps:8.0f} tx/s | {failed} failed | {duration:.2f}s")

    print("=" * 70)


@respx.mock
def test_burst_limit():
    """Test burst capacity - how many can we fire at once."""

    respx.post(f"{API_BASE_URL}/customers").mock(
        return_value=httpx.Response(200, json=mock_customer())
    )
    for _ in range(100000):
        respx.post(f"{API_BASE_URL}/usage").mock(
            return_value=httpx.Response(200, json=mock_charge())
        )

    client = Drip(api_key=API_KEY, base_url=API_BASE_URL)
    client.create_customer(onchain_address="0x1234")

    print("\n" + "=" * 70)
    print("BURST CAPACITY TEST")
    print("=" * 70)

    burst_sizes = [100, 500, 1000, 2000, 5000, 10000]

    for burst_size in burst_sizes:
        successful = 0
        failed = 0

        def charge():
            try:
                client.charge(customer_id="cus_limit", meter="test", quantity=1)
                return True
            except Exception:
                return False

        start = time.perf_counter()

        # Fire all at once with max workers = burst size
        with ThreadPoolExecutor(max_workers=burst_size) as executor:
            futures = [executor.submit(charge) for _ in range(burst_size)]
            for f in as_completed(futures):
                if f.result():
                    successful += 1
                else:
                    failed += 1

        duration = time.perf_counter() - start
        rps = burst_size / duration

        status = "✓" if failed == 0 else "✗"
        print(f"{status} Burst {burst_size:5} | {rps:8.0f} tx/s | {failed} failed | {duration:.3f}s")

    print("=" * 70)


if __name__ == "__main__":
    test_find_sequential_limit()
    test_find_concurrent_limit()
    test_burst_limit()
