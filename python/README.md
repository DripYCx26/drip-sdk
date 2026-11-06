# Drip SDK (Python)

Drip is a Python SDK for **usage-based tracking and execution logging** in systems where spend is tied to computation — AI agents, APIs, batch jobs, and infra workloads.

This **Core SDK** is optimized for pilots: capture usage and run data first, add billing later.

**One line to start tracking:** `drip.track_usage(customer_id, meter, quantity)`

[![PyPI version](https://img.shields.io/pypi/v/drip-sdk.svg)](https://pypi.org/project/drip-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/drip-sdk.svg)](https://pypi.org/project/drip-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

This Python SDK lives inside the unified [Drip SDK public repo](../README.md) under [`python/`](./).

---

## 60-Second Quickstart (Core SDK)

### 1. Install

```bash
pip install drip-sdk
```

### 2. Set your API key

```bash
# Secret key — full access (server-side only)
export DRIP_API_KEY=sk_test_...

# Or public key — usage, customers, billing (safe for client-side)
export DRIP_API_KEY=pk_test_...
```

Or use a `.env` file (recommended):

```bash
pip install drip-sdk[dotenv]   # or: pip install python-dotenv
```

```env
# .env
DRIP_API_KEY=sk_test_...
```

The SDK auto-loads `.env` files when `python-dotenv` is installed — no extra code needed.

### 3. Create a customer and track usage

```python
from drip import drip

# Create a customer first
customer = drip.create_customer(external_customer_id="user_123")

# Track usage — that's it
drip.track_usage(customer_id=customer.id, meter="api_calls", quantity=1)
```

The `drip` singleton reads `DRIP_API_KEY` from your environment automatically.

### Alternative: Explicit Configuration

```python
from drip import Drip

# Auto-reads DRIP_API_KEY from environment
client = Drip()

# Or pass config explicitly with a secret key (full access)
client = Drip(api_key="sk_test_...")

# Or with a public key (limited scope, safe for client-side)
client = Drip(api_key="pk_test_...")
```

### Full Example

```python
from drip import drip

# Verify connectivity
drip.ping()

# Create a customer (at least one of external_customer_id or onchain_address required)
customer = drip.create_customer(external_customer_id="user_123")

# Record usage
drip.track_usage(
    customer_id=customer.id,
    meter="llm_tokens",
    quantity=842,
    metadata={"model": "gpt-4o-mini"}
)

# Record execution lifecycle
drip.record_run(
    customer_id=customer.id,
    workflow="research-agent",
    events=[
        {"event_type": "llm.call", "quantity": 1700, "units": "tokens"},
        {"event_type": "tool.call", "quantity": 1},
    ],
    status="COMPLETED"
)

print(f"Customer {customer.id}: usage + run recorded")
```

**Expected result:**
- No exceptions
- Events appear in the Drip dashboard within seconds

---

## Core Concepts

| Concept | Description |
|---------|-------------|
| `customer_id` | The entity you're attributing usage to |
| `meter` | What's being measured (tokens, calls, time, etc.) |
| `quantity` | Numeric usage value |
| `run` | A single request or job execution |
| `correlation_id` | Optional. Your trace/request ID for linking Drip data with your APM (OpenTelemetry, Datadog, etc.) |

**Status values:** `PENDING` | `RUNNING` | `COMPLETED` | `FAILED`

**Event schema:** Payloads are schema-flexible. Drip stores events as structured JSON and does not enforce a fixed event taxonomy. CamelCase is accepted.

> **Distributed tracing:** Pass `correlation_id` to `start_run()`, `record_run()`, or `emit_event()` to cross-reference Drip billing with your observability stack. See [FULL_SDK.md](./FULL_SDK.md#distributed-tracing-correlation_id) for details.

---

## Idempotency Keys

Every mutating SDK method (`charge`, `track_usage`, `emit_event`) requires an `idempotency_key`. The server uses this key to deduplicate requests — if two requests share the same key, only the first is processed. The parameter is optional in the method signature because **the SDK always generates one for you if you don't provide it**.

`record_run` generates idempotency keys internally for its batch events (using `external_run_id` when provided, otherwise deterministic keys).

### Auto-generated keys (default)

When you omit `idempotency_key`, the SDK generates one automatically — this works for both `drip.core.Drip` and the full `Drip` client. The auto key is:

- **Unique per call** — two separate calls with identical parameters produce different keys, even across separate worker processes (e.g., Gunicorn, Lambda).
- **Stable across retries** — the key is generated once and reused for all retry attempts of that call, so network retries are safely deduplicated.

This means you get **free retry safety** with zero configuration.

### When to pass explicit keys

Pass your own `idempotency_key` when you need **application-level deduplication** — e.g., to guarantee that a specific business operation is billed exactly once, even across process restarts:

```python
customer = drip.create_customer(external_customer_id="user_123")

drip.charge(
    customer_id=customer.id,
    meter="api_calls",
    quantity=1,
    idempotency_key=f"order_{order_id}_charge",  # your business-level key
)
```

Common patterns:
- `order_{order_id}` — one charge per order
- `run_{run_id}_step_{step_index}` — one charge per pipeline step
- `invoice_{invoice_id}` — one charge per invoice

---

## Installation Options

```bash
pip install drip-sdk           # core only
pip install drip-sdk[fastapi]  # FastAPI helpers
pip install drip-sdk[flask]    # Flask helpers
pip install drip-sdk[all]      # everything
```

---

## SDK Variants

| Variant | Description |
|---------|-------------|
| **Core SDK** (recommended for pilots) | Usage tracking + execution logging only |
| **Full SDK** | Includes billing, balances, and workflows (for later stages) |

---

## Core SDK Methods

All methods are on the `Drip` / `AsyncDrip` class. Start with these for pilots:

| Method | Description |
|--------|-------------|
| `ping()` | Verify API connection |
| `create_customer(...)` | Create a customer (see below) |
| `get_or_create_customer(external_customer_id)` | Idempotently create or retrieve a customer by external ID |
| `get_customer(customer_id)` | Get customer details |
| `list_customers(options)` | List all customers |
| `track_usage(params)` | Record metered usage |
| `charge(customer_id, meter, quantity, ...)` | Create a billable charge (sync — waits for settlement) |
| `charge_async(customer_id, meter, quantity, ...)` | Async charge — returns immediately, processes in background |
| `list_charges(options)` | List charges for your business |
| `get_charge(charge_id)` | Get a single charge by ID |
| `list_events(options)` | List execution events with filters |
| `get_event(event_id)` | Get a single event by ID |
| `get_event_trace(event_id)` | Get event causality trace (ancestors, children, retries) |
| `record_run(params)` | Log complete agent run (simplified) |
| `start_run(params)` | Start execution trace |
| `emit_event(params)` | Log event within run |
| `emit_events_batch(params)` | Batch log events |
| `end_run(run_id, params)` | Complete execution trace |
| `get_run(run_id)` | Get run details and summary |
| `get_run_timeline(run_id)` | Get execution timeline |
| `get_balance(customer_id)` | Get customer balance |
| `check_entitlement(customer_id, feature_key, quantity?)` | Pre-request authorization check |
| `run(workflow, customer_id)` | Context manager for run tracking (see below) |

### Run Context Manager

```python
with drip.run("research-agent", customer_id=customer.id) as run:
    run.event("llm.call", quantity=1700, units="tokens")
    run.event("tool.call", quantity=1)
# Run auto-completes on exit, or marks FAILED on exception
```

### Creating Customers

All parameters are optional, but at least one of `external_customer_id` or `onchain_address` must be provided:

```python
# Simplest — just your internal user ID
customer = drip.create_customer(external_customer_id="user_123")

# With an on-chain address (for on-chain billing)
customer = drip.create_customer(
    onchain_address="0x1234...",
    external_customer_id="user_123"
)

# Internal/non-billing customer (for tracking only)
customer = drip.create_customer(
    external_customer_id="internal-team",
    is_internal=True
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `external_customer_id` | `str` | No* | Your internal user/account ID |
| `onchain_address` | `str` | No* | Customer's Ethereum address |
| `is_internal` | `bool` | No | Mark as internal (non-billing). Default: `False` |
| `metadata` | `dict` | No | Arbitrary key-value metadata |

\*At least one of `external_customer_id` or `onchain_address` is required.

---

## Async Core SDK

```python
import asyncio
from drip import AsyncDrip


async def main():
    async with AsyncDrip(api_key="sk_test_...") as client:
        await client.ping()

        # Create a customer
        customer = await client.create_customer(external_customer_id="user_123")

        await client.track_usage(
            customer_id=customer.id,
            meter="api_calls",
            quantity=1
        )

        result = await client.record_run(
            customer_id=customer.id,
            workflow="research-agent",
            events=[],
            status="COMPLETED"
        )

        print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Who This Is For

- AI agents (token metering, tool calls, execution traces)
- API companies (per-request billing, endpoint attribution)
- RPC providers (multi-chain call tracking)
- Cloud/infra (compute seconds, storage, bandwidth)

---

## Full SDK (Billing, Entitlements, Webhooks, Subscriptions, Invoices)

For billing, entitlements, subscriptions, invoices, contracts, webhooks, middleware, and advanced features:

```python
from drip import Drip

client = Drip(api_key="sk_test_...")

# Check if a customer can use a feature before processing
check = client.check_entitlement(customer.id, "search")

if not check.allowed:
    # Over quota — return 429 without wasting compute
    pass
```

Key methods:

| Method | Description |
|--------|-------------|
| `get_balance(customer_id)` | Get customer balance (USDC, pending, available) |
| `check_entitlement(customer_id, feature_key, quantity?)` | Pre-request authorization check (allowed/denied + remaining quota) |
| `set_customer_spending_cap(customer_id, cap_type, limit_value)` | Set daily/monthly/single-charge spending cap |
| `get_customer_spending_caps(customer_id)` | List active spending caps |
| `remove_customer_spending_cap(customer_id, cap_id)` | Remove a spending cap |
| `checkout(params)` | Create hosted checkout session for top-ups |

Highlights:
- **Billing** — `charge()`, `list_charges()`, `get_charge()`, `get_balance()`
- **Cost Estimation** — `estimate_from_usage()`, `estimate_from_hypothetical()` for budget planning
- **Spending Caps** — per-customer daily/monthly limits with multi-level alerts at 50%, 80%, 95%, 100%
- **Entitlements** — pre-request quota gating with `check_entitlement()`
- **Subscription billing** — create, update, pause, resume, cancel
- **Invoices** — available via REST API (SDK methods planned)
- **Contracts** — available via REST API (SDK methods planned)
- **Webhooks** — create, verify, manage webhook endpoints
- **Middleware** — FastAPI and Flask integrations

See **[FULL_SDK.md](./FULL_SDK.md)** for complete documentation.

---

## Error Handling

```python
from drip import Drip, DripError, DripAPIError

client = Drip(api_key="sk_test_...")
customer = client.create_customer(external_customer_id="user_123")

try:
    result = client.track_usage(customer_id=customer.id, meter="api_calls", quantity=1)
except DripAPIError as e:
    print(f"API error {e.status_code}: {e.message}")
except DripError as e:
    print(f"Error: {e}")
```

---

## Requirements

- Python 3.10+
- httpx
- pydantic

## Links

- [Full SDK Documentation](./FULL_SDK.md)
- [API Documentation](https://docs.drippay.dev/api-reference)
- [PyPI](https://pypi.org/project/drip-sdk/)

## License

MIT
