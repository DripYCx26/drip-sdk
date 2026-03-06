# Drip SDK (Node.js) — Full SDK Reference

This document covers billing, webhooks, and advanced features. For usage tracking and execution logging, see the main [README](./README.md).

---

## Contents

- [Installation](#installation)
- [Billing Lifecycle](#billing-lifecycle)
- [Quick Start](#quick-start)
- [Use Cases](#use-cases)
- [API Reference](#api-reference)
- [Subscription Billing](#subscription-billing)
- [Entitlements](#entitlements-pre-request-authorization)
- [Streaming Meter](#streaming-meter-llm-token-streaming)
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

// Secret key — full access (server-side only)
const drip = new Drip({ apiKey: 'sk_live_...' });

// Public key — usage, customers, billing (safe for client-side)
const drip = new Drip({ apiKey: 'pk_live_...' });
```

> **Key type detection:** The SDK auto-detects your key type from the prefix. Check `drip.keyType` to see if you're using a `'secret'`, `'public'`, or `'unknown'` key. Secret-key-only methods (webhooks, API key management, feature flags) will throw `DripError(403, 'PUBLIC_KEY_NOT_ALLOWED')` if called with a public key.

---

## Billing Lifecycle

Understanding `trackUsage` vs `charge`:

| Method | What it does |
|--------|--------------|
| `trackUsage()` | Logs usage to the ledger (no billing) |
| `charge()` | Converts usage into a billable charge |
| `createSubscription()` | Creates a recurring subscription (auto-charges on interval) |

**Typical flow:**

1. `trackUsage()` throughout the day/request stream
2. Optionally `estimateFromUsage()` to preview cost
3. `charge()` to create billable charges
4. `getBalance()` / `listCharges()` for reconciliation
5. Webhooks for `charge.succeeded` / `charge.failed`

> Most pilots start with `trackUsage()` only. Add `charge()` when you're ready to bill.

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
| `trackUsage(params)` | Log usage to ledger (no billing) |
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
| `emitEvent(params)` | Log event within run |
| `emitEventsBatch(params)` | Batch log events |
| `endRun(runId, params)` | Complete execution trace |
| `getRun(runId)` | Get run details |
| `getRunTimeline(runId)` | Get execution timeline |
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
| `listMeters()` | List available meters |
| `ping()` | Verify API connection |

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

Entitlement counters are automatically incremented when you call `charge()` — no extra work needed.

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

> **Setup:** Entitlement plans, rules, and customer assignments are managed via the REST API. See the [Entitlements guide](../../docs/integration/ENTITLEMENTS.md) for full API reference and setup walkthrough.

---

## Streaming Meter (LLM Token Streaming)

For LLM token streaming, accumulate usage locally and flush once:

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
await meter.flush();
```

---

## Framework Middleware

### Next.js

```typescript
import { withDrip } from '@drip-sdk/node/next';

export const POST = withDrip({
  meter: 'api_calls',
  quantity: 1,
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

Public keys (`pk_`) cannot access webhook, API key, or feature flag management endpoints. If you see `PUBLIC_KEY_NOT_ALLOWED` (403), switch to a secret key (`sk_`):

```typescript
// Wrong — public keys can't manage webhooks
const drip = new Drip({ apiKey: 'pk_live_...' });
await drip.createWebhook({ ... }); // Throws DripError(403)

// Right — use a secret key for admin operations
const drip = new Drip({ apiKey: 'sk_live_...' });
await drip.createWebhook({ ... }); // Works
```

### Rate Limits

If you hit 429, back off and retry. The SDK handles this automatically with exponential backoff.

### trackUsage vs charge

- `trackUsage()` = logging (free, no balance impact)
- `charge()` = billing (deducts from balance)

Start with `trackUsage()` during pilots. Add `charge()` when ready to bill.

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
