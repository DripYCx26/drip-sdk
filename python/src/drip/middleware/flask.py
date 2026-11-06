"""
Drip middleware for Flask.

This module provides Flask-specific middleware and decorators
for automatic usage billing.

Example:
    >>> from flask import Flask, g
    >>> from drip.middleware.flask import drip_middleware, get_drip_context
    >>>
    >>> def resolve_customer(request):
    ...     # Must resolve from verified auth (JWT/session/API key lookup)
    ...     session = verify_session(request.cookies.get("session"))
    ...     return session["drip_customer_id"]
    >>>
    >>> app = Flask(__name__)
    >>>
    >>> # Apply to specific routes
    >>> @app.route("/api/generate", methods=["POST"])
    >>> @drip_middleware(
    ...     meter="api_calls",
    ...     quantity=1,
    ...     customer_resolver=resolve_customer,
    ... )
    >>> def generate():
    ...     drip = get_drip_context()
    ...     return {"charged": drip.charge.charge.amount_usdc}
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from .core import (
    BILLING_IDENTITY_HEADERS,
    get_header,
    has_payment_proof_headers,
    process_request_sync,
)
from .types import DripContext, DripMiddlewareConfig


def _strip_billing_identity_headers() -> None:
    """Strip billing identity headers from Flask's request environ to prevent spoofing (VERIA-14)."""
    for header in BILLING_IDENTITY_HEADERS:
        # Flask/Werkzeug stores headers as HTTP_<UPPERCASED_DASHES_TO_UNDERSCORES>
        environ_key = "HTTP_" + header.upper().replace("-", "_")
        flask_request.environ.pop(environ_key, None)

# Flask type stubs for when Flask is not installed
if TYPE_CHECKING:
    from flask import Flask, Request

try:
    from flask import g as flask_g
    from flask import jsonify as flask_jsonify
    from flask import request as flask_request

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    flask_g: Any = None  # type: ignore[no-redef]
    flask_jsonify: Any = None  # type: ignore[no-redef]
    flask_request: Any = None  # type: ignore[no-redef]


# Flask g key for storing drip context
DRIP_CONTEXT_KEY = "drip_context"

F = TypeVar("F", bound=Callable[..., Any])


def _ensure_flask() -> None:
    """Raise error if Flask is not installed."""
    if not FLASK_AVAILABLE:
        raise ImportError(
            "Flask is required for this middleware. "
            "Install it with: pip install drip-sdk[flask]"
        )


