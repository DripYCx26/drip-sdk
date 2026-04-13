# Drip SDK (Node.js) — Full SDK Reference

This document covers billing, webhooks, and advanced features. For usage tracking and execution logging, see the main [README](./README.md).

---

## Contents

- [Installation](#installation)
- [Billing Lifecycle](#billing-lifecycle)
- [Quick Start](#quick-start)
- [Use Cases](#use-cases)
- [API Reference](#api-reference)
- [Pricing Plans](#pricing-plans)
- [Subscription Billing](#subscription-billing)
- [Entitlements](#entitlements-pre-request-authorization)
- [Streaming Meter](#streaming-meter-llm-token-streaming)
- [wrapApiCall](#wrapapicall--guaranteed-usage-recording)
- [Customer Management (Advanced)](#customer-management-advanced)
- [Event Trees and Traces](#event-trees-and-traces)
- [Framework Middleware](#framework-middleware)
- [LangChain Integration](#langchain-integration)
- [Webhooks](#webhooks)
- [Error Handling](#error-handling)
- [Gotchas](#gotchas)

---

## Installation

```bash
npm install @drip-sdk/node
```

```typescript
import { Drip } from '@drip-sdk/node';

// Secret key — use an Operator/Admin secret key for the flows in this guide
const drip = new Drip({ apiKey: 'sk_live_...' });
```

> **Key type detection:** The SDK auto-detects your key type from the prefix. Check `drip.keyType` to see if you're using a `'secret'`, `'public'`, or `'unknown'` key. The customer, usage, charge, run, pricing, and webhook flows in this guide should use a secret key (`sk_*`).

---

## Billing Lifecycle

Everything flows through a single method: `trackUsage()`. The `mode`
parameter controls whether the backend creates a billable charge, queues
it, or records the event for internal visibility only.

| Call | Endpoint | Semantics |
| ---- | -------- | --------- |
| `trackUsage({ ... })` | `POST /usage` | Default. Billing-aware — creates a charge if a pricing plan matches the unit type |
| `trackUsage({ ..., mode: 'batch' })` | `POST /usage/async` | High-throughput — queued, returns 202, charge created in background |
| `trackUsage({ ..., mode: 'internal' })` | `POST /usage/internal` | Visibility-only — never bills |
| `createSubscription()` | — | Recurring subscription (auto-charges on interval) |

> **Migration note:** The old `charge()` and `chargeAsync()` methods were
> removed. They were thin wrappers around `POST /usage` and `POST /usage/async`
> that duplicated `trackUsage`. Replace `drip.charge({...})` with
> `drip.trackUsage({...})` and `drip.chargeAsync({...})` with
> `drip.trackUsage({..., mode: 'batch'})`. `getCharge()` / `listCharges()`
> remain for read-only reconciliation.

**Typical flow:**

1. `trackUsage()` throughout the day/request stream (hits `/usage`,
   creates charges automatically when a pricing plan is configured)
2. Optionally `estimateFromUsage()` to preview cost
3. `getBalance()` / `listCharges()` for reconciliation
4. Webhooks for `charge.succeeded` / `charge.failed`

> Start pilots with `mode: 'internal'` during development. Switch to the
> default billing mode once you've configured a pricing plan for your unit
> type via `createPricingPlan()`.

---

## Quick Start

### Create a Customer + Track Usage

```typescript
// Create a customer first (at least one of externalCustomerId or onchainAddress required)
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

// Track metered usage (logs to ledger, no billing)
await drip.trackUsage({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
  metadata: { endpoint: '/v1/generate', method: 'POST' },
});

// Check accumulated usage
const balance = await drip.getBalance(customer.id);
console.log(`Balance: $${balance.balanceUsdc}`);
```

### Log Agent Runs

```typescript
const result = await drip.recordRun({
  customerId: customer.id,
  workflow: 'research-agent',
  events: [
    { eventType: 'llm.call', quantity: 1700, units: 'tokens' },
    { eventType: 'tool.call', quantity: 1 },
    { eventType: 'llm.call', quantity: 1000, units: 'tokens' },
  ],
  status: 'COMPLETED',
});

console.log(result.summary);
// Output: "Research Agent: 3 events recorded (2.5s)"
```

### View Execution Traces

```typescript
// Assume: runId from a previous startRun() or recordRun()
const runId = 'run_abc123';

const timeline = await drip.getRunTimeline(runId);

for (const event of timeline.events) {
  console.log(`${event.eventType}: ${event.durationMs}ms`);
}
```

---

## Use Cases

### RPC Providers

```typescript
// Create a customer for the API key owner
const apiKeyOwner = await drip.createCustomer({ externalCustomerId: 'rpc_user_123' });

await drip.trackUsage({
  customerId: apiKeyOwner.id,
  meter: 'rpc_calls',
  quantity: 1,
  metadata: {
    method: 'eth_call',
    chain: 'ethereum',
    latencyMs: 45,
    cacheHit: false,
  },
});
```

### API Companies

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'api_user_123' });

await drip.trackUsage({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
  metadata: {
    endpoint: '/v1/embeddings',
    tokens: 1500,
    model: 'text-embedding-3-small',
  },
});
```

### AI Agents

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

const run = await drip.startRun({
  customerId: customer.id,
  workflowId: 'document-processor',
});

await drip.emitEvent({
  runId: run.id,
  eventType: 'ocr.process',
  quantity: 5,
  units: 'pages',
});

await drip.emitEvent({
  runId: run.id,
  eventType: 'llm.summarize',
  quantity: 10500,
  units: 'tokens',
  metadata: { model: 'gpt-4', inputTokens: 10000, outputTokens: 500 },
});

await drip.endRun(run.id, { status: 'COMPLETED' });
```

### Distributed Tracing (correlationId)

Pass a `correlationId` to link Drip runs with your existing observability tools (OpenTelemetry, Datadog, etc.):

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

const run = await drip.startRun({
  customerId: customer.id,
  workflowId: 'document-processor',
  correlationId: span.spanContext().traceId, // OpenTelemetry trace ID
});

// Or with recordRun:
await drip.recordRun({
  customerId: customer.id,
  workflow: 'research-agent',
  correlationId: 'trace_abc123',
  events: [
    { eventType: 'llm.call', quantity: 1700, units: 'tokens' },
  ],
  status: 'COMPLETED',
});
```

**Key points:**
- `correlationId` is **user-supplied**, not auto-generated — you provide your own trace/request ID
- It's **optional** — skip it if you don't use distributed tracing
- Use it to cross-reference Drip billing data with traces in your APM dashboard
- Common values: OpenTelemetry `traceId`, Datadog `trace_id`, or your own `requestId`
- Visible in the Drip dashboard timeline and available via `getRunTimeline()`

Events also accept a `correlationId` for even finer-grained linking:

```typescript
await drip.emitEvent({
  runId: run.id,
  eventType: 'llm.call',
  quantity: 1700,
  units: 'tokens',
  correlationId: span.spanContext().spanId, // Link to a specific span
});
```

---

## API Reference

### Usage & Billing

| Method | Description |
|--------|-------------|
| `trackUsage(params)` | Log usage to ledger (no billing). Supports `mode: 'sync'` (default) and `mode: 'batch'` for high-throughput |
| `charge(params)` | Create a billable charge (sync — waits for settlement) |
| `chargeAsync(params)` | Create a billable charge (async — returns 202, processes in background) |
| `wrapApiCall(params)` | Wrap external API call with guaranteed usage recording |
| `getBalance(customerId)` | Get balance and usage summary |
| `getCharge(chargeId)` | Get charge details |
| `listCharges(options)` | List all charges |

### Execution Logging

| Method | Description |
|--------|-------------|
| `recordRun(params)` | Log complete agent run (simplified) |
| `startRun(params)` | Start execution trace |
| `emitEvent(params)` | Log event within run (supports `parentEventId`, `spanId`) |
| `emitEventsBatch(params)` | Batch log events |
| `endRun(runId, params)` | Complete execution trace |
| `getRun(runId)` | Get run details |
| `getRunTimeline(runId, options?)` | Get execution timeline (supports `limit`, `cursor`, `includeAnomalies`, `collapseRetries`) |
| `listEvents(options?)` | List execution events with filters (customerId, runId, eventType, outcome) |
| `getEvent(eventId)` | Get full event details |
| `getEventTrace(eventId)` | Get causality trace (ancestors, children, retry chain) |
| `createWorkflow(params)` | Create a workflow |
| `listWorkflows()` | List all workflows |

### Customer Management

| Method | Description |
|--------|-------------|
| `createCustomer(params)` | Create a customer (auto-provisions smart account on testnet) |
| `getOrCreateCustomer(externalCustomerId, metadata?)` | Idempotently create or retrieve a customer by external ID |
| `getCustomer(customerId)` | Get customer details |
| `listCustomers(options)` | List all customers |
| `provisionCustomer(customerId)` | Provision/re-provision smart account (secret key only) |
| `syncCustomerBalance(customerId)` | Sync on-chain balance from blockchain (secret key only) |

### Webhooks (Secret Key Only)

All webhook management methods require a **secret key (`sk_`)**. Using a public key throws `DripError(403)`.

| Method | Description |
|--------|-------------|
| `createWebhook(params)` | Create webhook endpoint |
| `updateWebhook(webhookId, params)` | Update a webhook (URL, events, filters, active status) |
| `listWebhooks()` | List all webhooks |
| `getWebhook(webhookId)` | Get webhook details |
| `deleteWebhook(webhookId)` | Delete a webhook |
| `testWebhook(webhookId)` | Send a test event to a webhook |
| `rotateWebhookSecret(webhookId)` | Rotate webhook signing secret |
| `Drip.verifyWebhookSignature()` | Verify webhook signature (static, no key needed) |

### Entitlements

| Method | Description |
|--------|-------------|
| `checkEntitlement(params)` | Pre-request authorization check (is customer allowed?) |

### Cost Estimation

| Method | Description |
|--------|-------------|
| `estimateFromUsage(params)` | Estimate cost from usage data |
| `estimateFromHypothetical(params)` | Estimate from hypothetical usage |

### Resilience & Observability

These methods require `resilience: true` in the constructor. They are synchronous (not async).

| Method | Description |
|--------|-------------|
| `getMetrics()` | Get SDK metrics (success rate, P95 latency, error counts) |
| `getHealth()` | Get health status (circuit breaker state, rate limiter) |

```typescript
const drip = new Drip({ apiKey: 'sk_test_...', resilience: true });

// After making some requests...
const metrics = drip.getMetrics();
if (metrics) {
  console.log(`Success rate: ${metrics.successRate.toFixed(1)}%`);
  console.log(`P95 latency: ${metrics.p95LatencyMs.toFixed(0)}ms`);
}

const health = drip.getHealth();
if (health) {
  console.log(`Circuit: ${health.circuitBreaker.state}`);
  console.log(`Available tokens: ${health.rateLimiter.availableTokens}`);
}
```

### Resilience modes

The `resilience` constructor option accepts three forms:

| Value | Rate limit | Retries | Circuit breaker | Best for |
|-------|-----------|---------|-----------------|----------|
| `true` (default) | 100 req/s, burst 200 | 3 retries | 5 failures to open | Most production apps |
| `'high-throughput'` | 1,000 req/s, burst 2,000 | 2 retries | 10 failures to open | High-volume ingestion |
| `false` | Disabled | Disabled | Disabled | Tests, low-level control |

### Custom resilience config

Pass a partial `ResilienceConfig` object to override specific settings:

```typescript
const drip = new Drip({
  apiKey: 'sk_live_...',
  resilience: {
    rateLimiter: {
      requestsPerSecond: 500,  // default: 100
      burstSize: 1000,         // default: 200
      enabled: true,
    },
    retry: {
      maxRetries: 5,           // default: 3
      baseDelayMs: 200,        // default: 100
      maxDelayMs: 15000,       // default: 10000
      retryableStatusCodes: [429, 500, 502, 503, 504], // default
      enabled: true,
    },
    circuitBreaker: {
      failureThreshold: 10,    // default: 5 — failures before opening
      successThreshold: 3,     // default: 2 — successes to close again
      timeoutMs: 60000,        // default: 30000 — wait before half-open
      enabled: true,
    },
    collectMetrics: true,      // default: true — enables getMetrics()
  },
});
```

### Circuit breaker states

| State | Meaning | Behavior |
|-------|---------|----------|
| `closed` | Healthy | All requests pass through |
| `open` | Too many failures | Requests fail immediately (fast-fail) |
| `half_open` | Testing recovery | Limited requests pass; if they succeed, circuit closes |

### Subscriptions (Secret Key Only)

| Method | Description |
|--------|-------------|
| `createSubscription(params)` | Create a recurring subscription |
| `getSubscription(subscriptionId)` | Get subscription details |
| `listSubscriptions(options)` | List subscriptions (filter by customer/status) |
| `updateSubscription(subscriptionId, params)` | Update subscription (name, amount, metadata) |
| `cancelSubscription(subscriptionId, params?)` | Cancel a subscription (default: end of period; `{ immediate: true }` for immediate) |
| `pauseSubscription(subscriptionId, params?)` | Pause a subscription (optional `resumeDate`) |
| `resumeSubscription(subscriptionId)` | Resume a paused subscription |

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

### Pricing Plans (Secret Key Only)

| Method | Description |
|--------|-------------|
| `createPricingPlan(params)` | Create a pricing plan (FLAT, TIERED, VOLUME, PACKAGE, PER_SEAT) |
| `getPricingPlan(planId)` | Get a pricing plan by ID (with tiers) |
| `listPricingPlans()` | List all pricing plans |
| `updatePricingPlan(planId, params)` | Update a plan (price changes create a new version) |
| `deletePricingPlan(planId)` | Soft-delete (deactivate) a plan |
| `getPricingPlanByType(unitType)` | Look up the active plan for a usage type |
| `listMeters()` | List meters (simplified pricing plan view) |

### API Keys & Usage Caps (REST API Only)

These administrative endpoints are available via the REST API with a secret key (`sk_`). SDK methods are planned for a future release.

| Endpoint | Description |
|--------|-------------|
| `POST /v1/api-keys` | Create a new API key pair (returns pk\_ + sk\_) |
| `GET /v1/api-keys` | List API keys for your business |
| `POST /v1/api-keys/:id/rotate` | Rotate an API key (old key expires after 24h grace period) |
| `POST /v1/api-keys/:id/revoke` | Revoke an API key immediately |
| `POST /v1/usage-caps` | Create a usage cap (daily/monthly charge or request limit) |
| `GET /v1/usage-caps` | List usage caps |
| `PATCH /v1/usage-caps/:id` | Update a usage cap (limit value, alert threshold, active status) |
| `GET /v1/usage-caps/types` | List available cap types |

### Other

| Method | Description |
|--------|-------------|
| `checkout(params)` | Create checkout session (fiat on-ramp) |
| `listMeters()` | List available meters |
| `ping()` | Verify API connection |

---

## Pricing Plans

Pricing plans define how much to charge per unit of usage. Each `unitType` (e.g., `api_call`, `token`, `compute_second`) can have one active plan at a time.

> **Requires a secret key (`sk_live_...`).** All pricing plan methods throw `DripError(403)` when called with a public key.

### Create a Pricing Plan

```typescript
// Simple flat-rate plan
const plan = await drip.createPricingPlan({
  name: 'API Calls',
  unitType: 'api_call',
  unitPriceUsd: 0.001,  // $0.001 per call
});

// Tiered (graduated) pricing — each tier applies to its range only
const tiered = await drip.createPricingPlan({
  name: 'Token Usage',
  unitType: 'token',
  unitPriceUsd: 0.0001,
  pricingModel: 'TIERED',
  tiers: [
    { minQuantity: 0, maxQuantity: 10000, unitPriceUsd: 0.0001 },
    { minQuantity: 10000, maxQuantity: 100000, unitPriceUsd: 0.00008 },
    { minQuantity: 100000, maxQuantity: null, unitPriceUsd: 0.00005 },
  ],
});

// Volume pricing — entire quantity priced at the matching tier
const volume = await drip.createPricingPlan({
  name: 'Compute',
  unitType: 'compute_second',
  unitPriceUsd: 0.01,
  pricingModel: 'VOLUME',
  tiers: [
    { minQuantity: 0, maxQuantity: 3600, unitPriceUsd: 0.01 },
    { minQuantity: 3600, maxQuantity: null, unitPriceUsd: 0.005 },
  ],
});

// Package pricing — charge per bundle of N units
const pkg = await drip.createPricingPlan({
  name: 'Storage',
  unitType: 'gb_storage',
  unitPriceUsd: 5.0,
  pricingModel: 'PACKAGE',
  tiers: [
    { minQuantity: 0, maxQuantity: null, unitPriceUsd: 5.0, packageSize: 100 },
  ],
});

// Per-seat pricing
const seat = await drip.createPricingPlan({
  name: 'Team License',
  unitType: 'seat',
  unitPriceUsd: 10.0,
  pricingModel: 'PER_SEAT',
});
```

### List & Look Up Plans

```typescript
// List all plans
const { data: plans } = await drip.listPricingPlans();
for (const plan of plans) {
  console.log(`${plan.name} (${plan.unitType}): $${plan.unitPriceUsd}/unit [${plan.pricingModel}]`);
}

// Get a specific plan by ID
const plan = await drip.getPricingPlan('plan_abc123');

// Look up by usage type (convenience)
const apiPlan = await drip.getPricingPlanByType('api_call');
console.log(`API calls cost $${apiPlan.unitPriceUsd} each`);
```

### Update a Plan

```typescript
// Rename (metadata-only, no versioning)
await drip.updatePricingPlan('plan_abc123', { name: 'Premium API Calls' });

// Change price (creates a new version, preserving billing history)
await drip.updatePricingPlan('plan_abc123', { unitPriceUsd: 0.002 });

// Switch pricing model
await drip.updatePricingPlan('plan_abc123', {
  pricingModel: 'TIERED',
  tiers: [
    { minQuantity: 0, maxQuantity: 1000, unitPriceUsd: 0.001 },
    { minQuantity: 1000, maxQuantity: null, unitPriceUsd: 0.0005 },
  ],
});
```

### Deactivate / Reactivate

```typescript
// Soft-delete (deactivate) — stops new charges, preserves history
await drip.deletePricingPlan('plan_abc123');

// Or deactivate via update (same effect, but returns the plan)
await drip.updatePricingPlan('plan_abc123', { isActive: false });

// Reactivate later
await drip.updatePricingPlan('plan_abc123', { isActive: true });
```

### Pricing Models Reference

| Model | Behavior | Tiers |
|-------|----------|-------|
| `FLAT` | `quantity * unitPriceUsd` | None |
| `TIERED` | Each tier applies to units within its range (graduated) | Required |
| `VOLUME` | Entire quantity priced at the single matching tier | Required |
| `PACKAGE` | Quantity rounded up to nearest `packageSize`, then priced | Required (with `packageSize`) |
| `PER_SEAT` | `seats * unitPriceUsd` per billing period | None |

### Legacy: `listMeters()`

The `listMeters()` method returns a simplified view of pricing plans (id, name, meter, unitPriceUsd, isActive). For full plan details including tiers and pricing models, use `listPricingPlans()` instead.

---

## Subscription Billing

```typescript
// Create a monthly subscription
const subscription = await drip.createSubscription({
  customerId: customer.id,
  name: 'Pro Plan',
  priceUsdc: 49_000000,  // $49.00 in USDC (6 decimals)
  interval: 'MONTHLY',
});

// List active subscriptions
const { data } = await drip.listSubscriptions({
  customerId: customer.id,
  status: 'ACTIVE',
});

// Pause / resume / cancel
await drip.pauseSubscription(subscription.id);
await drip.resumeSubscription(subscription.id);
await drip.cancelSubscription(subscription.id);
```

### Update Subscription

```typescript
// Update subscription name and metadata
const updated = await drip.updateSubscription(subscription.id, {
  name: 'Business Plan',
  metadata: { tier: 'business' },
});
```

### Cancel Options

```typescript
// Cancel at end of current billing period (default)
await drip.cancelSubscription(subscription.id);

// Cancel immediately
await drip.cancelSubscription(subscription.id, { immediate: true });
```


---

## Invoices, Contracts (REST API Only)

See the [Invoices](#invoices-rest-api-only) and [Contracts](#contracts-rest-api-only) REST API tables above for available endpoints.

```typescript
// Example using fetch (SDK methods coming soon)
const res = await fetch(`${baseUrl}/v1/invoices/generate`, {
  method: 'POST',
  headers: { Authorization: 'Bearer sk_live_...', 'Content-Type': 'application/json' },
  body: JSON.stringify({
    customerId: customer.id,
    periodStart: '2024-01-01T00:00:00Z',
    periodEnd: '2024-02-01T00:00:00Z',
  }),
});
```

**Invoice statuses:** `DRAFT` → `PENDING` → `PAID` | `PARTIALLY_PAID` | `VOIDED` | `OVERDUE`

Contracts let you create per-customer commercial agreements with custom pricing, prepaid commits, spend caps, and discounts. They automatically apply when billing — the pricing engine looks up active contracts and applies overrides before calculating charges.

---

## Entitlements (Pre-Request Authorization)

Check if a customer is allowed to use a feature **before** processing the request. This avoids wasting compute on customers who are over quota.

Entitlement counters are automatically incremented when you call `trackUsage()` in billing mode (default `mode: 'sync'`) — no extra work needed.

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

// Check before processing an expensive request
const check = await drip.checkEntitlement({
  customerId: customer.id,
  featureKey: 'search',
  quantity: 1,
});

if (!check.allowed) {
  // Customer is over quota — return 429 without processing
  return res.status(429).json({
    error: 'Quota exceeded',
    remaining: check.remaining,
    limit: check.limit,
    resetsAt: check.periodResetsAt,
  });
}

// Process the request, then charge
const results = await performSearch(query);
await drip.charge({ customerId: customer.id, meter: 'search', quantity: 1 });
// ^ Entitlement counter auto-increments
```

### CheckEntitlementParams

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customerId` | `string` | Yes | The Drip customer ID |
| `featureKey` | `string` | Yes | Feature key to check (e.g., `"search"`, `"api_calls"`) |
| `quantity` | `number` | No | Quantity to check (default: 1) |

### EntitlementCheckResult

| Field | Type | Description |
|-------|------|-------------|
| `allowed` | `boolean` | Whether the request is permitted |
| `featureKey` | `string` | The feature that was checked |
| `remaining` | `number` | Remaining quota in current period (-1 if unlimited) |
| `limit` | `number` | The limit for this period (-1 if unlimited) |
| `unlimited` | `boolean` | Whether the customer has unlimited access |
| `period` | `'DAILY' \| 'MONTHLY'` | The period this limit applies to |
| `periodResetsAt` | `string` | ISO timestamp for when the period resets |
| `reason` | `string?` | Denial reason (only present when `allowed` is `false`) |

> **Setup:** Entitlement plans, rules, and customer assignments are managed via the REST API. See the [Entitlements guide](../../docs/integration/entitlements.md) for full API reference and setup walkthrough.

---

## Batch Mode (High-Throughput Usage Tracking)

`trackUsage()` supports two write modes via the `mode` parameter:

| Mode | Endpoint | Latency | Returns | Use case |
|------|----------|---------|---------|----------|
| `sync` (default) | `/usage/internal` | ~50ms | `usageEventId` | Standard tracking — you need the event ID |
| `batch` | `/usage/internal/batch` | ~5ms | `pendingEvents`, `idempotencyKey` | High-volume, fire-and-forget telemetry |

### Sync mode (default)

```typescript
const result = await drip.trackUsage({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
});
// result: TrackUsageSyncResult
// result.usageEventId → "evt_abc123"
// result.isInternal → false
```

### Batch mode

Pass `mode: 'batch'` to enqueue the event for high-throughput bulk persistence. The server batches queued events and persists them in ~2-second windows.

```typescript
const result = await drip.trackUsage({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
  mode: 'batch',
});
// result: TrackUsageBatchResult
// result.mode → 'batch'
// result.pendingEvents → 42 (number of events in the queue)
// result.idempotencyKey → "track_cust_abc_api_calls_1_0"
// result.usageEventId → undefined (not assigned until flush)
```

### Discriminating the result type

```typescript
const result = await drip.trackUsage({ customerId, meter, quantity, mode: 'batch' });

if ('mode' in result && result.mode === 'batch') {
  // TrackUsageBatchResult
  console.log(`Queued: ${result.pendingEvents} pending`);
} else {
  // TrackUsageSyncResult
  console.log(`Stored: ${result.usageEventId}`);
}
```

### When to use batch mode

- **High-frequency telemetry** — thousands of events/second (e.g., per-request API metering)
- **Sub-cent microtransactions** — where latency matters more than immediate confirmation
- **Fire-and-forget** — when you don't need the `usageEventId` right away
- **Background workers** — batch jobs processing large volumes of usage data

For standard integrations (~100 events/second or fewer), stick with the default `sync` mode.

### Idempotency in batch mode

Batch mode uses the same idempotency key system as sync mode. The SDK auto-generates a key if you don't provide one. Duplicate keys are silently deduplicated on the server — safe to retry.

---

## Streaming Meter (LLM Token Streaming)

Accumulate usage locally and charge once at the end — ideal for LLM token streaming and high-frequency metering where you don't want an API call per chunk.

### Basic usage

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

const meter = drip.createStreamMeter({
  customerId: customer.id,
  meter: 'tokens',
});

for await (const chunk of llmStream) {
  await meter.add(chunk.tokens);
  yield chunk;
}

// Single API call at end
const result = await meter.flush();
console.log(`Charged ${result.quantity} tokens`);
```

### `addSync()` — maximum performance

Use `addSync()` instead of `add()` when you don't need auto-flush. It's synchronous and never makes an API call — pure local accumulation:

```typescript
for await (const chunk of llmStream) {
  meter.addSync(chunk.tokens); // synchronous, no await needed
}
await meter.flush();
```

### Auto-flush with `flushThreshold`

Set a threshold to automatically flush when accumulated usage reaches a limit. Useful for long-running streams where you want periodic charges rather than one massive charge at the end:

```typescript
const meter = drip.createStreamMeter({
  customerId: customer.id,
  meter: 'tokens',
  flushThreshold: 10_000, // auto-flush every 10k tokens
});

for await (const chunk of llmStream) {
  await meter.add(chunk.tokens); // auto-flushes when total >= 10,000
}

// Flush any remaining tokens
await meter.flush();
```

> **Note:** Auto-flush only triggers with `add()`, not `addSync()`. If you use `addSync()`, you must call `flush()` manually.

### Callbacks

Monitor accumulation and flush events with `onAdd` and `onFlush`:

```typescript
const meter = drip.createStreamMeter({
  customerId: customer.id,
  meter: 'tokens',
  onAdd: (quantity, total) => {
    console.log(`Added ${quantity}, running total: ${total}`);
  },
  onFlush: (result) => {
    console.log(`Flushed ${result.quantity} tokens, charge: ${result.charge?.id}`);
  },
});
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `total` | `number` | Current accumulated quantity (not yet charged) |
| `isFlushed` | `boolean` | Whether this meter has been flushed at least once |
| `flushCount` | `number` | Number of times this meter has been flushed |

### `reset()` — discard accumulated usage

Call `reset()` to discard accumulated usage without charging. Useful when the stream fails before delivery:

```typescript
try {
  for await (const chunk of llmStream) {
    meter.addSync(chunk.tokens);
    yield chunk;
  }
  await meter.flush();
} catch (error) {
  meter.reset(); // discard — don't charge for undelivered tokens
  throw error;
}
```

### Multi-flush idempotency

Each flush gets a unique idempotency key. If you provide an `idempotencyKey` in the options, each flush appends `_flush_0`, `_flush_1`, etc. If you don't provide one, the SDK generates a deterministic key per flush. Safe to retry.

### StreamMeterOptions

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customerId` | `string` | Yes | Customer to charge |
| `meter` | `string` | Yes | Usage type (must match a pricing plan) |
| `idempotencyKey` | `string` | No | Base key — `_flush_N` appended per flush |
| `metadata` | `Record<string, unknown>` | No | Attached to the charge |
| `flushThreshold` | `number` | No | Auto-flush when accumulated total reaches this |
| `onAdd` | `(quantity, total) => void` | No | Called on each `add()` / `addSync()` |
| `onFlush` | `(result) => void` | No | Called after each successful flush |

### StreamMeterFlushResult

| Field | Type | Description |
|-------|------|-------------|
| `success` | `boolean` | Whether the flush succeeded |
| `quantity` | `number` | Quantity that was charged |
| `charge` | `Charge \| null` | The charge object (null if quantity was 0) |
| `isDuplicate` | `boolean` | Whether the server deduplicated this flush |

---

## Framework Middleware

### Next.js

```typescript
import { withDrip } from '@drip-sdk/node/next';

export const POST = withDrip({
  meter: 'api_calls',
  quantity: 1,
  customerResolver: async (request) => {
    // Resolve from your auth system (e.g., JWT, session)
    const session = await verifySession(request);
    return session.dripCustomerId;
  },
}, async (req, { customerId }) => {
  return Response.json({ result: 'success' });
});
```

### Express

```typescript
import { dripMiddleware } from '@drip-sdk/node/express';

app.use('/api', dripMiddleware({
  meter: 'api_calls',
  quantity: 1,
  customerResolver: (req) => {
    // Resolve from your auth system (e.g., JWT, session, API key)
    return req.user.dripCustomerId;
  },
}));
```

---

## LangChain Integration

```typescript
import { DripCallbackHandler } from '@drip-sdk/node/langchain';

// Create a customer first
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

const handler = new DripCallbackHandler({
  drip,
  customerId: customer.id,
});

// Automatically tracks all LLM calls and tool usage
await agent.invoke({ input: '...' }, { callbacks: [handler] });
```

---

## Customer Spending Caps

Set per-customer spending limits with multi-level alerts (50%, 80%, 95%, 100%). Caps auto-reset daily or monthly and can optionally auto-block charges when exceeded.

```typescript
import { Drip, SpendingCapType } from '@drip-sdk/node';

const drip = new Drip({ apiKey: 'sk_live_...' });

// Create a customer first (or use an existing customer.id)
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

// Set a daily spending cap of $100 USDC
const cap = await drip.setCustomerSpendingCap(customer.id, {
  capType: 'DAILY_CHARGE_LIMIT',
  limitValue: 100,
  autoBlock: true, // Block charges when cap is reached (default)
});

// Set a monthly cap of $5,000
await drip.setCustomerSpendingCap(customer.id, {
  capType: 'MONTHLY_CHARGE_LIMIT',
  limitValue: 5000,
});

// Set a single-charge limit of $50
await drip.setCustomerSpendingCap(customer.id, {
  capType: 'SINGLE_CHARGE_LIMIT',
  limitValue: 50,
});

// List all active caps for a customer
const { caps } = await drip.getCustomerSpendingCaps(customer.id);
for (const c of caps) {
  console.log(`${c.capType}: ${c.currentUsage}/${c.limitValue} USDC`);
}

// Remove a cap
await drip.removeCustomerSpendingCap(customer.id, cap.id);
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

> **Secret key required.** All webhook management methods require an `sk_` key. Public keys (`pk_`) will receive a `DripError` with code `PUBLIC_KEY_NOT_ALLOWED` (HTTP 403).

```typescript
// Must use a secret key for webhook management
const drip = new Drip({ apiKey: 'sk_live_...' });

// Create webhook
const webhook = await drip.createWebhook({
  url: 'https://yourapp.com/webhooks/drip',
  events: ['charge.succeeded', 'charge.failed', 'customer.balance.low'],
});
// IMPORTANT: Store webhook.secret securely!

// Verify incoming webhook (static method, no key needed)
import { Drip } from '@drip-sdk/node';

const isValid = await Drip.verifyWebhookSignature(
  request.body,
  request.headers['x-drip-signature'],
  webhookSecret,
);
```

---

## Billing

```typescript
// Create a customer first
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

// Create a billable charge
const result = await drip.charge({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
});

// Get customer balance
const balance = await drip.getBalance(customer.id);
console.log(`Balance: $${balance.balanceUsdc}`);

// Async charge — returns 202, processes in background
// Use when you need fast response times and can handle eventual consistency via webhooks
const asyncResult = await drip.chargeAsync({
  customerId: customer.id,
  meter: 'tokens',
  quantity: 1500,
});
console.log(`Queued: ${asyncResult.charge.id}, status: ${asyncResult.charge.status}`);
// Subscribe to charge.succeeded / charge.failed webhooks for final status

// Query charges
const charge = await drip.getCharge(result.charge.id);
const charges = await drip.listCharges({ customerId: customer.id });

// Cost estimation from actual usage
const periodStart = new Date('2024-01-01');
const periodEnd = new Date('2024-01-31');
await drip.estimateFromUsage({ customerId: customer.id, periodStart, periodEnd });

// Cost estimation from hypothetical usage (no real data needed)
const estimate = await drip.estimateFromHypothetical({
  items: [
    { usageType: 'api_calls', quantity: 1000 },
    { usageType: 'tokens', quantity: 50000 },
  ],
});
console.log(`Estimated cost: $${estimate.estimatedTotalUsdc}`);

// Wrap external API call with guaranteed usage recording
const result = await drip.wrapApiCall({
  customerId: customer.id,
  meter: 'tokens',
  call: async () => openai.chat.completions.create({ model: 'gpt-4', messages }),
  extractUsage: (response) => response.usage.total_tokens,
});
// result.result = the API response, result.charge = the Drip charge

// Checkout (fiat on-ramp)
await drip.checkout({ customerId: customer.id, amount: 5000, returnUrl: 'https://yourapp.com/success' });
```

---

## `wrapApiCall()` — Guaranteed Usage Recording

Wraps an external API call (OpenAI, Anthropic, etc.) with automatic charge recording. The key guarantee: even if your process crashes after the API call returns, retrying with the same idempotency key is safe — the charge won't double-count.

```typescript
const { result, charge } = await drip.wrapApiCall({
  customerId: customer.id,
  meter: 'tokens',
  call: () => openai.chat.completions.create({
    model: 'gpt-4',
    messages: [{ role: 'user', content: 'Hello!' }],
  }),
  extractUsage: (r) => r.usage?.total_tokens ?? 0,
});
```

**How it works:**
1. Generates an idempotency key **before** the API call
2. Calls your function (no retry — called exactly once)
3. Extracts usage from the result via `extractUsage`
4. Records the charge in Drip **with** retry (idempotency makes this safe)

### Retry options

The Drip charge call (step 4) retries automatically. You can customize this:

```typescript
const { result } = await drip.wrapApiCall({
  customerId: customer.id,
  meter: 'api_calls',
  call: () => fetch('https://api.example.com/expensive'),
  extractUsage: () => 1,
  retryOptions: {
    maxAttempts: 5,     // default: 3
    baseDelayMs: 200,   // default: 100 — exponential backoff base
    maxDelayMs: 10000,  // default: 5000 — maximum delay between retries
    isRetryable: (err) => {
      // Custom logic — by default retries on network errors, 5xx, 408, 429
      return err instanceof DripError && err.statusCode >= 500;
    },
  },
});
```

### WrapApiCallParams

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customerId` | `string` | Yes | Customer to charge |
| `meter` | `string` | Yes | Usage type (must match a pricing plan) |
| `call` | `() => Promise<T>` | Yes | Your external API call |
| `extractUsage` | `(result: T) => number` | Yes | Extract quantity from the API response |
| `idempotencyKey` | `string` | No | Custom key — auto-generated if omitted |
| `metadata` | `Record<string, unknown>` | No | Attached to the charge |
| `retryOptions` | `RetryOptions` | No | Customize retry behavior for the charge call |

---

## Customer Management (Advanced)

### `provisionCustomer(customerId)`

Provisions (or re-provisions) an ERC-4337 smart account for a customer. On testnet, auto-funds with USDC. Requires a **secret key** (`sk_`).

```typescript
const customer = await drip.provisionCustomer('cust_abc123');
console.log(customer.onchainAddress); // smart account address
```

Use this when:
- A customer was created without an `onchainAddress` and now needs one
- You need to re-provision after a failed initial provisioning
- You're migrating customers to on-chain billing

### `syncCustomerBalance(customerId)`

Syncs a customer's on-chain balance from the blockchain. Requires a **secret key** (`sk_`).

```typescript
const { balance } = await drip.syncCustomerBalance('cust_abc123');
console.log(`On-chain balance: ${balance} USDC`);
```

Use this when the dashboard shows a stale balance, or after an on-chain deposit that hasn't been picked up by the periodic sync.

---

## Event Trees and Traces

### `parentEventId` — build causality trees

Pass `parentEventId` when emitting events to build parent-child relationships. This creates a trace tree showing which events triggered which:

```typescript
const run = await drip.startRun({ customerId: customer.id, workflowId: 'pipeline' });

// Root event
const planning = await drip.emitEvent({
  runId: run.id,
  eventType: 'llm.call',
  quantity: 500,
  units: 'tokens',
});

// Child events — triggered by the planning step
await drip.emitEvent({
  runId: run.id,
  eventType: 'tool.call',
  quantity: 1,
  parentEventId: planning.id, // links to parent
});
```

Use `getEventTrace(eventId)` to retrieve the full tree (ancestors, children, retry chain).

### `spanId` — OpenTelemetry linking

Pass `spanId` to link Drip events with specific OpenTelemetry spans in your APM:

```typescript
await drip.emitEvent({
  runId: run.id,
  eventType: 'llm.call',
  quantity: 1700,
  units: 'tokens',
  correlationId: span.spanContext().traceId,
  spanId: span.spanContext().spanId, // links to a specific OTel span
});
```

### `getRunTimeline()` options

The timeline endpoint accepts pagination and filtering options:

```typescript
const timeline = await drip.getRunTimeline('run_abc123', {
  limit: 50,              // max events to return (default: all)
  cursor: 'evt_xyz...',   // pagination cursor from previous response
  includeAnomalies: true, // include anomaly detection results (default: false)
  collapseRetries: true,  // group retried events together (default: false)
});

for (const event of timeline.events) {
  console.log(`${event.eventType}: ${event.outcome} (${event.durationMs}ms)`);
  if (event.parentEventId) {
    console.log(`  └─ child of ${event.parentEventId}`);
  }
}
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `limit` | `number` | all | Max events to return |
| `cursor` | `string` | — | Pagination cursor |
| `includeAnomalies` | `boolean` | `false` | Include anomaly detection results |
| `collapseRetries` | `boolean` | `false` | Group retried events into a single entry |

---

## Event Querying

Query execution events recorded via `emitEvent()` or `emitEventsBatch()`.

```typescript
// List all events for a customer
const { data: events, total } = await drip.listEvents({
  customerId: customer.id,
});
console.log(`${total} events found`);

// Filter by event type and outcome
const failures = await drip.listEvents({
  customerId: customer.id,
  eventType: 'tool_call',
  outcome: 'FAILURE',
  limit: 10,
});

// Get full details for a single event
const event = await drip.getEvent(events[0].id);
console.log(`${event.eventType}: ${event.outcome}`);

// Get causality trace — shows parent chain, children, and retries
const trace = await drip.getEventTrace(events[0].id);
console.log(`Ancestors: ${trace.ancestors.length}, Children: ${trace.children.length}`);
```

---

## Error Handling

```typescript
import { Drip, DripError } from '@drip-sdk/node';

const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

try {
  await drip.charge({
    customerId: customer.id,
    meter: 'api_calls',
    quantity: 1,
  });
} catch (error) {
  if (error instanceof DripError) {
    console.error(`Error: ${error.message} (${error.code})`);

    // Handle public key access errors
    if (error.code === 'PUBLIC_KEY_NOT_ALLOWED') {
      console.error('This operation requires a secret key (sk_)');
    }
  }
}
```

### Error codes

The `DripError` object includes a machine-readable `code` field. Common codes you may encounter:

| Code | HTTP | Meaning | What to do |
|------|------|---------|------------|
| `CUSTOMER_NOT_FOUND` | 404 | Customer ID doesn't exist | Create customer first with `createCustomer()` |
| `DUPLICATE_CUSTOMER` | 409 | Customer already exists | Use `getOrCreateCustomer()` for idempotent creation |
| `CUSTOMER_BLOCKED` | 403 | Customer is blocked/suspended | Contact support |
| `INTERNAL_CUSTOMER` | 400 | Trying to bill an internal-only customer | Use a non-internal customer for billing |
| `PUBLIC_KEY_NOT_ALLOWED` | 403 | Using a public key for a secret-key-only endpoint | Switch to `sk_` key |
| `UNAUTHORIZED` | 401 | Invalid or missing API key | Check `DRIP_API_KEY` |
| `VALIDATION_ERROR` | 422 | Missing or invalid request fields | Check `error.data` for field-level details |
| `INVALID_PARAMETER` | 400 | Parameter value out of range | Check parameter constraints |
| `NOT_FOUND` | 404 | Resource doesn't exist | Verify the ID |
| `DUPLICATE_PRICING_PLAN` | 409 | Pricing plan for this unit type exists | Fetch existing plan |
| `INSUFFICIENT_BALANCE` | 400 | Customer balance too low | Top up via checkout or deposit |
| `PAYMENT_REQUIRED` | 402 | Charge requires payment | Customer needs funds |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests | Back off and retry (SDK handles automatically) |
| `SERVICE_UNAVAILABLE` | 503 | Temporary server issue | Retry with backoff |
| `WEBHOOK_LIMIT_EXCEEDED` | 400 | Too many webhooks configured | Delete unused webhooks |
| `PROVISIONING_FAILED` | 500 | Smart account provisioning failed | Retry or check network status |

```typescript
try {
  await drip.charge({ customerId, meter: 'tokens', quantity: 100 });
} catch (error) {
  if (error instanceof DripError) {
    switch (error.code) {
      case 'CUSTOMER_NOT_FOUND':
        // Create customer first
        break;
      case 'INSUFFICIENT_BALANCE':
      case 'PAYMENT_REQUIRED':
        // Redirect to checkout
        break;
      case 'RATE_LIMIT_EXCEEDED':
        // SDK retries automatically, but you hit the limit
        break;
      default:
        console.error(`${error.code}: ${error.message}`);
    }
  }
}
```

---

## Gotchas

### Idempotency

The API requires an `idempotencyKey` on every mutating request (`charge`, `trackUsage`, `emitEvent`, and each event in `emitEventsBatch`). The SDK **always generates one automatically** if you don't provide it — so zero configuration is needed for basic use. Pass your own key when you need application-level deduplication across process restarts:

```typescript
const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

await drip.charge({
  customerId: customer.id,
  meter: 'api_calls',
  quantity: 1,
  idempotencyKey: 'req_abc123_step_1',
});
```

### Public Key Restrictions

Public keys (`pk_`) are not the right credential for the onboarding, billing, and observability flows in this guide. Use an Operator/Admin secret key (`sk_*`) for customer, usage, charge, run, event, pricing-plan, and webhook operations. Depending on the method, a `pk_*` call may fail locally with `PUBLIC_KEY_NOT_ALLOWED` or be rejected by the API with `403 FORBIDDEN`.

```typescript
// Right — use a secret key for billing/admin operations
const drip = new Drip({ apiKey: 'sk_live_...' });
await drip.createWebhook({ ... });
```

### Rate Limits

If you hit 429, back off and retry. The SDK handles this automatically with exponential backoff.

### trackUsage vs charge

- `trackUsage()` = logging (free, no balance impact)
- `trackUsage()` (default mode) = billing (deducts from balance when pricing plan matches)

Start with `trackUsage({ mode: 'internal' })` during pilots. Drop `mode` (default = billing) when ready to bill.

---

## TypeScript Support

Full TypeScript support with exported types:

```typescript
import type {
  Customer,
  Charge,
  ChargeResult,
  TrackUsageParams,
  RunResult,
  Webhook,
} from '@drip-sdk/node';
```

---

## Requirements

- Node.js 18.0.0 or higher (SDK supports Node 18+; the Drip monorepo uses Node 24.x)

## Links

- [Core SDK (README)](./README.md)
- [API Reference](https://docs.drippay.dev/api-reference)
- [GitHub](https://github.com/MichaelLevin5908/drip)
- [npm](https://www.npmjs.com/package/@drip-sdk/node)
