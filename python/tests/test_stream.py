"""Tests for StreamMeter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from drip.models import ChargeInfo, ChargeResult, ChargeStatus
from drip.stream import StreamMeter, StreamMeterOptions


@pytest.fixture
def mock_charge_result():
    """Create a mock charge result."""
    return ChargeResult(
        success=True,
        usage_event_id="usage_123",
        is_duplicate=False,
        charge=ChargeInfo(
            id="chg_123",
            amount_usdc="0.001000",
            amount_token="1000000000000000",
            tx_hash="0x123abc",
            status=ChargeStatus.CONFIRMED,
        ),
    )


@pytest.fixture
def create_meter(mock_charge_result):
    """Factory fixture to create meters with custom options."""
    def _create(options=None, charge_fn=None):
        if charge_fn is None:
            charge_fn = MagicMock(return_value=mock_charge_result)

        full_options = StreamMeterOptions(
            customer_id="cust_123",
            meter="tokens",
            **(options or {}),
        )
        return StreamMeter(_charge_fn=charge_fn, _options=full_options), charge_fn
    return _create


class TestAddSync:
    """Tests for add_sync method."""

    def test_accumulates_quantity(self, create_meter):
        """Should accumulate quantity."""
        meter, _ = create_meter()

        meter.add_sync(10)
        assert meter.total == 10

        meter.add_sync(5)
        assert meter.total == 15

    def test_ignores_non_positive_quantities(self, create_meter):
        """Should ignore non-positive quantities."""
        meter, _ = create_meter()

        meter.add_sync(0)
        assert meter.total == 0

        meter.add_sync(-5)
        assert meter.total == 0

    def test_calls_on_add_callback(self, create_meter):
        """Should call on_add callback."""
        on_add = MagicMock()
        meter, _ = create_meter({"on_add": on_add})

        meter.add_sync(10)
        on_add.assert_called_with(10, 10)

        meter.add_sync(5)
        on_add.assert_called_with(5, 15)


class TestFlush:
    """Tests for flush method."""

    def test_charges_with_accumulated_quantity(self, create_meter):
        """Should charge with accumulated quantity."""
        meter, charge_fn = create_meter()

        meter.add_sync(100)
        result = meter.flush()

        charge_fn.assert_called_once_with(
            customer_id="cust_123",
            meter="tokens",
            quantity=100,
            idempotency_key=None,
            metadata=None,
        )
        assert result.success is True
        assert result.quantity == 100
        assert result.charge.amount_usdc == "0.001000"

    def test_resets_total_after_flush(self, create_meter):
        """Should reset total after flush."""
        meter, _ = create_meter()

        meter.add_sync(100)
        meter.flush()

        assert meter.total == 0

    def test_returns_success_with_null_charge_when_total_is_zero(self, create_meter):
        """Should return success with null charge when total is 0."""
        meter, charge_fn = create_meter()

        result = meter.flush()

        charge_fn.assert_not_called()
        assert result.success is True
        assert result.quantity == 0
        assert result.charge is None

    def test_includes_idempotency_key_with_flush_count(self, create_meter):
        """Should include idempotency key with flush count."""
        meter, charge_fn = create_meter({"idempotency_key": "stream_123"})

        meter.add_sync(50)
        meter.flush()

        call_args = charge_fn.call_args
        assert call_args.kwargs["idempotency_key"] == "stream_123_flush_0"

        meter.add_sync(50)
        meter.flush()

        call_args = charge_fn.call_args
        assert call_args.kwargs["idempotency_key"] == "stream_123_flush_1"

    def test_includes_metadata(self, create_meter):
        """Should include metadata."""
        metadata = {"model": "gpt-4"}
        meter, charge_fn = create_meter({"metadata": metadata})

        meter.add_sync(100)
        meter.flush()

        call_args = charge_fn.call_args
        assert call_args.kwargs["metadata"] == {"model": "gpt-4"}

    def test_calls_on_flush_callback(self, create_meter):
        """Should call on_flush callback."""
        on_flush = MagicMock()
        meter, _ = create_meter({"on_flush": on_flush})

        meter.add_sync(100)
        meter.flush()

        on_flush.assert_called_once()
        result = on_flush.call_args[0][0]
        assert result.success is True
        assert result.quantity == 100

    def test_updates_is_flushed_and_flush_count(self, create_meter):
        """Should update is_flushed and flush_count."""
        meter, _ = create_meter()

        assert meter.is_flushed is False
        assert meter.flush_count == 0

        meter.add_sync(100)
        meter.flush()

        assert meter.is_flushed is True
        assert meter.flush_count == 1

        meter.add_sync(50)
        meter.flush()

        assert meter.flush_count == 2


class TestFlushFailurePreservesUsage:
    """Tests that usage is not lost when a charge call fails."""

    def test_usage_preserved_on_charge_exception(self, create_meter):
        """If charge_fn raises, accumulated usage must not be lost."""
        charge_fn = MagicMock(side_effect=RuntimeError("network timeout"))
        meter, _ = create_meter(charge_fn=charge_fn)

        meter.add_sync(80)
        with pytest.raises(RuntimeError, match="network timeout"):
            meter.flush()

        # Usage must still be present so it can be retried
        assert meter.total == 80

    def test_concurrent_add_during_flush_preserved(self, create_meter, mock_charge_result):
        """Usage added between snapshot and charge completion must not be lost."""
        added_during_flush: list[float] = []

        def charge_fn_with_side_effect(**kwargs):
            # Simulate usage arriving while the charge call is in-flight
            meter.add_sync(25)
            added_during_flush.append(25)
            return mock_charge_result

        meter, _ = create_meter(charge_fn=MagicMock(side_effect=charge_fn_with_side_effect))

        meter.add_sync(100)
        result = meter.flush()

        assert result.success is True
        assert result.quantity == 100
        # The 25 added during the charge call must be preserved
        assert meter.total == 25


class TestReset:
    """Tests for reset method."""

    def test_resets_total_without_charging(self, create_meter):
        """Should reset total to 0 without charging."""
        meter, charge_fn = create_meter()

        meter.add_sync(100)
        meter.reset()

        assert meter.total == 0
        charge_fn.assert_not_called()


class TestDuplicateDetection:
    """Tests for duplicate detection."""

    def test_passes_through_is_duplicate(self, create_meter, mock_charge_result):
        """Should pass through is_duplicate from charge result."""
        replay_result = ChargeResult(
            success=True,
            usage_event_id="usage_123",
            is_duplicate=True,
            charge=mock_charge_result.charge,
        )
        meter, _ = create_meter(charge_fn=MagicMock(return_value=replay_result))

        meter.add_sync(100)
        result = meter.flush()

        assert result.is_duplicate is True


@pytest.mark.asyncio
class TestAsyncFlush:
    """Tests for async flush_async method."""

    async def test_charges_with_accumulated_quantity(self, create_meter, mock_charge_result):
        """Should charge with accumulated quantity (async)."""
        async_charge_fn = AsyncMock(return_value=mock_charge_result)
        meter, _ = create_meter(charge_fn=async_charge_fn)

        meter.add_sync(100)
        result = await meter.flush_async()

        async_charge_fn.assert_called_once()
        assert result.success is True
        assert result.quantity == 100

    async def test_auto_flush_on_threshold(self, create_meter, mock_charge_result):
        """Should auto-flush when threshold is reached."""
        async_charge_fn = AsyncMock(return_value=mock_charge_result)
        meter, _ = create_meter(
            {"flush_threshold": 100},
            charge_fn=async_charge_fn,
        )

        # Add below threshold
        await meter.add(50)
        async_charge_fn.assert_not_called()
        assert meter.total == 50

        # Add to exceed threshold
        result = await meter.add(60)
        async_charge_fn.assert_called_once()
        assert meter.total == 0
        assert result.success is True
