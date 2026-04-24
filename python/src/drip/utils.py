"""
Drip SDK utility functions.

This module provides utility functions for idempotency key generation,
webhook signature verification, and other common operations.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import IdempotencyKeyParams


def generate_idempotency_key(
    customer_id: str,
    step_name: str,
    run_id: str | None = None,
    sequence: int | None = None,
) -> str:
    """
    Generate a deterministic idempotency key.

    Ensures "one logical action = one event" even with retries.
    The key is a SHA-256 hash of the input parameters.

    Args:
        customer_id: The customer ID.
        step_name: The name of the step/action.
        run_id: Optional run ID for scoping.
        sequence: Optional sequence number for ordered steps.

    Returns:
        A deterministic idempotency key string.

    Example:
        >>> key = generate_idempotency_key(
        ...     customer_id="cus_123",
        ...     step_name="process_tokens",
        ...     run_id="run_456",
        ...     sequence=1
        ... )
        >>> print(key)  # Consistent hash
    """
    parts = [customer_id]

    if run_id:
        parts.append(run_id)

    parts.append(step_name)

    if sequence is not None:
        parts.append(str(sequence))

    key_input = ":".join(parts)
    return hashlib.sha256(key_input.encode()).hexdigest()


def generate_idempotency_key_from_params(params: IdempotencyKeyParams) -> str:
    """
    Generate idempotency key from params object.

    Args:
        params: IdempotencyKeyParams with customer_id, step_name, etc.

    Returns:
        A deterministic idempotency key string.
    """
    return generate_idempotency_key(
        customer_id=params.customer_id,
        step_name=params.step_name,
        run_id=params.run_id,
        sequence=params.sequence,
    )


def verify_webhook_signature(
    payload: str,
    signature: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """
    Verify a webhook signature using HMAC-SHA256.

    Uses timing-safe comparison to prevent timing attacks.

    Args:
        payload: The raw request body as a string.
        signature: The signature from the X-Drip-Signature header (format: t=timestamp,v1=hexsignature).
        secret: The webhook secret.
        tolerance: Maximum age of signature in seconds (default 5 minutes).

    Returns:
        True if the signature is valid, False otherwise.

    Example:
        >>> is_valid = verify_webhook_signature(
        ...     payload='{"event": "charge.succeeded"}',
        ...     signature="t=1234567890,v1=abc123...",
        ...     secret="whsec_..."
        ... )
    """
    if not payload or not signature or not secret:
        return False

    try:
        # Parse signature format: t=timestamp,v1=hexsignature
        parts = signature.split(",")
        timestamp_part = next((p for p in parts if p.startswith("t=")), None)
        signature_part = next((p for p in parts if p.startswith("v1=")), None)

        if not timestamp_part or not signature_part:
            return False

        timestamp = int(timestamp_part[2:])
        provided_signature = signature_part[3:]

        # Check timestamp tolerance
        import time

        now = int(time.time())
        if abs(now - timestamp) > tolerance:
            return False

        # Compute expected signature using timestamp.payload format
        signature_payload = f"{timestamp}.{payload}"
        expected = hmac.new(
            key=secret.encode("utf-8"),
            msg=signature_payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        # Timing-safe comparison
        return hmac.compare_digest(expected, provided_signature)
    except (ValueError, AttributeError):
        return False


def generate_webhook_signature(
    payload: str,
    secret: str,
    timestamp: int | None = None,
) -> str:
    """
    Generate a webhook signature for testing purposes.

    Creates a signature in the same format the Drip backend uses,
    allowing you to test your webhook handling code locally.

    Args:
        payload: The webhook payload (JSON string).
        secret: The webhook secret.
        timestamp: Optional timestamp (defaults to current time).

    Returns:
        Signature in format: t=timestamp,v1=hexsignature

    Example:
        >>> signature = generate_webhook_signature(
        ...     payload='{"type": "charge.succeeded", "data": {...}}',
        ...     secret="whsec_test123"
        ... )
        >>> is_valid = verify_webhook_signature(payload, signature, secret)
        >>> print(is_valid)  # True
    """
    import time

    ts = timestamp if timestamp is not None else int(time.time())
    signature_payload = f"{ts}.{payload}"
    sig = hmac.new(
        key=secret.encode("utf-8"),
        msg=signature_payload.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={sig}"


def generate_nonce(length: int = 32) -> str:
    """
    Generate a cryptographically secure random nonce.

    Args:
        length: The length of the nonce in bytes (hex string will be 2x).

    Returns:
        A hex-encoded random string.
    """
    return secrets.token_hex(length)


def current_timestamp() -> int:
    """
    Get the current Unix timestamp in seconds.

    Returns:
        Current Unix timestamp.
    """
    return int(time.time())


def current_timestamp_ms() -> int:
    """
    Get the current Unix timestamp in milliseconds.

    Returns:
        Current Unix timestamp in milliseconds.
    """
    return int(time.time() * 1000)


def is_valid_hex(value: str) -> bool:
    """
    Check if a string is a valid hexadecimal value.

    Args:
        value: The string to check.

    Returns:
        True if the string is valid hex, False otherwise.
    """
    if not value:
        return False

    # Remove 0x prefix if present
    if value.startswith("0x") or value.startswith("0X"):
        value = value[2:]

    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def normalize_address(address: str) -> str:
    """
    Normalize an Ethereum address to lowercase with 0x prefix.

    Args:
        address: The address to normalize.

    Returns:
        Normalized address with 0x prefix in lowercase.

    Raises:
        ValueError: If the address is not valid.
    """
    if not address:
        raise ValueError("Address cannot be empty")

    # Remove 0x prefix for processing
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]

    # Validate hex and length (20 bytes = 40 hex chars)
    if len(clean) != 40 or not is_valid_hex(clean):
        raise ValueError(f"Invalid Ethereum address: {address}")

    return f"0x{clean}"


def format_usdc_amount(amount_wei: int | str) -> str:
    """
    Format a USDC amount from wei (6 decimals) to a human-readable string.

    Args:
        amount_wei: Amount in the smallest unit (6 decimals).

    Returns:
        Formatted string like "$1.23".
    """
    if isinstance(amount_wei, str):
        amount_wei = int(amount_wei)

    # USDC has 6 decimals
    amount_dollars = amount_wei / 1_000_000
    return f"${amount_dollars:.2f}"


def parse_usdc_amount(amount_str: str) -> int:
    """
    Parse a USDC amount string to wei (6 decimals).

    Args:
        amount_str: Amount as a string, e.g., "1.23" or "$1.23".

    Returns:
        Amount in the smallest unit (6 decimals).
    """
    # Remove $ prefix if present
    clean = amount_str.strip()
    if clean.startswith("$"):
        clean = clean[1:]

    # Parse and convert to wei
    amount_dollars = float(clean)
    return int(amount_dollars * 1_000_000)
