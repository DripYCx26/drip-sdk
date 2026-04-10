"""
Production-grade resilience patterns for the Drip SDK.

This module provides:
- Rate limiting (token bucket algorithm)
- Retry with exponential backoff
- Circuit breaker pattern
- Request metrics and observability
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger("drip.resilience")

P = ParamSpec("P")
T = TypeVar("T")


# =============================================================================
# Rate Limiter (Token Bucket Algorithm)
# =============================================================================


@dataclass
class RateLimiterConfig:
    """Configuration for rate limiting."""

    requests_per_second: float = 100.0
    """Maximum requests per second."""

    burst_size: int = 200
    """Maximum burst size (bucket capacity)."""

    enabled: bool = True
    """Whether rate limiting is enabled."""


class RateLimiter:
    """
    Thread-safe token bucket rate limiter.

    Allows bursting up to `burst_size` requests, then limits to
    `requests_per_second` sustained rate.
    """

    def __init__(self, config: RateLimiterConfig | None = None) -> None:
        self.config = config or RateLimiterConfig()
        self._tokens = float(self.config.burst_size)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.config.burst_size,
            self._tokens + elapsed * self.config.requests_per_second
        )
        self._last_refill = now

    def acquire(self, timeout: float | None = None) -> bool:
        """
        Acquire a token, blocking if necessary.

        Args:
            timeout: Maximum time to wait for a token (None = wait forever)

        Returns:
            True if token acquired, False if timeout
        """
        if not self.config.enabled:
            return True

        deadline = time.monotonic() + timeout if timeout else None

        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True

                # Calculate wait time for next token
                wait_time = (1 - self._tokens) / self.config.requests_per_second

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            time.sleep(wait_time)

    async def acquire_async(self, timeout: float | None = None) -> bool:
        """Async version of acquire."""
        if not self.config.enabled:
            return True

        if self._async_lock is None:
            self._async_lock = asyncio.Lock()

        deadline = time.monotonic() + timeout if timeout else None

        while True:
            async with self._async_lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True

                wait_time = (1 - self._tokens) / self.config.requests_per_second

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            await asyncio.sleep(wait_time)

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens."""
        with self._lock:
            self._refill()
            return self._tokens


# =============================================================================
# Retry with Exponential Backoff
# =============================================================================


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    """Maximum number of retry attempts."""

    base_delay: float = 0.1
    """Base delay in seconds."""

    max_delay: float = 10.0
    """Maximum delay in seconds."""

    exponential_base: float = 2.0
    """Exponential backoff base."""

    jitter: float = 0.1
    """Random jitter factor (0-1)."""

    retryable_exceptions: tuple[type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
    )
    """Exception types that should trigger retry."""

    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)
    """HTTP status codes that should trigger retry."""

    enabled: bool = True
    """Whether retry is enabled."""


class RetryExhausted(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, attempts: int, last_exception: Exception) -> None:
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(f"Retry exhausted after {attempts} attempts: {last_exception}")


def calculate_backoff(attempt: int, config: RetryConfig) -> float:
    """Calculate backoff delay for a given attempt."""
    delay = config.base_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)

    # Add jitter
    if config.jitter > 0:
        jitter_range = delay * config.jitter
        delay += random.uniform(-jitter_range, jitter_range)

    return max(0, delay)


