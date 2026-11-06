"""
Drip middleware for Python web frameworks.

This module provides middleware adapters for popular Python web frameworks
including FastAPI and Flask.

FastAPI:
    >>> from drip.middleware.fastapi import DripMiddleware, get_drip_context
    >>>
    >>> app = FastAPI()
    >>> app.add_middleware(DripMiddleware, meter="api_calls", quantity=1)

Flask:
    >>> from drip.middleware.flask import drip_middleware, get_drip_context
    >>>
    >>> @app.route("/api/endpoint")
    >>> @drip_middleware(meter="api_calls", quantity=1)
    >>> def endpoint():
    ...     return {"success": True}
"""

from .core import (
    BILLING_IDENTITY_HEADERS,
    create_async_drip_client,
    create_drip_client,
    generate_payment_request,
    get_header,
    has_payment_proof_headers,
    parse_payment_proof,
    process_request_async,
    process_request_sync,
    resolve_customer_id_async,
    resolve_customer_id_sync,
)
from .types import (
    DripContext,
    DripMiddlewareConfig,
    PaymentRequestHeaders,
    ProcessRequestResult,
)

__all__ = [
    # Security constants
    "BILLING_IDENTITY_HEADERS",
    # Core functions
    "get_header",
    "has_payment_proof_headers",
    "parse_payment_proof",
    "generate_payment_request",
    "resolve_customer_id_sync",
    "resolve_customer_id_async",
    "process_request_sync",
    "process_request_async",
    "create_drip_client",
    "create_async_drip_client",
    # Types
    "DripContext",
    "DripMiddlewareConfig",
    "PaymentRequestHeaders",
    "ProcessRequestResult",
]
