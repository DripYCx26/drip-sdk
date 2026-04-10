# Drip SDK (Python) — Full SDK Reference

This document covers billing, webhooks, and advanced features. For usage tracking and execution logging, see the main [README](./README.md).

---

## Contents

- [Installation](#installation)
- [Billing Lifecycle](#billing-lifecycle)
- [Quick Start](#quick-start)
- [Async Support](#async-support)
- [API Reference](#api-reference)
- [Subscription Billing](#subscription-billing)
- [Entitlements](#entitlements-pre-request-authorization)
- [Streaming Meter](#streaming-meter)
- [Framework Middleware](#framework-middleware)
- [LangChain Integration](#langchain-integration)
- [Webhooks](#webhooks)
- [Error Handling](#error-handling)
- [Gotchas](#gotchas)

---

## Installation

```bash
pip install drip-sdk[all]
```

```python
from drip import Drip

client = Drip(api_key="sk_test_...")
```

---

## Billing Lifecycle

Understanding `track_usage` vs `charge`:

| Method | What it does |
|--------|--------------|
| `track_usage()` | Logs usage to the ledger (no billing) |
| `charge()` | Converts usage into a billable charge |
| `create_subscription()` | Creates a recurring subscription (auto-bills on interval) |

**Typical flow:**

1. `track_usage()` throughout the day/request stream
2. Optionally `estimate_from_usage()` to preview cost
3. `charge()` to create billable charges
4. `get_balance()` / `list_charges()` for reconciliation
5. Webhooks for `charge.succeeded` / `charge.failed`

> Most pilots start with `track_usage()` only. Add `charge()` when you're ready to bill.

---

## Quick Start

```python
# Create a customer (at least one of external_customer_id or onchain_address required)
customer = client.create_customer(external_customer_id="user_123")

# Or with an on-chain address for on-chain billing
customer = client.create_customer(
    external_customer_id="user_123",
    onchain_address="0x1234567890abcdef..."
)

# Or an internal/non-billing customer (for tracking only)
internal = client.create_customer(
    external_customer_id="internal-team",
    is_internal=True
)

# Track usage (logs to ledger, no billing)
client.track_usage(
    customer_id=customer.id,
    meter="api_calls",
    quantity=1,
    metadata={"endpoint": "/v1/generate"}
)

# Create a billable charge
result = client.charge(
    customer_id=customer.id,
    meter="api_calls",
    quantity=1
)

print(f"Charged: {result.charge.amount_usdc} USDC")
```

### `create_customer()` Parameters

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `external_customer_id` | `str` | No* | Your internal user/account ID |
| `onchain_address` | `str` | No* | Customer's Ethereum address |
| `is_internal` | `bool` | No | Mark as internal (non-billing). Default: `False` |
| `metadata` | `dict` | No | Arbitrary key-value metadata |

\*At least one of `external_customer_id` or `onchain_address` is required.

---

## Async Support

```python
from drip import AsyncDrip

async with AsyncDrip(api_key="sk_test_...") as client:
    # Create a customer first
    customer = await client.create_customer(
        external_customer_id="user_123"
    )

    # Then charge for usage
    result = await client.charge(
        customer_id=customer.id,
        meter="api_calls",
        quantity=1
    )
```

---

## API Reference

### Usage & Billing

| Method | Description |
|--------|-------------|
| `track_usage(params)` | Log usage to ledger (no billing) |
| `charge(params)` | Create a billable charge (sync — waits for settlement) |
| `charge_async(params)` | Create a billable charge (async — returns immediately, processes in background) |
| `wrap_api_call(params)` | Wrap external API call with guaranteed usage recording |
| `get_balance(customer_id)` | Get balance and usage summary |
| `get_charge(charge_id)` | Get charge details |
| `list_charges(options)` | List all charges |

### Execution Logging

| Method | Description |
|--------|-------------|
| `record_run(params)` | Log complete agent run (simplified) |
| `start_run(params)` | Start execution trace |
| `emit_event(params)` | Log event within run |
| `emit_events_batch(params)` | Batch log events |
| `end_run(run_id, params)` | Complete execution trace |
| `get_run(run_id)` | Get run details and summary |
| `get_run_timeline(run_id)` | Get execution timeline |
| `list_events(options)` | List execution events with filters (customer_id, run_id, event_type, outcome) |
| `get_event(event_id)` | Get full event details |
| `get_event_trace(event_id)` | Get causality trace (ancestors, children, retry chain) |
| `create_workflow(params)` | Create a workflow |
| `list_workflows()` | List all workflows |

### Customer Management

| Method | Description |
|--------|-------------|
| `create_customer(params)` | Create a customer (auto-provisions smart account on testnet) |
| `get_or_create_customer(external_customer_id, metadata?)` | Idempotently create or retrieve a customer by external ID |
| `get_customer(customer_id)` | Get customer details |
| `list_customers(options)` | List all customers |

### Webhooks (Secret Key Only)

All webhook management methods require a **secret key (`sk_`)**. Using a public key throws `DripAPIError(403)`.

| Method | Description |
|--------|-------------|
| `create_webhook(params)` | Create webhook endpoint |
| `update_webhook(webhook_id, params)` | Update a webhook (URL, events, filters, active status) |
| `list_webhooks()` | List all webhooks |
| `get_webhook(webhook_id)` | Get webhook details |
| `delete_webhook(webhook_id)` | Delete a webhook |
| `test_webhook(webhook_id)` | Send a test event to the webhook |
| `rotate_webhook_secret(webhook_id)` | Rotate webhook signing secret |
| `Drip.verify_webhook_signature()` | Verify webhook signature (static, no key needed) |

### Entitlements

| Method | Description |
|--------|-------------|
| `check_entitlement(customer_id, feature_key, quantity)` | Pre-request authorization check (is customer allowed?) |

### Cost Estimation

| Method | Description |
|--------|-------------|
| `estimate_from_usage(params)` | Estimate cost from usage data |
| `estimate_from_hypothetical(params)` | Estimate from hypothetical usage |

### Resilience & Observability

These methods require `resilience=True` in the constructor. They are synchronous (not async).

| Method | Description |
|--------|-------------|
| `get_metrics()` | Get SDK metrics (success rate, P95 latency, error counts) |
| `get_health()` | Get health status (circuit breaker state, rate limiter) |

```python
client = Drip(api_key="sk_test_...", resilience=True)

# After making some requests...
metrics = client.get_metrics()
if metrics:
    print(f"Success rate: {metrics['success_rate']}%")
    print(f"P95 latency: {metrics['p95_latency_ms']}ms")

health = client.get_health()
if health:
    print(f"Circuit: {health['circuit_breaker']['state']}")
```

### Subscriptions (Secret Key Only)

| Method | Description |
|--------|-------------|
| `create_subscription(params)` | Create a recurring subscription |
| `get_subscription(subscription_id)` | Get subscription details |
| `list_subscriptions(options)` | List subscriptions (filter by customer/status) |
| `update_subscription(subscription_id, params)` | Update subscription (name, price, metadata) |
| `cancel_subscription(subscription_id, immediate=False)` | Cancel a subscription (default: end of period) |
| `pause_subscription(subscription_id, resume_date=None)` | Pause a subscription |
| `resume_subscription(subscription_id)` | Resume a paused subscription |

### Invoices (REST API Only)

Invoice management is available via the REST API. SDK methods are planned for a future release.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/invoices/generate` | Generate invoice from charges |
| POST | `/v1/invoices/generate-from-subscription` | Generate from subscription |
| GET | `/v1/invoices` | List invoices |
| GET | `/v1/invoices/:id` | Get invoice details |
| POST | `/v1/invoices/:id/issue` | Finalize (DRAFT → PENDING) |
| POST | `/v1/invoices/:id/paid` | Mark as paid |
| POST | `/v1/invoices/:id/void` | Void an invoice |
| GET | `/v1/invoices/summary` | Aggregated statistics |
| GET | `/v1/invoices/:id/pdf` | Download PDF |

### Contracts (REST API Only)

Contract management is available via the REST API. SDK methods are planned for a future release.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/contracts` | Create a contract |
| GET | `/v1/contracts` | List contracts |
| GET | `/v1/contracts/:id` | Get contract details |
| PATCH | `/v1/contracts/:id` | Update a contract |
| DELETE | `/v1/contracts/:id` | Delete a contract |
| POST | `/v1/contracts/:id/overrides` | Add pricing override |
| DELETE | `/v1/contracts/:id/overrides/:unitType` | Remove pricing override |

### API Keys, Pricing Plans & Usage Caps (REST API Only)

These administrative endpoints are available via the REST API with a secret key (`sk_`). SDK methods are planned for a future release.

| Endpoint | Description |
|--------|-------------|
| `POST /v1/api-keys` | Create a new API key pair (returns pk\_ + sk\_) |
| `GET /v1/api-keys` | List API keys for your business |
| `POST /v1/api-keys/:id/rotate` | Rotate an API key (old key expires after 24h grace period) |
| `POST /v1/api-keys/:id/revoke` | Revoke an API key immediately |
| `POST /v1/pricing-plans` | Create a pricing plan (unit type + price per unit) |
| `GET /v1/pricing-plans` | List pricing plans |
| `POST /v1/usage-caps` | Create a usage cap (daily/monthly charge or request limit) |
| `GET /v1/usage-caps` | List usage caps |
| `PATCH /v1/usage-caps/:id` | Update a usage cap (limit value, alert threshold, active status) |
| `GET /v1/usage-caps/types` | List available cap types |

### Other

| Method | Description |
|--------|-------------|
| `checkout(params)` | Create checkout session (fiat on-ramp) |
| `list_meters()` | List available meters (returns `data`: list of `Meter` with `name`, `meter`, `unit_price_usd`, `is_active`) |
| `ping()` | Verify API connection |

---

## Subscription Billing

```python
# Create a monthly subscription
subscription = client.create_subscription(
    customer_id=customer.id,
    name="Pro Plan",
    price_usdc=49_000000,  # $49.00 in USDC (6 decimals)
    interval="MONTHLY",
)

# List active subscriptions
result = client.list_subscriptions(
    customer_id=customer.id,
    status="ACTIVE",
)

# Pause / resume / cancel
client.pause_subscription(subscription.id)
client.resume_subscription(subscription.id)
client.cancel_subscription(subscription.id)
```

### Update Subscription

```python
# Update subscription name and price
updated = client.update_subscription(
    subscription_id=subscription.id,
    name="Business Plan",
    price_usdc=99_000000,  # $99.00
)
```

### Cancel Options

```python
# Cancel at end of current billing period (default)
client.cancel_subscription(subscription.id)

# Cancel immediately
client.cancel_subscription(subscription.id, immediate=True)
```


### Async Subscription Billing

```python
async with AsyncDrip(api_key="sk_test_...") as client:
    subscription = await client.create_subscription(
        customer_id=customer.id,
        name="Pro Plan",
        price_usdc=49_000000,
        interval="MONTHLY",
    )

    # Update
    updated = await client.update_subscription(
        subscription_id=subscription.id,
        name="Business Plan",
        price_usdc=99_000000,
    )

    # Cancel
    await client.cancel_subscription(subscription.id, immediate=True)
```

---

## Invoices, Contracts (REST API Only)

See the [Invoices](#invoices-rest-api-only) and [Contracts](#contracts-rest-api-only) REST API tables above for available endpoints.

```python
# Example using httpx (SDK methods coming soon)
import httpx

res = httpx.post(
    f"{base_url}/v1/invoices/generate",
    headers={"Authorization": "Bearer sk_live_..."},
    json={
        "customerId": customer.id,
        "periodStart": "2024-01-01T00:00:00Z",
        "periodEnd": "2024-02-01T00:00:00Z",
    },
)
```

**Invoice statuses:** `DRAFT` → `PENDING` → `PAID` | `PARTIALLY_PAID` | `VOIDED` | `OVERDUE`

Contracts let you create per-customer commercial agreements with custom pricing, prepaid commits, spend caps, and discounts. They automatically apply when billing — the pricing engine looks up active contracts and applies overrides before calculating charges.

---

## Entitlements (Pre-Request Authorization)

Check if a customer is allowed to use a feature **before** processing the request. This avoids wasting compute on customers who are over quota.

Entitlement counters are automatically incremented when you call `charge()` — no extra work needed.

```python
customer = client.create_customer(external_customer_id="user_123")

# Check before processing an expensive request
check = client.check_entitlement(customer.id, "search", quantity=1)

if not check.allowed:
    # Customer is over quota — return 429 without processing
    return {
        "error": "Quota exceeded",
        "remaining": check.remaining,
        "limit": check.limit,
        "resets_at": check.period_resets_at,
    }, 429

# Process the request, then charge
results = perform_search(query)
client.charge(customer_id=customer.id, meter="search", quantity=1)
# ^ Entitlement counter auto-increments
```

### Async

```python
async with AsyncDrip(api_key="sk_test_...") as client:
    check = await client.check_entitlement(customer.id, "search")
    if check.allowed:
        # process...
        pass
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customer_id` | `str` | Yes | The Drip customer ID |
| `feature_key` | `str` | Yes | Feature key to check (e.g., `"search"`, `"api_calls"`) |
| `quantity` | `float` | No | Quantity to check (default: 1) |

### EntitlementCheckResult

| Field | Type | Description |
|-------|------|-------------|
| `allowed` | `bool` | Whether the request is permitted |
| `feature_key` | `str` | The feature that was checked |
| `remaining` | `float` | Remaining quota in current period (-1 if unlimited) |
| `limit` | `float` | The limit for this period (-1 if unlimited) |
| `unlimited` | `bool` | Whether the customer has unlimited access |
| `period` | `str` | `"DAILY"` or `"MONTHLY"` |
| `period_resets_at` | `str` | ISO timestamp for when the period resets |
| `reason` | `str \| None` | Denial reason (only present when `allowed` is `False`) |

> **Setup:** Entitlement plans, rules, and customer assignments are managed via the REST API. See the [Entitlements guide](../docs/integration/ENTITLEMENTS.md) for full API reference and setup walkthrough.

---

## Streaming Meter

For LLM token streaming, accumulate usage locally and flush once:

```python
# Create a customer first
customer = client.create_customer(external_customer_id="user_123")

meter = client.create_stream_meter(
    customer_id=customer.id,
    meter="tokens",
)

# Stream from your LLM provider
for chunk in openai_stream:
    tokens = chunk.usage.completion_tokens if chunk.usage else 1
    meter.add_sync(tokens)  # Accumulates locally, no API call
    yield chunk

# Single charge at end of stream
result = meter.flush()
print(f"Charged {result.charge.amount_usdc} USDC for {result.quantity} tokens")
```

### Async Streaming

```python
async with AsyncDrip(api_key="sk_test_...") as client:
    # Create a customer first
    customer = await client.create_customer(external_customer_id="user_123")

    meter = client.create_stream_meter(
        customer_id=customer.id,
        meter="tokens",
    )

    async for chunk in openai_stream:
        await meter.add(chunk.tokens)

    result = await meter.flush_async()
```

### Stream Meter Options

```python
meter = client.create_stream_meter(
    customer_id=customer.id,
    meter="tokens",
    idempotency_key="stream_req_123",           # Prevent duplicates
    metadata={"model": "gpt-4"},                # Attach to charge
    flush_threshold=10000,                       # Auto-flush at 10k tokens
    on_add=lambda qty, total: print(f"Total: {total}"),
)
```

---

## Framework Middleware

### FastAPI

The `customer_resolver` is **required** — it must be a callable that resolves the customer ID from a verified authentication source (e.g., JWT, session token, API key lookup).

```python
from fastapi import FastAPI, Request, Depends
from drip.middleware.fastapi import DripMiddleware, get_drip_context, DripContext


def resolve_customer(request: Request) -> str:
    """Resolve customer from verified JWT."""
    token = request.headers.get("authorization", "").replace("Bearer ", "")
    decoded = verify_jwt(token)
    return decoded["drip_customer_id"]


app = FastAPI()

app.add_middleware(
    DripMiddleware,
    meter="api_calls",
    quantity=1,
    customer_resolver=resolve_customer,
    exclude_paths=["/health", "/docs"],
)

@app.post("/api/generate")
async def generate(request: Request):
    drip = get_drip_context(request)
    print(f"Charged: {drip.charge.charge.amount_usdc} USDC")
    return {"success": True}
```

### Per-Route Configuration

```python
from drip.middleware.fastapi import with_drip

@app.post("/api/expensive")
@with_drip(
    meter="tokens",
    quantity=lambda req: calculate_tokens(req),
    customer_resolver=resolve_customer,
)
async def expensive_operation(request: Request):
    drip = get_drip_context(request)
    return {"charged": drip.charge.charge.amount_usdc}
```

### Flask

```python
from flask import Flask
from drip.middleware.flask import drip_middleware, get_drip_context

app = Flask(__name__)


def resolve_customer(request):
    """Resolve customer from verified session."""
    session = verify_session(request.cookies.get("session"))
    return session["drip_customer_id"]


@app.route("/api/generate", methods=["POST"])
@drip_middleware(meter="api_calls", quantity=1, customer_resolver=resolve_customer)
def generate():
    drip = get_drip_context()
    return {"success": True}
```

### Customer Resolution

The `customer_resolver` must be a callable that resolves customer identity from a **verified authentication source**. Never trust unauthenticated client headers or query parameters directly.

```python
# From JWT (verified server-side)
def get_customer_from_jwt(request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    return decode_jwt(token)["customer_id"]

app.add_middleware(
    DripMiddleware,
    meter="api_calls",
    quantity=1,
    customer_resolver=get_customer_from_jwt,
)

# From session (verified server-side)
def get_customer_from_session(request):
    session = verify_session(request.cookies.get("session"))
    return session["customer_id"]

app.add_middleware(
    DripMiddleware,
    meter="api_calls",
    quantity=1,
    customer_resolver=get_customer_from_session,
)

# From API key lookup (verified server-side)
def get_customer_from_api_key(request):
    api_key = request.headers.get("x-api-key")
    customer = lookup_customer_by_api_key(api_key)
    return customer.drip_id

app.add_middleware(
    DripMiddleware,
    meter="api_calls",
    quantity=1,
    customer_resolver=get_customer_from_api_key,
)
```

---

## LangChain Integration

```python
from drip import Drip
from drip.integrations.langchain import DripCallbackHandler
from langchain_openai import ChatOpenAI

# Create a customer first
client = Drip(api_key="sk_test_...")
customer = client.create_customer(external_customer_id="user_123")

handler = DripCallbackHandler(
    api_key="sk_test_...",
    customer_id=customer.id,
    workflow="chatbot",
)

llm = ChatOpenAI(model="gpt-4", callbacks=[handler])
response = llm.invoke("Hello!")  # Automatically metered and billed
```

Built-in pricing for GPT-4, GPT-3.5, Claude 3, etc.

---

## Customer Spending Caps

Set per-customer spending limits with multi-level alerts (50%, 80%, 95%, 100%). Caps auto-reset daily or monthly and can optionally auto-block charges when exceeded.

```python
from drip import Drip, SpendingCapType

client = Drip(api_key="sk_live_...")

# Create a customer first
customer = client.create_customer(external_customer_id="user_123")

# Set a daily spending cap of $100 USDC
cap = client.set_customer_spending_cap(
    customer.id,
    SpendingCapType.DAILY_CHARGE_LIMIT,
    limit_value=100.0,
    auto_block=True,  # Block charges when cap is reached (default)
)

# Set a monthly cap of $5,000
client.set_customer_spending_cap(
    customer.id,
    SpendingCapType.MONTHLY_CHARGE_LIMIT,
    limit_value=5000.0,
)

# Set a single-charge limit of $50
client.set_customer_spending_cap(
    customer.id,
    SpendingCapType.SINGLE_CHARGE_LIMIT,
    limit_value=50.0,
)

# List all active caps for a customer
result = client.get_customer_spending_caps(customer.id)
for c in result.caps:
    print(f"{c.cap_type}: {c.current_usage}/{c.limit_value} USDC")

# Remove a cap
client.remove_customer_spending_cap(customer.id, cap.id)
```

### Async Usage

```python
from drip import AsyncDrip, SpendingCapType

async with AsyncDrip(api_key="sk_live_...") as client:
    customer = await client.create_customer(external_customer_id="user_123")

    cap = await client.set_customer_spending_cap(
        customer.id,
        SpendingCapType.DAILY_CHARGE_LIMIT,
        limit_value=100.0,
    )

    result = await client.get_customer_spending_caps(customer.id)
    await client.remove_customer_spending_cap(customer.id, cap.id)
```

### Cap Types

| Type | Description | Reset |
|------|-------------|-------|
| `DAILY_CHARGE_LIMIT` | Max total charges per day | Every 24 hours |
| `MONTHLY_CHARGE_LIMIT` | Max total charges per month | Every 30 days |
| `SINGLE_CHARGE_LIMIT` | Max amount for a single charge | N/A |

### Spending Alert Webhooks

When a customer approaches their cap, Drip emits webhook events at these thresholds:

| Threshold | Event | Level |
|-----------|-------|-------|
| 50% | `customer.spending.warning` | `info` |
| 80% | `customer.spending.warning` | `warning` |
| 95% | `customer.spending.warning` | `critical` |
| 100% | `customer.spending.blocked` | `blocked` |

Each alert level fires only once per period. Subscribe to these events via webhooks to get real-time notifications.

---

## Webhooks

```python
# Create webhook
webhook = client.create_webhook(
    url="https://yourapp.com/webhooks/drip",
    events=["charge.succeeded", "charge.failed", "customer.balance.low"],
    description="Production webhook"
)
# IMPORTANT: Store webhook.secret securely!

# Verify incoming webhook
from drip import verify_webhook_signature

is_valid = verify_webhook_signature(
    payload=request.body,
    signature=request.headers["X-Drip-Signature"],
    secret=webhook_secret
)
```

---

## Billing

```python
# Create a customer first
customer = client.create_customer(external_customer_id="user_123")

# Create a billable charge
result = client.charge(
    customer_id=customer.id,
    meter="api_calls",
    quantity=1,
)

# Get customer balance
balance = client.get_balance(customer.id)
print(f"Balance: {balance.balance_usdc} USDC")

# Async charge — returns immediately, processes in background
# Use when you need fast response times and can handle eventual consistency via webhooks
async_result = client.charge_async(
    customer_id=customer.id,
    meter="tokens",
    quantity=1500,
)
print(f"Queued: {async_result.charge.id}, status: {async_result.charge.status}")
# Subscribe to charge.succeeded / charge.failed webhooks for final status

# Query charges
charge = client.get_charge(result.charge.id)
charges = client.list_charges(customer_id=customer.id, limit=100)

# Cost estimation from actual usage
from datetime import datetime
estimate = client.estimate_from_usage(
    period_start=datetime(2024, 1, 1),
    period_end=datetime(2024, 1, 31),
    customer_id=customer.id,
)

# Cost estimation from hypothetical usage (no real data needed)
estimate = client.estimate_from_hypothetical(
    items=[
        {"usage_type": "api_calls", "quantity": 1000},
        {"usage_type": "tokens", "quantity": 50000},
    ]
)
print(f"Estimated cost: ${estimate.estimated_total_usdc} USDC")

# Wrap external API call with guaranteed usage recording
result = client.wrap_api_call(
    customer_id=customer.id,
    meter="tokens",
    call=lambda: openai.chat.completions.create(model="gpt-4", messages=messages),
    extract_usage=lambda r: r.usage.total_tokens,
)
# result.result = the API response
# result.charge = the Drip ChargeResult
# result.idempotency_key = the idempotency key used

# Checkout (fiat on-ramp)
checkout = client.checkout(
    amount=5000,  # $50.00 in cents
    return_url="https://yourapp.com/success",
    customer_id=customer.id,
)
print(f"Checkout URL: {checkout.url}")
```

---

## Event Querying

Query execution events recorded via `emit_event()` or `emit_events_batch()`.

```python
# List all events for a customer
result = client.list_events(customer_id=customer.id)
print(f"{result.total} events found")

# Filter by event type and outcome
failures = client.list_events(
    customer_id=customer.id,
    event_type="tool_call",
    outcome="FAILURE",
    limit=10,
)

# Get full details for a single event
event = client.get_event(result.data[0].id)
print(f"{event.event_type}: {event.outcome}")

# Get causality trace — shows parent chain, children, and retries
trace = client.get_event_trace(result.data[0].id)
print(f"Ancestors: {len(trace.ancestors)}, Children: {len(trace.children)}")
```

---

## Agent Run Tracking

### Simple (record_run)

```python
# Create a customer first
customer = client.create_customer(external_customer_id="user_123")

result = client.record_run(
    customer_id=customer.id,
    workflow="text-generation",
    events=[
        {"eventType": "prompt.received", "quantity": 100, "units": "tokens"},
        {"eventType": "completion.generated", "quantity": 500, "units": "tokens"},
    ],
    status="COMPLETED"
)

print(f"Run ID: {result.run.id}")
```

> **Event key format:** Both snake_case (`event_type`, `cost_units`) and camelCase (`eventType`, `costUnits`) keys are accepted in event dicts. The SDK normalizes to camelCase before sending to the API.

### Fine-Grained Control

```python
# Create a customer
customer = client.create_customer(external_customer_id="user_123")

# Create workflow (once)
workflow = client.create_workflow(
    name="Text Generation",
    slug="text-generation",
    product_surface="AGENT"
)

# Start run
run = client.start_run(
    customer_id=customer.id,
    workflow_id=workflow.id,
    correlation_id="trace_456"
)

# Emit events
client.emit_event(
    run_id=run.id,
    event_type="tokens.generated",
    quantity=1500,
    units="tokens"
)

# End run
result = client.end_run(run.id, status="COMPLETED")

# Get timeline
timeline = client.get_run_timeline(run.id)
print(f"Total cost: {timeline.totals.total_cost_units}")
```

### Distributed Tracing (correlation_id)

Pass a `correlation_id` to link Drip runs with your existing observability tools (OpenTelemetry, Datadog, etc.):

```python
from opentelemetry import trace

customer = client.create_customer(external_customer_id="user_123")
span = trace.get_current_span()

run = client.start_run(
    customer_id=customer.id,
    workflow_id=workflow.id,
    correlation_id=span.get_span_context().trace_id,  # OpenTelemetry trace ID
)

# Or with record_run:
client.record_run(
    customer_id=customer.id,
    workflow="research-agent",
    correlation_id="trace_abc123",
    events=[
        {"eventType": "llm.call", "quantity": 1700, "units": "tokens"},
    ],
    status="COMPLETED",
)
```

**Key points:**
- `correlation_id` is **user-supplied**, not auto-generated — you provide your own trace/request ID
- It's **optional** — skip it if you don't use distributed tracing
- Use it to cross-reference Drip billing data with traces in your APM dashboard
- Common values: OpenTelemetry `trace_id`, Datadog `trace_id`, or your own `request_id`
- Visible in the Drip dashboard timeline and available via `get_run_timeline()`

Events also accept a `correlation_id` for finer-grained linking:

```python
client.emit_event(
    run_id=run.id,
    event_type="llm.call",
    quantity=1700,
    units="tokens",
    correlation_id="span_xyz",  # Link to a specific span
)
```

---

## Error Handling

```python
from drip import (
    DripError,
    DripAPIError,
    DripAuthenticationError,
    DripPaymentRequiredError,
    DripRateLimitError,
    DripNetworkError,
)

try:
    result = client.charge(
        customer_id=customer.id,
        meter="api_calls",
        quantity=1
    )
except DripAuthenticationError:
    print("Invalid API key")
except DripPaymentRequiredError as e:
    print(f"Insufficient balance: {e.payment_request}")
except DripRateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after} seconds")
except DripNetworkError:
    print("Network error, please retry")
except DripAPIError as e:
    print(f"API error {e.status_code}: {e.message}")
```

---

## Gotchas

### Idempotency

The API requires an `idempotency_key` on every mutating request (`charge`, `track_usage`, `emit_event`, and each event in `emit_events_batch`). The SDK **always generates one automatically** if you don't provide it — so zero configuration is needed for basic use. Pass your own key when you need application-level deduplication across process restarts:

```python
result = client.charge(
    customer_id=customer.id,
    meter="api_calls",
    quantity=1,
    idempotency_key="req_abc123_step_1"
)

if result.is_duplicate:
    print("Already processed")
```

### Rate Limits

If you hit 429, back off and retry. The SDK handles this automatically with exponential backoff.

### track_usage vs charge

- `track_usage()` = logging (free, no balance impact)
- `charge()` = billing (deducts from balance)

Start with `track_usage()` during pilots. Add `charge()` when ready to bill.

### Development Mode

Skip charging in development (FastAPI):

```python
app.add_middleware(
    DripMiddleware,
    meter="api_calls",
    quantity=1,
    customer_resolver=resolve_customer,
    skip_in_development=True,  # Skips when DRIP_ENV=development
)
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DRIP_API_KEY` | Your Drip API key |
| `DRIP_API_URL` | Custom API base URL (default: Drip production API; see [drippay.dev](https://drippay.dev)) |
| `DRIP_ENV` | Environment (development/production) |

---

## Requirements

- Python 3.10+
- httpx
- pydantic

## Links

- [Core SDK (README)](./README.md)
- [API Reference](https://docs.drippay.dev/api-reference)
- [GitHub](https://github.com/MichaelLevin5908/drip)
- [PyPI](https://pypi.org/project/drip-sdk/)