class DripFlaskMiddleware:
    """
    Flask extension for automatic Drip billing.

    This extension can be applied to all routes or specific blueprints.

    Example:
        >>> from flask import Flask
        >>> from drip.middleware.flask import DripFlaskMiddleware
        >>>
        >>> app = Flask(__name__)
        >>> drip = DripFlaskMiddleware(app, meter="api_calls", quantity=1)
    """

    def __init__(
        self,
        app: Flask | None = None,
        *,
        meter: str = "",
        quantity: float | Callable[[Request], float] = 1,
        customer_resolver: Callable[[Request], str],
        api_key: str | None = None,
        base_url: str | None = None,
        idempotency_key: Callable[[Request, str], str] | None = None,
        on_error: Callable[[Exception, Request], Any] | None = None,
        on_charge: Callable[[Any, Request], Any] | None = None,
        skip_in_development: bool = False,
        metadata: dict[str, Any] | Callable[[Request], dict[str, Any]] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> None:
        """
        Initialize the Flask extension.

        Args:
            app: Flask application (optional, can use init_app later).
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
            exclude_paths: Paths to exclude from billing.
        """
        _ensure_flask()

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

        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """
        Initialize the extension with a Flask app.

        Args:
            app: Flask application.
        """
        app.before_request(self._before_request)

    def _before_request(self) -> Any:
        """Process request before route handler."""
        # Check if path should be excluded
        path = flask_request.path
        for exclude in self.exclude_paths:
            if path.startswith(exclude):
                return None

        # Skip if meter not configured
        if not self.config.meter:
            return None

        # Strip billing identity headers before processing (VERIA-14)
        _strip_billing_identity_headers()

        # Process the request
        result = process_request_sync(flask_request, self.config)

        if result.payment_required and result.payment_headers:
            response = flask_jsonify({
                "error": "Payment Required",
                "paymentRequest": result.payment_request.model_dump(by_alias=True)
                if result.payment_request
                else None,
            })
            response.status_code = 402
            for key, value in result.payment_headers.to_dict().items():
                response.headers[key] = value
            return response

        if result.error:
            error = result.error
            status_code = getattr(error, "status_code", 500)
            response = flask_jsonify({
                "error": str(error),
                "code": getattr(error, "code", "INTERNAL_ERROR"),
            })
            response.status_code = status_code
            return response

        if result.context:
            flask_g.drip_context = result.context

        return None


def drip_middleware(
    meter: str,
    quantity: float | Callable[[Request], float],
    customer_resolver: Callable[[Request], str],
    api_key: str | None = None,
    base_url: str | None = None,
    idempotency_key: Callable[[Request, str], str] | None = None,
    on_error: Callable[[Exception, Request], Any] | None = None,
    on_charge: Callable[[Any, Request], Any] | None = None,
    skip_in_development: bool = False,
    metadata: dict[str, Any] | Callable[[Request], dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    """
    Decorator for Flask route handlers with Drip billing.

    This decorator wraps a route handler to automatically charge
    customers before processing the request.

    Example:
        >>> from flask import Flask
        >>> from drip.middleware.flask import drip_middleware, get_drip_context
        >>>
        >>> app = Flask(__name__)
        >>>
        >>> @app.route("/api/generate", methods=["POST"])
        >>> @drip_middleware(meter="api_calls", quantity=1)
        >>> def generate():
        ...     drip = get_drip_context()
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
    _ensure_flask()

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
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Strip billing identity headers before processing (VERIA-14)
            _strip_billing_identity_headers()

            # Process the request
            result = process_request_sync(flask_request, config)

            if result.payment_required and result.payment_headers:
                response = flask_jsonify({
                    "error": "Payment Required",
                    "paymentRequest": result.payment_request.model_dump(by_alias=True)
                    if result.payment_request
                    else None,
                })
                response.status_code = 402
                for key, value in result.payment_headers.to_dict().items():
                    response.headers[key] = value
                return response

            if result.error:
                error = result.error
                status_code = getattr(error, "status_code", 500)
                response = flask_jsonify({
                    "error": str(error),
                    "code": getattr(error, "code", "INTERNAL_ERROR"),
                })
                response.status_code = status_code
                return response

            if result.context:
                flask_g.drip_context = result.context

            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def get_drip_context() -> DripContext:
    """
    Get Drip context from Flask's g object.

    Call this in your route handler to access charge information.

    Example:
        >>> @app.route("/api/generate", methods=["POST"])
        >>> @drip_middleware(meter="api_calls", quantity=1)
        >>> def generate():
        ...     drip = get_drip_context()
        ...     print(f"Customer: {drip.customer_id}")
        ...     print(f"Charged: {drip.charge.charge.amount_usdc}")
        ...     return {"success": True}

    Returns:
        DripContext with charge information.

    Raises:
        ValueError: If context is not available.
    """
    _ensure_flask()

    context: DripContext | None = getattr(flask_g, DRIP_CONTEXT_KEY, None)
    if context is None:
        raise ValueError(
            "Drip context not found. Ensure drip_middleware decorator is applied."
        )
    return context


def has_drip_context() -> bool:
    """
    Check if Drip context is available in Flask's g object.

    Returns:
        True if context is available.
    """
    _ensure_flask()
    return hasattr(flask_g, DRIP_CONTEXT_KEY)


def create_drip_decorator(
    meter: str,
    quantity: float | Callable[[Request], float],
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> Callable[[F], F]:
    """
    Create a reusable Drip decorator with preset configuration.

    Example:
        >>> from drip.middleware.flask import create_drip_decorator
        >>>
        >>> charge_api_call = create_drip_decorator(
        ...     meter="api_calls",
        ...     quantity=1,
        ... )
        >>>
        >>> @app.route("/api/endpoint1")
        >>> @charge_api_call
        >>> def endpoint1():
        ...     return {"success": True}
        >>>
        >>> @app.route("/api/endpoint2")
        >>> @charge_api_call
        >>> def endpoint2():
        ...     return {"success": True}

    Args:
        meter: Usage meter type.
        quantity: Static quantity or callable.
        api_key: API key.
        base_url: API base URL.
        **kwargs: Additional config options.

    Returns:
        Reusable decorator.
    """
    return drip_middleware(
        meter=meter,
        quantity=quantity,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )


# Re-export types for convenience
__all__ = [
    "DripFlaskMiddleware",
    "DripContext",
    "DripMiddlewareConfig",
    "drip_middleware",
    "get_drip_context",
    "has_drip_context",
    "create_drip_decorator",
    "has_payment_proof_headers",
    "get_header",
]
