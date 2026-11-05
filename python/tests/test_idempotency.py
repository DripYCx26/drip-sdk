"""Tests for deterministic idempotency key generation."""

from __future__ import annotations

import re
import threading

from drip.client import _deterministic_idempotency_key, _call_counter_lock


def _reset_counter() -> None:
    """Reset the module-level counter for test isolation."""
    import drip.client as mod

    with _call_counter_lock:
        mod._call_counter = 0


class TestDeterministicIdempotencyKey:
    def setup_method(self) -> None:
        _reset_counter()

    def test_format(self) -> None:
        key = _deterministic_idempotency_key("chg", "cust_123", "tokens", 100)
        assert re.fullmatch(r"chg_[a-f0-9]{24}", key)

    def test_unique_per_call(self) -> None:
        key1 = _deterministic_idempotency_key("chg", "cust_123", "tokens", 100)
        key2 = _deterministic_idempotency_key("chg", "cust_123", "tokens", 100)
        assert key1 != key2

    def test_different_params_different_keys(self) -> None:
        key1 = _deterministic_idempotency_key("chg", "cust_123", "tokens", 100)
        _reset_counter()
        key2 = _deterministic_idempotency_key("chg", "cust_456", "tokens", 100)
        assert key1 != key2

    def test_different_prefix_different_keys(self) -> None:
        key1 = _deterministic_idempotency_key("chg", "cust_123")
        _reset_counter()
        key2 = _deterministic_idempotency_key("track", "cust_123")
        assert key1 != key2

    def test_filters_none(self) -> None:
        key = _deterministic_idempotency_key("evt", "run_1", None, 5)
        assert re.fullmatch(r"evt_[a-f0-9]{24}", key)

    def test_deterministic_with_same_counter(self) -> None:
        """Same counter state + same params = same key (deterministic).

        This is critical for retry safety: if a charge call times out and the
        caller retries with the same idempotency key, the server can deduplicate.
        """
        _reset_counter()
        key1 = _deterministic_idempotency_key("chg", "a")
        _reset_counter()
        key2 = _deterministic_idempotency_key("chg", "a")
        assert key1 == key2, "Keys must be identical given same counter state and params"

    def test_thread_safety(self) -> None:
        """Multiple threads should never produce duplicate keys."""
        results: list[str] = []
        lock = threading.Lock()

        def generate(n: int) -> None:
            keys = [_deterministic_idempotency_key("t", "x") for _ in range(n)]
            with lock:
                results.extend(keys)

        threads = [threading.Thread(target=generate, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 200
        assert len(set(results)) == 200, "Duplicate keys detected across threads"
