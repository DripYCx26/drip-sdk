"""Tests for utility functions."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from drip.utils import (
    current_timestamp,
    current_timestamp_ms,
    format_usdc_amount,
    generate_idempotency_key,
    generate_nonce,
    generate_webhook_signature,
    is_valid_hex,
    normalize_address,
    parse_usdc_amount,
    verify_webhook_signature,
)


class TestIdempotencyKey:
    def test_generate_basic_key(self) -> None:
        """Should generate a key from customer_id and step_name."""
        key = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process_tokens",
        )
        assert len(key) == 64  # SHA-256 hex digest

    def test_generate_key_with_run_id(self) -> None:
        """Should include run_id in key generation."""
        key1 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_456",
        )
        key2 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_789",
        )
        assert key1 != key2

    def test_generate_key_with_sequence(self) -> None:
        """Should include sequence in key generation."""
        key1 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            sequence=1,
        )
        key2 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            sequence=2,
        )
        assert key1 != key2

    def test_key_is_deterministic(self) -> None:
        """Same inputs should produce same key."""
        key1 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_456",
            sequence=1,
        )
        key2 = generate_idempotency_key(
            customer_id="cus_123",
            step_name="process",
            run_id="run_456",
            sequence=1,
        )
        assert key1 == key2


class TestWebhookSignature:
    def test_verify_valid_signature(self) -> None:
        """Should verify valid signatures using t=timestamp,v1=signature format."""
        secret = "whsec_test123"
        payload = '{"event": "test"}'
        # Use the generate_webhook_signature function to create proper format
        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_reject_invalid_signature(self) -> None:
        """Should reject invalid signatures."""
        assert verify_webhook_signature("payload", "t=123,v1=invalid", "secret") is False

    def test_reject_malformed_signature(self) -> None:
        """Should reject malformed signatures missing required parts."""
        secret = "whsec_test123"
        payload = '{"event": "test"}'
        # Missing v1= part
        assert verify_webhook_signature(payload, "t=123", secret) is False
        # Missing t= part
        assert verify_webhook_signature(payload, "v1=abc123", secret) is False

    def test_reject_empty_values(self) -> None:
        """Should reject empty values."""
        assert verify_webhook_signature("", "sig", "secret") is False
        assert verify_webhook_signature("payload", "", "secret") is False
        assert verify_webhook_signature("payload", "sig", "") is False

    def test_roundtrip_generate_and_verify(self) -> None:
        """Should be able to generate and verify signatures."""
        secret = "whsec_test123"
        payload = '{"event": "charge.succeeded", "data": {"id": "chg_123"}}'
        signature = generate_webhook_signature(payload, secret)
        assert verify_webhook_signature(payload, signature, secret) is True


class TestNonce:
    def test_generate_nonce_default_length(self) -> None:
        """Should generate 32-byte nonce by default."""
        nonce = generate_nonce()
        assert len(nonce) == 64  # 32 bytes = 64 hex chars

    def test_generate_nonce_custom_length(self) -> None:
        """Should generate nonce with custom length."""
        nonce = generate_nonce(16)
        assert len(nonce) == 32  # 16 bytes = 32 hex chars

    def test_nonce_is_unique(self) -> None:
        """Each nonce should be unique."""
        nonces = [generate_nonce() for _ in range(100)]
        assert len(set(nonces)) == 100


class TestTimestamp:
    def test_current_timestamp(self) -> None:
        """Should return current Unix timestamp in seconds."""
        ts = current_timestamp()
        assert isinstance(ts, int)
        assert ts > 1700000000  # After 2023

    def test_current_timestamp_ms(self) -> None:
        """Should return current Unix timestamp in milliseconds."""
        ts = current_timestamp_ms()
        assert isinstance(ts, int)
        assert ts > 1700000000000  # After 2023 in ms


class TestHexValidation:
    def test_valid_hex(self) -> None:
        """Should accept valid hex strings."""
        assert is_valid_hex("abc123") is True
        assert is_valid_hex("0xabc123") is True
        assert is_valid_hex("ABC123") is True
        assert is_valid_hex("0XABC123") is True

    def test_invalid_hex(self) -> None:
        """Should reject invalid hex strings."""
        assert is_valid_hex("") is False
        assert is_valid_hex("xyz") is False
        assert is_valid_hex("0xghi") is False


class TestAddressNormalization:
    def test_normalize_lowercase(self) -> None:
        """Should normalize to lowercase with 0x prefix."""
        address = normalize_address("0xABCDEF1234567890ABCDEF1234567890ABCDEF12")
        assert address == "0xabcdef1234567890abcdef1234567890abcdef12"

    def test_normalize_without_prefix(self) -> None:
        """Should add 0x prefix if missing."""
        address = normalize_address("abcdef1234567890abcdef1234567890abcdef12")
        assert address == "0xabcdef1234567890abcdef1234567890abcdef12"

    def test_reject_invalid_address(self) -> None:
        """Should reject invalid addresses."""
        with pytest.raises(ValueError):
            normalize_address("")

        with pytest.raises(ValueError):
            normalize_address("0x123")  # Too short

        with pytest.raises(ValueError):
            normalize_address("0xZZZZ567890abcdef1234567890abcdef12345678")  # Invalid chars


class TestUSDCFormatting:
    def test_format_usdc_amount(self) -> None:
        """Should format USDC amounts correctly."""
        assert format_usdc_amount(1000000) == "$1.00"
        assert format_usdc_amount(1500000) == "$1.50"
        assert format_usdc_amount(100) == "$0.00"
        assert format_usdc_amount("2500000") == "$2.50"

    def test_parse_usdc_amount(self) -> None:
        """Should parse USDC strings correctly."""
        assert parse_usdc_amount("1.00") == 1000000
        assert parse_usdc_amount("$1.50") == 1500000
        assert parse_usdc_amount("0.01") == 10000
