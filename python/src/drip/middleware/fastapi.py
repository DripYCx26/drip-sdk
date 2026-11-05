"""
Drip middleware for FastAPI.

This module provides FastAPI-specific middleware and dependency injection
for automatic usage billing.

Example:
    >>> from fastapi import FastAPI, Request, Depends
    >>> from drip.middleware.fastapi import drip_middleware, get_drip_context, DripContext
    >>>
    >>> def resolve_customer(request: Request) -> str:
    ...     # Must resolve from verified auth (JWT/session/API key lookup)
    ...     return verify_jwt(request.headers["authorization"])["drip_customer_id"]
    >>>
    >>> app = FastAPI()
    >>>
    >>> # Apply middleware
    >>> app.add_middleware(
    ...     DripMiddleware,
    ...     meter="api_calls",
    ...     quantity=1,
    ...     customer_resolver=resolve_customer,
    ... )
    >>>
    >>> # Or use dependency injection
    >>> @app.post("/api/generate")
    >>> async def generate(drip: DripContext = Depends(get_drip_context)):
    ...     print(f"Charged: {drip.charge.charge.amount_usdc} USDC")
    ...     return {"success": True}
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from ..errors import DripMiddlewareError, DripMiddlewareErrorCode
from .core import (
    BILLING_IDENTITY_HEADERS,
    get_header,
    has_payment_proof_headers,
    process_request_async,
)
from .types import DripContext, DripMiddlewareConfig

# Type stubs for when FastAPI is not installed
if TYPE_CHECKING:
    from fastapi import Request, Response
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.types import ASGIApp

try:
    from fastapi.responses import JSONResponse as FastAPIJSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware as StarletteBaseMiddleware

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPIJSONResponse: Any = None  # type: ignore[no-redef]
    StarletteBaseMiddleware: type = object  # type: ignore[no-redef]


# Request state key for storing drip context
DRIP_CONTEXT_KEY = "drip_context"

F = TypeVar("F", bound=Callable[..., Any])


def _ensure_fastapi() -> None:
    """Raise error if FastAPI is not installed."""
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI is required for this middleware. "
            "Install it with: pip install drip-sdk[fastapi]"
        )


class DripMiddleware(StarletteBaseMiddleware):  # type: ignore[misc]
    """
    FastAPI/Starlette middleware for automatic Drip billing.

    This middleware intercepts requests and charges customers based on
    the configured meter and quantity before passing to route handlers.

    Example:
        >>> from fastapi import FastAPI
        >>> from drip.middleware.fastapi import DripMiddleware
        >>>
        >>> def resolve_customer(request: Request) -> str:
        ...     return verify_jwt(request.headers["authorization"])["drip_customer_id"]
        >>>
        >>> app = FastAPI()
        >>> app.add_middleware(
        ...     DripMiddleware,
        ...     meter="api_calls",
        ...     quantity=1,
        ...     customer_resolver=resolve_customer,
        ... )
    """

    def __init__(
        self,
        app: ASGIApp,
        meter: str,
        quantity: float | Callable[[Request], float | Awaitable[float]],
        customer_resolver: Callable[[Request], str | Awaitable[str]],
        api_key: str | None = None,
        base_url: str | None = None,
        idempotency_key: Callable[[Request, str], str | Awaitable[str]] | None = None,
        on_error: Callable[[Exception, Request], Any] | None = None,
        on_charge: Callable[[Any, Request], Any] | None = None,
        skip_in_development: bool = False,
        metadata: dict[str, Any] | Callable[[Request], dict[str, Any]] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> None:
        """
        Initialize the middleware.

        Args:
            app: The ASGI application.
            meter: Usage meter type (e.g., "api_calls").
            quantity: Static quantity or callable to compute dynamically.
            api_key: API key (defaults to DRIP_API_KEY env var).
            base_url: API base URL.
            customer_resolver: Callable that resolves customer ID from authenticated request.
            idempotency_key: Optional function to generate idempotency keys.
            on_error: Optional error callback.
            on_charge: Optional success callback.
            skip_in_development: Skip charging in development mode.
            metadata: Static metadata or callable.
            exclude_paths: Paths to exclude from billing.
        """
        _ensure_fastapi()
        super().__init__(app)

        self.config = DripMiddlewareConfig(
            meter=meter,
            quantity=quantity,
            api_key=api_key,
            base_url=base_url,
            customer_resolver=customer_resolver,
            idempotency_key=idempotency_key,
            on_error=on_error,
            on_charge=on_charge,
            skip_in_development=skip_in_development,
            metadata=metadata,
        )
        self.exclude_paths = exclude_paths or []

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request through the middleware."""
        # Check if path should be excluded
        path = request.url.path
        for exclude in self.exclude_paths:
            if path.startswith(exclude):
                return await call_next(request)

        # Strip billing identity headers to prevent spoofing (VERIA-14).
        # These headers must never be trusted from clients.
        blocked = {h.lower().encode() for h in BILLING_IDENTITY_HEADERS}
        request.scope["headers"] = [
            (k, v) for k, v in request.scope["headers"] if k.lower() not in blocked
        ]

        # Process the request
        result = await process_request_async(request, self.config)

        if result.payment_required and result.payment_headers:
            # Return 402 Payment Required
            return FastAPIJSONResponse(
                status_code=402,
                content={
                    "error": "Payment Required",
                    "paymentRequest": result.payment_request.model_dump(by_alias=True)
                    if result.payment_request
                    else None,
                },
                headers=result.payment_headers.to_dict(),
            )

        if result.error:
            error = result.error
            status_code = getattr(error, "status_code", 500)
            return FastAPIJSONResponse(
                status_code=status_code,
                content={
                    "error": str(error),
                    "code": getattr(error, "code", "INTERNAL_ERROR"),
                },
            )

        if result.context:
            # Attach context to request state
            request.state.drip_context = result.context

        return await call_next(request)