def with_retry(config: RetryConfig | None = None) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that adds retry logic to a function.

    Example:
        @with_retry(RetryConfig(max_retries=3))
        def call_api():
            ...
    """
    _config = config or RetryConfig()

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not _config.enabled:
                return func(*args, **kwargs)

            last_exception: Exception | None = None

            for attempt in range(_config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except _config.retryable_exceptions as e:
                    last_exception = e
                    if attempt < _config.max_retries:
                        delay = calculate_backoff(attempt, _config)
                        logger.warning(
                            f"Retry attempt {attempt + 1}/{_config.max_retries} "
                            f"after {delay:.2f}s: {e}"
                        )
                        time.sleep(delay)
                    else:
                        raise RetryExhausted(attempt + 1, e) from e
                except Exception as e:
                    # Check if it's an HTTP error with retryable status code
                    status_code = getattr(e, "status_code", None)
                    if status_code in _config.retryable_status_codes:
                        last_exception = e
                        if attempt < _config.max_retries:
                            delay = calculate_backoff(attempt, _config)
                            logger.warning(
                                f"Retry attempt {attempt + 1}/{_config.max_retries} "
                                f"after {delay:.2f}s (status {status_code}): {e}"
                            )
                            time.sleep(delay)
                            continue
                    raise

            if last_exception:
                raise RetryExhausted(_config.max_retries + 1, last_exception)
            raise RuntimeError("Unexpected retry loop exit")

        return wrapper

    return decorator


def with_retry_async(config: RetryConfig | None = None) -> Callable[
    [Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]
]:
    """Async version of with_retry decorator."""
    _config = config or RetryConfig()

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not _config.enabled:
                return await func(*args, **kwargs)

            last_exception: Exception | None = None

            for attempt in range(_config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except _config.retryable_exceptions as e:
                    last_exception = e
                    if attempt < _config.max_retries:
                        delay = calculate_backoff(attempt, _config)
                        logger.warning(
                            f"Retry attempt {attempt + 1}/{_config.max_retries} "
                            f"after {delay:.2f}s: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise RetryExhausted(attempt + 1, e) from e
                except Exception as e:
                    status_code = getattr(e, "status_code", None)
                    if status_code in _config.retryable_status_codes:
                        last_exception = e
                        if attempt < _config.max_retries:
                            delay = calculate_backoff(attempt, _config)
                            logger.warning(
                                f"Retry attempt {attempt + 1}/{_config.max_retries} "
                                f"after {delay:.2f}s (status {status_code}): {e}"
                            )
                            await asyncio.sleep(delay)
                            continue
                    raise

            if last_exception:
                raise RetryExhausted(_config.max_retries + 1, last_exception)
            raise RuntimeError("Unexpected retry loop exit")

        return wrapper

    return decorator


# =============================================================================
# Circuit Breaker
# =============================================================================


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5
    """Number of failures before opening circuit."""

    success_threshold: int = 2
    """Number of successes in half-open to close circuit."""

    timeout: float = 30.0
    """Seconds to wait before transitioning from open to half-open."""

    enabled: bool = True
    """Whether circuit breaker is enabled."""


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""

    def __init__(self, circuit_name: str, time_until_retry: float) -> None:
        self.circuit_name = circuit_name
        self.time_until_retry = time_until_retry
        super().__init__(
            f"Circuit '{circuit_name}' is open. Retry in {time_until_retry:.1f}s"
        )


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by failing fast when a service is unhealthy.
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        with self._lock:
            self._check_state_transition()
            return self._state

    def _check_state_transition(self) -> None:
        """Check if state should transition based on timeout."""
        if self._state == CircuitState.OPEN and self._last_failure_time:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.config.timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(f"Circuit '{self.name}' transitioning to HALF_OPEN")

    def record_success(self) -> None:
        """Record a successful call."""
        if not self.config.enabled:
            return

        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(f"Circuit '{self.name}' closed after recovery")
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        if not self.config.enabled:
            return

        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open returns to open
                self._state = CircuitState.OPEN
                logger.warning(f"Circuit '{self.name}' re-opened after failure in HALF_OPEN")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit '{self.name}' opened after {self._failure_count} failures"
                    )

    def allow_request(self) -> bool:
        """Check if a request should be allowed."""
        if not self.config.enabled:
            return True

        with self._lock:
            self._check_state_transition()

            # Allow requests if CLOSED or HALF_OPEN (test request), deny if OPEN
            return self._state != CircuitState.OPEN

    def get_time_until_retry(self) -> float:
        """Get seconds until circuit transitions to half-open."""
        with self._lock:
            if self._state != CircuitState.OPEN or not self._last_failure_time:
                return 0
            elapsed = time.monotonic() - self._last_failure_time
            return max(0, self.config.timeout - elapsed)

    def __call__(self, func: Callable[P, T]) -> Callable[P, T]:
        """Use as decorator."""
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not self.allow_request():
                raise CircuitBreakerOpen(self.name, self.get_time_until_retry())

            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception:
                self.record_failure()
                raise

        return wrapper


# =============================================================================
# Metrics and Observability
# =============================================================================


@dataclass
class RequestMetrics:
    """Metrics for a single request."""

    method: str
    endpoint: str
    status_code: int | None
    duration_ms: float
    success: bool
    timestamp: float = field(default_factory=time.time)
    error: str | None = None
    retry_count: int = 0


class MetricsCollector:
    """
    Collects and aggregates request metrics.

    Thread-safe metrics collection with windowed aggregation.
    """

    def __init__(self, window_size: int = 1000) -> None:
        """
        Initialize metrics collector.

        Args:
            window_size: Number of recent requests to keep for aggregation
        """
        self._metrics: deque[RequestMetrics] = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_successes = 0
        self._total_failures = 0

    def record(self, metrics: RequestMetrics) -> None:
        """Record a request's metrics."""
        with self._lock:
            self._metrics.append(metrics)
            self._total_requests += 1
            if metrics.success:
                self._total_successes += 1
            else:
                self._total_failures += 1

    def get_summary(self) -> dict[str, Any]:
        """Get aggregated metrics summary."""
        with self._lock:
            if not self._metrics:
                return {
                    "total_requests": 0,
                    "success_rate": 0.0,
                    "avg_latency_ms": 0.0,
                    "p50_latency_ms": 0.0,
                    "p95_latency_ms": 0.0,
                    "p99_latency_ms": 0.0,
                    "requests_by_endpoint": {},
                    "errors_by_type": {},
                }

            latencies = sorted(m.duration_ms for m in self._metrics)
            successes = sum(1 for m in self._metrics if m.success)

            # Group by endpoint
            by_endpoint: dict[str, int] = {}
            for m in self._metrics:
                by_endpoint[m.endpoint] = by_endpoint.get(m.endpoint, 0) + 1

            # Group errors
            errors: dict[str, int] = {}
            for m in self._metrics:
                if m.error:
                    errors[m.error] = errors.get(m.error, 0) + 1

            return {
                "window_size": len(self._metrics),
                "total_requests": self._total_requests,
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "success_rate": successes / len(self._metrics) * 100,
                "avg_latency_ms": sum(latencies) / len(latencies),
                "min_latency_ms": latencies[0],
                "max_latency_ms": latencies[-1],
                "p50_latency_ms": latencies[len(latencies) // 2],
                "p95_latency_ms": latencies[int(len(latencies) * 0.95)],
                "p99_latency_ms": latencies[int(len(latencies) * 0.99)],
                "requests_by_endpoint": by_endpoint,
                "errors_by_type": errors,
            }

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._metrics.clear()
            self._total_requests = 0
            self._total_successes = 0
            self._total_failures = 0


# =============================================================================
# Combined Resilience Manager
# =============================================================================


@dataclass
class ResilienceConfig:
    """Combined configuration for all resilience features."""

    rate_limiter: RateLimiterConfig = field(default_factory=RateLimiterConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    collect_metrics: bool = True

    @classmethod
    def default(cls) -> ResilienceConfig:
        """Create default production configuration."""
        return cls(
            rate_limiter=RateLimiterConfig(
                requests_per_second=100.0,
                burst_size=200,
                enabled=True,
            ),
            retry=RetryConfig(
                max_retries=3,
                base_delay=0.1,
                max_delay=10.0,
                enabled=True,
            ),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=5,
                success_threshold=2,
                timeout=30.0,
                enabled=True,
            ),
            collect_metrics=True,
        )

    @classmethod
    def disabled(cls) -> ResilienceConfig:
        """Create configuration with all features disabled."""
        return cls(
            rate_limiter=RateLimiterConfig(enabled=False),
            retry=RetryConfig(enabled=False),
            circuit_breaker=CircuitBreakerConfig(enabled=False),
            collect_metrics=False,
        )

    @classmethod
    def high_throughput(cls) -> ResilienceConfig:
        """Configuration optimized for high throughput."""
        return cls(
            rate_limiter=RateLimiterConfig(
                requests_per_second=1000.0,
                burst_size=2000,
                enabled=True,
            ),
            retry=RetryConfig(
                max_retries=2,
                base_delay=0.05,
                max_delay=5.0,
                enabled=True,
            ),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=10,
                success_threshold=3,
                timeout=15.0,
                enabled=True,
            ),
            collect_metrics=True,
        )


