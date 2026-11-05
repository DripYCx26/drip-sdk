"""
Tests for the resilience module: rate limiting, retry, circuit breaker, metrics.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from drip import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
    MetricsCollector,
    RateLimiter,
    RateLimiterConfig,
    RequestMetrics,
    ResilienceConfig,
    ResilienceManager,
    RetryConfig,
    RetryExhausted,
    calculate_backoff,
    with_retry,
    with_retry_async,
)

# =============================================================================
# Rate Limiter Tests
# =============================================================================


class TestRateLimiter:
    def test_allows_burst(self) -> None:
        """Rate limiter should allow burst up to burst_size."""
        limiter = RateLimiter(RateLimiterConfig(
            requests_per_second=10,
            burst_size=5,
        ))

        # Should allow 5 requests immediately (burst)
        for _ in range(5):
            assert limiter.acquire(timeout=0.01)

    def test_limits_after_burst(self) -> None:
        """Rate limiter should throttle after burst is exhausted."""
        limiter = RateLimiter(RateLimiterConfig(
            requests_per_second=100,
            burst_size=3,
        ))

        # Exhaust burst
        for _ in range(3):
            limiter.acquire(timeout=0.01)

        # Next request should need to wait
        start = time.perf_counter()
        limiter.acquire(timeout=1.0)
        elapsed = time.perf_counter() - start

        # Should have waited ~10ms (1/100 per second)
        assert elapsed >= 0.005

    def test_disabled_limiter(self) -> None:
        """Disabled rate limiter should allow all requests."""
        limiter = RateLimiter(RateLimiterConfig(enabled=False))

        # Should allow unlimited requests instantly
        for _ in range(100):
            assert limiter.acquire(timeout=0.001)

    def test_thread_safety(self) -> None:
        """Rate limiter should be thread-safe."""
        limiter = RateLimiter(RateLimiterConfig(
            requests_per_second=1000,
            burst_size=100,
        ))

        acquired = []

        def worker():
            result = limiter.acquire(timeout=1.0)
            acquired.append(result)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            for f in futures:
                f.result()

        assert len(acquired) == 50
        assert all(acquired)

    @pytest.mark.asyncio
    async def test_async_acquire(self) -> None:
        """Async rate limiter should work correctly."""
        limiter = RateLimiter(RateLimiterConfig(
            requests_per_second=100,
            burst_size=5,
        ))

        results = []
        for _ in range(5):
            result = await limiter.acquire_async(timeout=0.1)
            results.append(result)

        assert all(results)


# =============================================================================
# Retry Tests
# =============================================================================


class TestRetry:
    def test_successful_no_retry(self) -> None:
        """Successful calls should not retry."""
        call_count = 0

        @with_retry(RetryConfig(max_retries=3))
        def successful_call():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_call()
        assert result == "success"
        assert call_count == 1

    def test_retries_on_retryable_exception(self) -> None:
        """Should retry on retryable exceptions."""
        call_count = 0

        @with_retry(RetryConfig(
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        ))
        def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection failed")
            return "success"

        result = flaky_call()
        assert result == "success"
        assert call_count == 3

    def test_raises_after_max_retries(self) -> None:
        """Should raise RetryExhausted after max retries."""
        call_count = 0

        @with_retry(RetryConfig(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        ))
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always fails")

        with pytest.raises(RetryExhausted) as exc_info:
            always_fails()

        assert exc_info.value.attempts == 3  # Initial + 2 retries
        assert call_count == 3

    def test_no_retry_on_non_retryable(self) -> None:
        """Should not retry non-retryable exceptions."""
        call_count = 0

        @with_retry(RetryConfig(
            max_retries=3,
            retryable_exceptions=(ConnectionError,),
        ))
        def raises_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            raises_value_error()

        assert call_count == 1

    def test_backoff_calculation(self) -> None:
        """Backoff should increase exponentially."""
        config = RetryConfig(
            base_delay=0.1,
            exponential_base=2.0,
            max_delay=10.0,
            jitter=0,
        )

        assert calculate_backoff(0, config) == 0.1
        assert calculate_backoff(1, config) == 0.2
        assert calculate_backoff(2, config) == 0.4
        assert calculate_backoff(3, config) == 0.8

    def test_backoff_max_limit(self) -> None:
        """Backoff should not exceed max_delay."""
        config = RetryConfig(
            base_delay=1.0,
            exponential_base=2.0,
            max_delay=5.0,
            jitter=0,
        )

        assert calculate_backoff(10, config) == 5.0

    @pytest.mark.asyncio
    async def test_async_retry(self) -> None:
        """Async retry should work correctly."""
        call_count = 0

        @with_retry_async(RetryConfig(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        ))
        async def flaky_async():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("failed")
            return "success"

        result = await flaky_async()
        assert result == "success"
        assert call_count == 2


# =============================================================================
# Circuit Breaker Tests
# =============================================================================


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        """Circuit breaker should start in closed state."""
        cb = CircuitBreaker("test", CircuitBreakerConfig())
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_failures(self) -> None:
        """Circuit should open after threshold failures."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=3,
        ))

        # Record failures
        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN

    def test_rejects_when_open(self) -> None:
        """Open circuit should reject requests."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
        ))

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allow_request()

    def test_transitions_to_half_open(self) -> None:
        """Circuit should transition to half-open after timeout."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            timeout=0.05,  # 50ms timeout
        ))

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.1)  # Wait for timeout
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_after_success_in_half_open(self) -> None:
        """Circuit should close after successes in half-open."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            success_threshold=2,
            timeout=0.01,
        ))

        cb.record_failure()
        time.sleep(0.05)  # Wait longer to ensure half-open

        # Verify we're in half-open
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # Still need one more

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self) -> None:
        """Circuit should reopen on failure in half-open."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            timeout=0.01,
        ))

        cb.record_failure()
        time.sleep(0.02)  # Enter half-open
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_as_decorator(self) -> None:
        """Circuit breaker should work as decorator."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=2,
        ))

        @cb
        def may_fail(should_fail: bool):
            if should_fail:
                raise Exception("failed")
            return "success"

        # Successful calls
        assert may_fail(False) == "success"
        assert may_fail(False) == "success"

        # Failing calls
        with pytest.raises(Exception, match="failed"):
            may_fail(True)
        with pytest.raises(Exception, match="failed"):
            may_fail(True)

        # Circuit should be open now
        with pytest.raises(CircuitBreakerOpen):
            may_fail(False)


# =============================================================================
# Metrics Tests
# =============================================================================


class TestMetricsCollector:
    def test_records_metrics(self) -> None:
        """Should record request metrics."""
        collector = MetricsCollector()

        collector.record(RequestMetrics(
            method="POST",
            endpoint="/usage",
            status_code=200,
            duration_ms=50.0,
            success=True,
        ))

        summary = collector.get_summary()
        assert summary["total_requests"] == 1
        assert summary["total_successes"] == 1
        assert summary["success_rate"] == 100.0

    def test_calculates_percentiles(self) -> None:
        """Should calculate latency percentiles."""
        collector = MetricsCollector()

        # Record 100 requests with varying latencies
        for i in range(100):
            collector.record(RequestMetrics(
                method="POST",
                endpoint="/usage",
                status_code=200,
                duration_ms=float(i + 1),  # 1-100ms
                success=True,
            ))

        summary = collector.get_summary()
        # Percentiles are approximate based on index calculation
        assert 49 <= summary["p50_latency_ms"] <= 52
        assert 94 <= summary["p95_latency_ms"] <= 96
        assert 98 <= summary["p99_latency_ms"] <= 100

    def test_tracks_errors(self) -> None:
        """Should track errors by type."""
        collector = MetricsCollector()

        collector.record(RequestMetrics(
            method="POST",
            endpoint="/usage",
            status_code=500,
            duration_ms=100.0,
            success=False,
            error="DripAPIError",
        ))
        collector.record(RequestMetrics(
            method="POST",
            endpoint="/usage",
            status_code=429,
            duration_ms=50.0,
            success=False,
            error="DripRateLimitError",
        ))

        summary = collector.get_summary()
        assert summary["total_failures"] == 2
        assert summary["errors_by_type"]["DripAPIError"] == 1
        assert summary["errors_by_type"]["DripRateLimitError"] == 1

    def test_window_size_limit(self) -> None:
        """Should respect window size limit."""
        collector = MetricsCollector(window_size=10)

        # Record more than window size
        for i in range(20):
            collector.record(RequestMetrics(
                method="POST",
                endpoint="/usage",
                status_code=200,
                duration_ms=float(i),
                success=True,
            ))

        summary = collector.get_summary()
        assert summary["window_size"] == 10
        assert summary["total_requests"] == 20  # Total still tracked

    def test_reset(self) -> None:
        """Should reset all metrics."""
        collector = MetricsCollector()

        collector.record(RequestMetrics(
            method="POST",
            endpoint="/usage",
            status_code=200,
            duration_ms=50.0,
            success=True,
        ))

        collector.reset()
        summary = collector.get_summary()
        assert summary["total_requests"] == 0


# =============================================================================
# Resilience Manager Tests
# =============================================================================


class TestResilienceManager:
    def test_executes_successfully(self) -> None:
        """Should execute function with all resilience features."""
        manager = ResilienceManager(ResilienceConfig.default())

        result = manager.execute(
            lambda: "success",
            method="POST",
            endpoint="/test",
        )

        assert result == "success"

    def test_retries_on_failure(self) -> None:
        """Should retry failed requests."""
        config = ResilienceConfig(
            rate_limiter=RateLimiterConfig(enabled=False),
            retry=RetryConfig(
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(ConnectionError,),
            ),
            circuit_breaker=CircuitBreakerConfig(enabled=False),
        )
        manager = ResilienceManager(config)

        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("failed")
            return "success"

        result = manager.execute(flaky, method="POST", endpoint="/test")
        assert result == "success"
        assert call_count == 2

    def test_circuit_breaker_integration(self) -> None:
        """Circuit breaker should integrate with manager."""
        config = ResilienceConfig(
            rate_limiter=RateLimiterConfig(enabled=False),
            retry=RetryConfig(enabled=False),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=2,
            ),
        )
        manager = ResilienceManager(config)

        def always_fails():
            raise Exception("failed")

        # Trigger circuit breaker
        for _ in range(2):
            with pytest.raises(Exception, match="failed"):
                manager.execute(always_fails)

        # Circuit should be open
        with pytest.raises(CircuitBreakerOpen):
            manager.execute(lambda: "test")

    def test_collects_metrics(self) -> None:
        """Should collect metrics for requests."""
        manager = ResilienceManager(ResilienceConfig.default())

        manager.execute(lambda: "success", method="POST", endpoint="/usage")
        manager.execute(lambda: "success", method="GET", endpoint="/customers")

        metrics = manager.get_metrics()
        assert metrics is not None
        assert metrics["total_requests"] == 2
        assert metrics["requests_by_endpoint"]["/usage"] == 1
        assert metrics["requests_by_endpoint"]["/customers"] == 1

    def test_health_status(self) -> None:
        """Should provide health status."""
        manager = ResilienceManager(ResilienceConfig.default())

        health = manager.get_health()
        assert health["circuit_breaker"]["state"] == "closed"
        assert health["rate_limiter"]["available_tokens"] > 0

    @pytest.mark.asyncio
    async def test_async_execute(self) -> None:
        """Should work with async functions."""
        manager = ResilienceManager(ResilienceConfig.default())

        async def async_call():
            return "async success"

        result = await manager.execute_async(
            async_call,
            method="POST",
            endpoint="/test",
        )

        assert result == "async success"


class TestResilienceConfig:
    def test_default_config(self) -> None:
        """Default config should have sensible values."""
        config = ResilienceConfig.default()

        assert config.rate_limiter.enabled
        assert config.retry.enabled
        assert config.circuit_breaker.enabled
        assert config.collect_metrics

    def test_disabled_config(self) -> None:
        """Disabled config should turn off all features."""
        config = ResilienceConfig.disabled()

        assert not config.rate_limiter.enabled
        assert not config.retry.enabled
        assert not config.circuit_breaker.enabled
        assert not config.collect_metrics

    def test_high_throughput_config(self) -> None:
        """High throughput config should have optimized values."""
        config = ResilienceConfig.high_throughput()

        assert config.rate_limiter.requests_per_second == 1000.0
        assert config.rate_limiter.burst_size == 2000
        assert config.retry.max_retries == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
