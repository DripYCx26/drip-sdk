"""
StreamMeter - Accumulate usage locally and charge once at the end.

Perfect for LLM token streaming and other high-frequency metering scenarios
where you want to avoid making an API call for every small increment.

Example:
    >>> from drip import Drip
    >>>
    >>> client = Drip(api_key="sk_test_...")
    >>> meter = client.create_stream_meter(
    ...     customer_id=customer.id,
    ...     meter="tokens",
    ... )
    >>>
    >>> for chunk in llm_stream:
    ...     meter.add_sync(chunk.tokens)
    ...
    >>> result = meter.flush()
    >>> print(f"Charged {result.charge.amount_usdc} for {result.quantity} tokens")

Async Example:
    >>> async with AsyncDrip(api_key="sk_test_...") as client:
    ...     meter = client.create_stream_meter(
    ...         customer_id=customer.id,
    ...         meter="tokens",
    ...     )
    ...
    ...     async for chunk in llm_stream:
    ...         await meter.add(chunk.tokens)  # May auto-flush
    ...
    ...     result = await meter.flush()
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .models import ChargeInfo, ChargeResult

if TYPE_CHECKING:
    pass


@dataclass
class StreamMeterOptions:
    """Options for creating a StreamMeter."""

    customer_id: str
    """The Drip customer ID to charge."""

    meter: str
    """The usage meter/type to record against."""

    idempotency_key: str | None = None
    """Unique key to prevent duplicate charges. If not provided, one will be generated."""

    metadata: dict[str, object] | None = None
    """Additional metadata to attach to the charge."""

    flush_threshold: float | None = None
    """Auto-flush when accumulated quantity reaches this threshold."""

    on_add: Callable[[float, float], None] | None = None
    """Callback invoked on each add() call with (quantity, total)."""

    on_flush: Callable[[StreamMeterFlushResult], None] | None = None
    """Callback invoked after each successful flush."""


@dataclass
class StreamMeterFlushResult:
    """Result of flushing a StreamMeter."""

    success: bool
    """Whether the flush was successful."""

    quantity: float
    """The quantity that was charged."""

    charge: ChargeInfo | None
    """The charge result from the API (if quantity > 0)."""

    is_duplicate: bool
    """Whether this was a duplicate request matched by idempotencyKey."""


class SyncChargeFn(Protocol):
    """Protocol for synchronous charge function."""

    def __call__(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ChargeResult: ...


class AsyncChargeFn(Protocol):
    """Protocol for asynchronous charge function."""

    def __call__(
        self,
        customer_id: str,
        meter: str,
        quantity: float,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Awaitable[ChargeResult]: ...


ChargeFn = SyncChargeFn | AsyncChargeFn


@dataclass
class StreamMeter:
    """
    StreamMeter accumulates usage locally and charges once when flushed.

    This is ideal for:
    - LLM token streaming (charge once at end of stream)
    - High-frequency events (batch small increments)
    - Partial failure handling (charge for what was delivered)

    The same class works for both sync and async clients - just use
    the appropriate methods (flush vs flush_async, add vs add_async).
    """

    _charge_fn: ChargeFn
    _options: StreamMeterOptions
    _total: float = field(default=0.0, init=False)
    _flushed: bool = field(default=False, init=False)
    _flush_count: int = field(default=0, init=False)

    @property
    def total(self) -> float:
        """Current accumulated quantity (not yet charged)."""
        return self._total

    @property
    def is_flushed(self) -> bool:
        """Whether this meter has been flushed at least once."""
        return self._flushed

    @property
    def flush_count(self) -> int:
        """Number of times this meter has been flushed."""
        return self._flush_count

    def add_sync(self, quantity: float) -> None:
        """
        Synchronously add quantity without auto-flush.

        Use this for maximum performance when you don't need threshold-based flushing.

        Args:
            quantity: Amount to add (must be positive)
        """
        if quantity <= 0:
            return

        self._total += quantity

        # Invoke callback if provided
        if self._options.on_add:
            self._options.on_add(quantity, self._total)

    async def add(self, quantity: float) -> StreamMeterFlushResult | None:
        """
        Add quantity to the accumulated total (async).

        If a flush_threshold is set and the total exceeds it,
        this will automatically trigger a flush.

        Args:
            quantity: Amount to add (must be positive)

        Returns:
            Flush result if auto-flush was triggered, None otherwise.
        """
        if quantity <= 0:
            return None

        self._total += quantity

        # Invoke callback if provided
        if self._options.on_add:
            self._options.on_add(quantity, self._total)

        # Check for auto-flush threshold
        if (
            self._options.flush_threshold is not None
            and self._total >= self._options.flush_threshold
        ):
            return await self.flush_async()

        return None

    def flush(self) -> StreamMeterFlushResult:
        """
        Flush accumulated usage and charge the customer (sync).

        If total is 0, returns a success result with no charge.
        After flush, usage that was successfully charged is subtracted,
        preserving any usage added concurrently during the charge call.

        Returns:
            The flush result including charge details.
        """
        quantity = self._total

        # Nothing to charge
        if quantity == 0:
            return StreamMeterFlushResult(
                success=True,
                quantity=0,
                charge=None,
                is_duplicate=False,
            )

        # Increment flush count BEFORE the charge call so that retries
        # get a new idempotency key. If we only incremented on success,
        # a failed flush followed by a retry would reuse the same key
        # and the backend would deduplicate it — silently losing the charge.
        idempotency_key = None
        if self._options.idempotency_key:
            idempotency_key = f"{self._options.idempotency_key}_flush_{self._flush_count}"
        self._flush_count += 1

        # Charge the customer
        charge_result = self._charge_fn(
            customer_id=self._options.customer_id,
            meter=self._options.meter,
            quantity=quantity,
            idempotency_key=idempotency_key,
            metadata=self._options.metadata,
        )

        # Handle if this is actually an awaitable (async client)
        if hasattr(charge_result, "__await__"):
            raise RuntimeError(
                "Cannot use sync flush() with async client. Use flush_async() instead."
            )

        if not charge_result.success:
            raise RuntimeError("StreamMeter flush failed: charge was not accepted")

        self._flushed = True

        # Subtract charged amount, preserving usage added while charge was in flight
        self._total = max(0.0, self._total - quantity)

        result = StreamMeterFlushResult(
            success=charge_result.success,
            quantity=quantity,
            charge=charge_result.charge,
            is_duplicate=charge_result.is_duplicate,
        )

        # Invoke callback if provided
        if self._options.on_flush:
            self._options.on_flush(result)

        return result

    async def flush_async(self) -> StreamMeterFlushResult:
        """
        Flush accumulated usage and charge the customer (async).

        If total is 0, returns a success result with no charge.
        After flush, usage that was successfully charged is subtracted,
        preserving any usage added concurrently during the charge call.

        Returns:
            The flush result including charge details.
        """
        quantity = self._total

        # Nothing to charge
        if quantity == 0:
            return StreamMeterFlushResult(
                success=True,
                quantity=0,
                charge=None,
                is_duplicate=False,
            )

        # Increment flush count BEFORE the charge call so that retries
        # get a new idempotency key (same rationale as sync flush).
        idempotency_key = None
        if self._options.idempotency_key:
            idempotency_key = f"{self._options.idempotency_key}_flush_{self._flush_count}"
        self._flush_count += 1

        # Charge the customer
        charge_result = self._charge_fn(
            customer_id=self._options.customer_id,
            meter=self._options.meter,
            quantity=quantity,
            idempotency_key=idempotency_key,
            metadata=self._options.metadata,
        )

        # Await if this is a coroutine
        if hasattr(charge_result, "__await__"):
            charge_result = await charge_result

        if not charge_result.success:
            raise RuntimeError("StreamMeter flush failed: charge was not accepted")

        self._flushed = True

        # Subtract charged amount, preserving usage added while charge was in flight
        self._total = max(0.0, self._total - quantity)

        result = StreamMeterFlushResult(
            success=charge_result.success,
            quantity=quantity,
            charge=charge_result.charge,
            is_duplicate=charge_result.is_duplicate,
        )

        # Invoke callback if provided
        if self._options.on_flush:
            self._options.on_flush(result)

        return result

    def reset(self) -> None:
        """
        Reset the meter without charging.

        Use this to discard accumulated usage (e.g., on error before delivery).
        """
        self._total = 0