class ResilienceManager:
    """
    Manages all resilience features for the SDK.

    Provides a unified interface for rate limiting, retry, circuit breaker,
    and metrics collection.
    """

    def __init__(self, config: ResilienceConfig | None = None) -> None:
        self.config = config or ResilienceConfig.default()
        self.rate_limiter = RateLimiter(self.config.rate_limiter)
        self.circuit_breaker = CircuitBreaker("drip_api", self.config.circuit_breaker)
        self.metrics = MetricsCollector() if self.config.collect_metrics else None
        self._retry_config = self.config.retry

    def execute(
        self,
        func: Callable[[], T],
        method: str = "UNKNOWN",
        endpoint: str = "unknown",
    ) -> T:
        """
        Execute a function with all resilience features.

        Args:
            func: The function to execute
            method: HTTP method for metrics
            endpoint: Endpoint for metrics

        Returns:
            Result of the function
        """
        start_time = time.perf_counter()
        retry_count = 0
        last_error: str | None = None

        # Rate limiting
        if not self.rate_limiter.acquire(timeout=30.0):
            raise TimeoutError("Rate limiter timeout")

        # Circuit breaker check
        if not self.circuit_breaker.allow_request():
            raise CircuitBreakerOpen(
                self.circuit_breaker.name,
                self.circuit_breaker.get_time_until_retry()
            )

        # Execute with retry
        last_exception: Exception | None = None

        for attempt in range(self._retry_config.max_retries + 1):
            try:
                result = func()
                self.circuit_breaker.record_success()

                # Record success metrics
                if self.metrics:
                    duration = (time.perf_counter() - start_time) * 1000
                    self.metrics.record(RequestMetrics(
                        method=method,
                        endpoint=endpoint,
                        status_code=200,
                        duration_ms=duration,
                        success=True,
                        retry_count=retry_count,
                    ))

                return result

            except Exception as e:
                last_exception = e
                last_error = type(e).__name__

                # Check if retryable
                status_code = getattr(e, "status_code", None)
                is_retryable = (
                    isinstance(e, self._retry_config.retryable_exceptions) or
                    status_code in self._retry_config.retryable_status_codes
                )

                if is_retryable and attempt < self._retry_config.max_retries:
                    retry_count += 1
                    delay = calculate_backoff(attempt, self._retry_config)
                    logger.warning(f"Retry {attempt + 1}: {e}")
                    time.sleep(delay)
                    continue

                # Not retryable or exhausted retries
                self.circuit_breaker.record_failure()

                # Record failure metrics
                if self.metrics:
                    duration = (time.perf_counter() - start_time) * 1000
                    self.metrics.record(RequestMetrics(
                        method=method,
                        endpoint=endpoint,
                        status_code=status_code,
                        duration_ms=duration,
                        success=False,
                        error=last_error,
                        retry_count=retry_count,
                    ))

                raise

        # Should not reach here
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected execution path")

    async def execute_async(
        self,
        func: Callable[[], Awaitable[T]],
        method: str = "UNKNOWN",
        endpoint: str = "unknown",
    ) -> T:
        """Async version of execute."""
        start_time = time.perf_counter()
        retry_count = 0
        last_error: str | None = None

        # Rate limiting
        if not await self.rate_limiter.acquire_async(timeout=30.0):
            raise TimeoutError("Rate limiter timeout")

        # Circuit breaker check
        if not self.circuit_breaker.allow_request():
            raise CircuitBreakerOpen(
                self.circuit_breaker.name,
                self.circuit_breaker.get_time_until_retry()
            )

        last_exception: Exception | None = None

        for attempt in range(self._retry_config.max_retries + 1):
            try:
                result = await func()
                self.circuit_breaker.record_success()

                if self.metrics:
                    duration = (time.perf_counter() - start_time) * 1000
                    self.metrics.record(RequestMetrics(
                        method=method,
                        endpoint=endpoint,
                        status_code=200,
                        duration_ms=duration,
                        success=True,
                        retry_count=retry_count,
                    ))

                return result

            except Exception as e:
                last_exception = e
                last_error = type(e).__name__

                status_code = getattr(e, "status_code", None)
                is_retryable = (
                    isinstance(e, self._retry_config.retryable_exceptions) or
                    status_code in self._retry_config.retryable_status_codes
                )

                if is_retryable and attempt < self._retry_config.max_retries:
                    retry_count += 1
                    delay = calculate_backoff(attempt, self._retry_config)
                    logger.warning(f"Retry {attempt + 1}: {e}")
                    await asyncio.sleep(delay)
                    continue

                self.circuit_breaker.record_failure()

                if self.metrics:
                    duration = (time.perf_counter() - start_time) * 1000
                    self.metrics.record(RequestMetrics(
                        method=method,
                        endpoint=endpoint,
                        status_code=status_code,
                        duration_ms=duration,
                        success=False,
                        error=last_error,
                        retry_count=retry_count,
                    ))

                raise

        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected execution path")

    def get_metrics(self) -> dict[str, Any] | None:
        """Get current metrics summary."""
        return self.metrics.get_summary() if self.metrics else None

    def get_health(self) -> dict[str, Any]:
        """Get health status of all resilience components."""
        return {
            "circuit_breaker": {
                "state": self.circuit_breaker.state.value,
                "time_until_retry": self.circuit_breaker.get_time_until_retry(),
            },
            "rate_limiter": {
                "available_tokens": self.rate_limiter.available_tokens,
                "requests_per_second": self.config.rate_limiter.requests_per_second,
            },
            "metrics": self.get_metrics(),
        }