def get_drip_context(request: Request) -> DripContext:
    """
    FastAPI dependency to get Drip context from request.

    Example:
        >>> from fastapi import Depends
        >>> from drip.middleware.fastapi import get_drip_context, DripContext
        >>>
        >>> @app.post("/api/generate")
        >>> async def generate(drip: DripContext = Depends(get_drip_context)):
        ...     print(f"Customer: {drip.customer_id}")
        ...     return {"charged": drip.charge.charge.amount_usdc}

    Raises:
        ValueError: If Drip context is not available (middleware not applied).
    """
    _ensure_fastapi()

    context: DripContext | None = getattr(request.state, DRIP_CONTEXT_KEY, None)
    if context is None:
        raise ValueError(
            "Drip context not found. Ensure DripMiddleware is applied to this route."
        )
    return context


def has_drip_context(request: Request) -> bool:
    """
    Check if request has Drip context attached.

    Args:
        request: The FastAPI request.

    Returns:
        True if context is available.
    """
    _ensure_fastapi()
    return hasattr(request.state, DRIP_CONTEXT_KEY)


def with_drip(
    meter: str,
    quantity: float | Callable[[Request], float | Awaitable[float]],
    customer_resolver: Callable[[Request], str | Awaitable[str]],
    api_key: str | None = None,
    base_url: str | None = None,
    idempotency_key: Callable[[Request, str], str | Awaitable[str]] | None = None,
    on_error: Callable[[Exception, Request], Any] | None = None,
    on_charge: Callable[[Any, Request], Any] | None = None,
    skip_in_development: bool = False,
    metadata: dict[str, Any] | Callable[[Request], dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    """
    Decorator for FastAPI route handlers with Drip billing.

    This decorator wraps a route handler to automatically charge
    customers before processing the request.

    Example:
        >>> from fastapi import FastAPI, Request
        >>> from drip.middleware.fastapi import with_drip
        >>>
        >>> app = FastAPI()
        >>>
        >>> @app.post("/api/generate")
        >>> @with_drip(meter="api_calls", quantity=1, customer_resolver=resolve_customer)
        >>> async def generate(request: Request):
        ...     drip = request.state.drip_context
        ...     return {"charged": drip.charge.charge.amount_usdc}

    Args:
        meter: Usage meter type.
        quantity: Static quantity or callable.
        api_key: API key.
        base_url: API base URL.
        customer_resolver: Customer resolution method.
        idempotency_key: Idempotency key generator.
        on_error: Error callback.
        on_charge: Success callback.
        skip_in_development: Skip in dev mode.
        metadata: Request metadata.

    Returns:
        Decorator function.
    """
    _ensure_fastapi()

    config = DripMiddlewareConfig(
        meter=meter,
        quantity=quantity,
        api_key=api_key,
        base_url=base_url,
        customer_resolver=customer_resolver,
        idempotency_key=idempotency_key,
        on_error=on_error,
        on_charge=on_charge,
        skip_in_development=skip_in_development,
        metadata=metadata,
    )

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
            # Strip billing identity headers to prevent spoofing (VERIA-14)
            blocked = {h.lower().encode() for h in BILLING_IDENTITY_HEADERS}
            request.scope["headers"] = [
                (k, v) for k, v in request.scope["headers"] if k.lower() not in blocked
            ]

            # Process the request
            result = await process_request_async(request, config)

            if result.payment_required and result.payment_headers:
                return FastAPIJSONResponse(
                    status_code=402,
                    content={
                        "error": "Payment Required",
                        "paymentRequest": result.payment_request.model_dump(by_alias=True)
                        if result.payment_request
                        else None,
                    },
                    headers=result.payment_headers.to_dict(),
                )

            if result.error:
                error = result.error
                status_code = getattr(error, "status_code", 500)
                return FastAPIJSONResponse(
                    status_code=status_code,
                    content={
                        "error": str(error),
                        "code": getattr(error, "code", "INTERNAL_ERROR"),
                    },
                )

            if result.context:
                request.state.drip_context = result.context

            return await func(request, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def create_drip_dependency(
    meter: str,
    quantity: float | Callable[[Request], float | Awaitable[float]],
    customer_resolver: Callable[[Request], str | Awaitable[str]],
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> Callable[[Request], Awaitable[DripContext]]:
    """
    Create a FastAPI dependency that charges on each request.

    This is an alternative to middleware when you need per-route configuration.

    Example:
        >>> from fastapi import FastAPI, Depends
        >>> from drip.middleware.fastapi import create_drip_dependency
        >>>
        >>> charge_api_call = create_drip_dependency(
        ...     meter="api_calls",
        ...     quantity=1,
        ...     customer_resolver=resolve_customer,
        ... )
        >>>
        >>> @app.post("/api/generate")
        >>> async def generate(drip: DripContext = Depends(charge_api_call)):
        ...     return {"charged": drip.charge.charge.amount_usdc}

    Args:
        meter: Usage meter type.
        quantity: Static quantity or callable.
        api_key: API key.
        base_url: API base URL.
        customer_resolver: Customer resolution method.
        **kwargs: Additional config options.

    Returns:
        FastAPI dependency function.
    """
    _ensure_fastapi()

    config = DripMiddlewareConfig(
        meter=meter,
        quantity=quantity,
        api_key=api_key,
        base_url=base_url,
        customer_resolver=customer_resolver,
        **kwargs,
    )

    async def dependency(request: Request) -> DripContext:
        # Strip billing identity headers to prevent spoofing (VERIA-14)
        blocked = {h.lower().encode() for h in BILLING_IDENTITY_HEADERS}
        request.scope["headers"] = [
            (k, v) for k, v in request.scope["headers"] if k.lower() not in blocked
        ]

        result = await process_request_async(request, config)

        if result.payment_required:
            raise DripMiddlewareError(
                "Payment required",
                DripMiddlewareErrorCode.PAYMENT_REQUIRED,
                status_code=402,
                details={
                    "paymentRequest": result.payment_request.model_dump(by_alias=True)
                    if result.payment_request
                    else None
                },
            )

        if result.error:
            raise result.error

        if result.context is None:
            raise DripMiddlewareError(
                "Failed to process request",
                DripMiddlewareErrorCode.INTERNAL_ERROR,
                status_code=500,
            )

        return result.context

    return dependency


# Re-export types for convenience
__all__ = [
    "DripMiddleware",
    "DripContext",
    "DripMiddlewareConfig",
    "get_drip_context",
    "has_drip_context",
    "with_drip",
    "create_drip_dependency",
    "has_payment_proof_headers",
    "get_header",
]
