/**
 * Drip SDK Core - Essential API for pilots and new integrations
 *
 * This SDK focuses on two core concepts:
 * - **Usage tracking**: trackUsage() for recording usage without billing
 * - **Execution logging**: recordRun() and related methods for tracking runs/events
 *
 * For billing, webhooks, cost estimation, and advanced features:
 * `import { Drip } from '@drip-sdk/node'`
 *
 * @packageDocumentation
 */

import { deterministicIdempotencyKey } from './idempotency.js';
export { DripError } from './errors.js';

// ============================================================================
// Configuration Types
// ============================================================================

/**
 * Configuration options for the Drip SDK client.
 *
 * All fields are optional - the SDK will read from environment variables:
 * - `DRIP_API_KEY` - Your Drip API key
 * - `DRIP_BASE_URL` - Override API base URL (optional)
 */
export interface DripConfig {
  /**
   * Your Drip API key. Obtain this from the Drip dashboard.
   * Falls back to `DRIP_API_KEY` environment variable if not provided.
   *
   * Supports both key types:
   * - **Secret keys** (`sk_live_...` / `sk_test_...`): Full access to all endpoints
   * - **Public keys** (`pk_live_...` / `pk_test_...`): Client-safe identifiers only.
   *   The customer, usage, and run/event flows exposed by this SDK should use
   *   a secret key.
   *
   * @example "sk_live_abc123..." or "pk_live_abc123..."
   */
  apiKey?: string;

  /**
   * Base URL for the Drip API. Defaults to production API.
   * Falls back to `DRIP_BASE_URL` environment variable if not provided.
   * @default "https://api.drippay.dev/v1"
   */
  baseUrl?: string;

  /**
   * Request timeout in milliseconds.
   * @default 30000
   */
  timeout?: number;
}

// ============================================================================
// Customer Types
// ============================================================================

/**
 * Parameters for creating a new customer.
 */
export interface CreateCustomerParams {
  /**
   * Your internal customer/user ID for reconciliation.
   * At least one of `externalCustomerId` or `onchainAddress` is required.
   * @example "user_12345"
   */
  externalCustomerId?: string;

  /**
   * The customer's Drip Smart Account address (derived from their EOA).
   * At least one of `externalCustomerId` or `onchainAddress` is required.
   * @example "0x1234567890abcdef..."
   */
  onchainAddress?: string;

  /**
   * Whether this customer is internal-only (usage tracked but not billed).
   * @default false
   */
  isInternal?: boolean;

  /**
   * Additional metadata to store with the customer.
   */
  metadata?: Record<string, unknown>;
}

/**
 * A Drip customer record.
 */
export interface Customer {
  /** Unique customer ID in Drip */
  id: string;

  /** Your business ID (optional - may not be returned by all endpoints) */
  businessId?: string;

  /** Your external customer ID (if provided) */
  externalCustomerId: string | null;

  /** Customer's on-chain address (null for internal-only customers) */
  onchainAddress: string | null;

  /** Whether this customer is internal-only (usage tracked but not billed) */
  isInternal?: boolean;

  /** Custom metadata */
  metadata: Record<string, unknown> | null;

  /** ISO timestamp of creation */
  createdAt: string;

  /** ISO timestamp of last update */
  updatedAt: string;
}

/**
 * Options for listing customers.
 */
export interface ListCustomersOptions {
  /**
   * Maximum number of customers to return (1-100).
   * @default 100
   */
  limit?: number;

  /**
   * Filter by customer status.
   */
  status?: 'ACTIVE' | 'LOW_BALANCE' | 'PAUSED';
}

/**
 * Response from listing customers.
 */
export interface ListCustomersResponse {
  /** Array of customers */
  data: Customer[];

  /** Total count returned */
  count: number;
}

// ============================================================================
// Usage Tracking Types
// ============================================================================

export type TrackUsageMode = 'batch' | 'sync';

/**
 * Parameters for tracking usage without billing.
 */
export interface TrackUsageParams {
  /**
   * The Drip customer ID to track usage for.
   */
  customerId: string;

  /**
   * The meter/usage type (e.g., 'api_calls', 'tokens').
   */
  meter: string;

  /**
   * The quantity of usage to record.
   */
  quantity: number;

  /**
   * Unique key to prevent duplicate records.
   * Auto-generated if not provided, ensuring every call is individually trackable.
   */
  idempotencyKey?: string;

  /**
   * Human-readable unit label (e.g., 'tokens', 'requests').
   */
  units?: string;

  /**
   * Human-readable description of this usage event.
   */
  description?: string;

  /**
   * Additional metadata to attach to this usage event.
   */
  metadata?: Record<string, unknown>;

  /**
   * Write mode for usage tracking.
   *
   * - `sync` (default): persist immediately via `/usage/internal`
   * - `batch`: enqueue for high-throughput bulk persistence via
   *   `/usage/internal/batch` and return immediately
   */
  mode?: TrackUsageMode;
}

export type TrackUsageSyncParams = TrackUsageParams & { mode?: 'sync' };
export type TrackUsageBatchParams = Omit<TrackUsageParams, 'mode'> & { mode: 'batch' };

interface BaseTrackUsageResult {
  /** Whether the usage was recorded */
  success: boolean;

  /** Customer ID */
  customerId: string;

  /** Usage type that was recorded */
  usageType: string;

  /** Quantity recorded */
  quantity: number;

  /** Confirmation message */
  message: string;
}

/**
 * Result of tracking usage synchronously (legacy/default behavior).
 */
export interface TrackUsageSyncResult extends BaseTrackUsageResult {
  /** The usage event ID */
  usageEventId: string;

  /** Whether this customer is internal-only */
  isInternal: boolean;
}

/**
 * Result of tracking usage in batch mode.
 */
export interface TrackUsageBatchResult extends BaseTrackUsageResult {
  /** Explicit batch mode marker */
  mode: 'batch';

  /** Whether this customer is internal-only, when returned by the API */
  isInternal?: undefined;

  /** Idempotency key for queued batch writes */
  idempotencyKey: string;

  /** Number of pending queued events for batch writes */
  pendingEvents: number;

  /** Not assigned until the event is flushed */
  usageEventId?: undefined;
}

/**
 * Result of tracking usage (no billing).
 */
export type TrackUsageResult = TrackUsageSyncResult | TrackUsageBatchResult;

// ============================================================================
// Run & Event Types (Execution Ledger)
// ============================================================================

/**
 * Parameters for starting a new run.
 */
export interface StartRunParams {
  /** Customer ID this run belongs to */
  customerId: string;

  /** Workflow ID this run executes */
  workflowId: string;

  /** Your external run ID for correlation */
  externalRunId?: string;

  /** Correlation ID for distributed tracing */
  correlationId?: string;

  /** Parent run ID for nested runs */
  parentRunId?: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Possible run statuses.
 */
export type RunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMEOUT';

/**
 * Result of starting a run.
 */
export interface RunResult {
  id: string;
  customerId: string;
  workflowId: string;
  workflowName: string;
  status: RunStatus;
  correlationId: string | null;
  createdAt: string;
}

/**
 * Parameters for ending/updating a run.
 */
export interface EndRunParams {
  /** New status for the run */
  status: 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';

  /** Error message if failed */
  errorMessage?: string;

  /** Error code for categorization */
  errorCode?: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Result of ending a run.
 */
export interface EndRunResult {
  id: string;
  status: RunStatus;
  endedAt: string | null;
  durationMs: number | null;
  eventCount: number;
  totalCostUnits: string | null;
}

/**
 * Parameters for emitting an event to a run.
 */
export interface EmitEventParams {
  /** Run ID to attach this event to */
  runId: string;

  /** Event type (e.g., "request.start", "llm.call") */
  eventType: string;

  /** Quantity of units consumed */
  quantity?: number;

  /** Human-readable unit label */
  units?: string;

  /** Human-readable description */
  description?: string;

  /** Cost in abstract units */
  costUnits?: number;

  /** Currency for cost */
  costCurrency?: string;

  /** Correlation ID for tracing */
  correlationId?: string;

  /** Parent event ID for trace tree */
  parentEventId?: string;

  /** OpenTelemetry-style span ID */
  spanId?: string;

  /** Idempotency key */
  idempotencyKey?: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Result of emitting an event.
 */
export interface EventResult {
  id: string;
  runId: string;
  eventType: string;
  quantity: number;
  costUnits: number | null;
  isDuplicate: boolean;
  timestamp: string;
}

/**
 * A single event to record in a run.
 */
export interface RecordRunEvent {
  /** Event type (e.g., "request.start", "llm.call", "request.end") */
  eventType: string;

  /** Quantity of units consumed */
  quantity?: number;

  /** Human-readable unit label */
  units?: string;

  /** Human-readable description */
  description?: string;

  /** Cost in abstract units */
  costUnits?: number;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Parameters for recording a complete run in one call.
 */
export interface RecordRunParams {
  /** Customer ID this run belongs to */
  customerId: string;

  /**
   * Workflow/request type identifier. Examples:
   * - "rpc-request" for RPC providers
   * - "api-request" for API providers
   * - "agent-run" for AI agents
   *
   * Auto-creates if it doesn't exist.
   */
  workflow: string;

  /** Events that occurred during the run */
  events: RecordRunEvent[];

  /** Final status of the run */
  status: 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';

  /** Error message if status is FAILED */
  errorMessage?: string;

  /** Error code if status is FAILED */
  errorCode?: string;

  /** Your external run ID for correlation */
  externalRunId?: string;

  /** Correlation ID for distributed tracing */
  correlationId?: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Result of recording a run.
 */
export interface RecordRunResult {
  /** The created run */
  run: {
    id: string;
    workflowId: string;
    workflowName: string;
    status: RunStatus;
    durationMs: number | null;
  };

  /** Summary of events created */
  events: {
    created: number;
    duplicates: number;
  };

  /** Total cost computed */
  totalCostUnits: string | null;

  /** Human-readable summary */
  summary: string;
}

/**
 * Full run timeline response from GET /runs/:id/timeline.
 */
export interface RunTimeline {
  runId: string;
  workflowId: string | null;
  customerId: string;
  status: RunStatus;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  events: Array<{
    id: string;
    eventType: string;
    actionName: string | null;
    outcome: 'SUCCESS' | 'FAILED' | 'PENDING' | 'TIMEOUT' | 'RETRYING';
    explanation: string | null;
    description: string | null;
    timestamp: string;
    durationMs: number | null;
    parentEventId: string | null;
    retryOfEventId: string | null;
    attemptNumber: number;
    retriedByEventId: string | null;
    costUsdc: string | null;
    isRetry: boolean;
    retryChain: {
      totalAttempts: number;
      finalOutcome: string;
      events: string[];
    } | null;
    metadata: {
      usageType: string;
      quantity: number;
      units: string | null;
    } | null;
  }>;
  anomalies: Array<{
    id: string;
    type: string;
    severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    title: string;
    explanation: string;
    relatedEventIds: string[];
    detectedAt: string;
    status: 'OPEN' | 'INVESTIGATING' | 'RESOLVED' | 'FALSE_POSITIVE' | 'IGNORED';
  }>;
  summary: {
    totalEvents: number;
    byType: Record<string, number>;
    byOutcome: Record<string, number>;
    retriedEvents: number;
    failedEvents: number;
    totalCostUsdc: string | null;
  };
  hasMore: boolean;
  nextCursor: string | null;
}

/**
 * Run details response from GET /runs/:id.
 */
export interface RunDetails {
  id: string;
  customerId: string;
  customerName: string | null;
  workflowId: string;
  workflowName: string;
  status: RunStatus;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  errorMessage: string | null;
  errorCode: string | null;
  correlationId: string | null;
  metadata: Record<string, unknown> | null;
  totals: {
    eventCount: number;
    totalQuantity: string;
    totalCostUnits: string;
  };
  _links: {
    timeline: string;
  };
}

// ============================================================================
// Entitlement Types
// ============================================================================

// ============================================================================
// Customer Spending Cap Types
// ============================================================================

/** Cap types for per-customer spending limits */
export type SpendingCapType = 'DAILY_CHARGE_LIMIT' | 'MONTHLY_CHARGE_LIMIT' | 'SINGLE_CHARGE_LIMIT';

/** Parameters for setting a per-customer spending cap. */
export interface SetSpendingCapParams {
  /** Cap type: daily, monthly, or single-charge limit */
  capType: SpendingCapType;
  /** Spending limit in USDC */
  limitValue: number;
  /** Auto-block charges when cap is reached (default: true) */
  autoBlock?: boolean;
}

/** A per-customer spending cap with current usage tracking. */
export interface CustomerSpendingCap {
  id: string;
  capType: string;
  limitValue: string;
  currentUsage: string;
  periodStart: string;
  isActive: boolean;
  autoBlock: boolean;
  lastAlertLevel: string | null;
}

// ============================================================================
// Entitlement Types
// ============================================================================

/**
 * Parameters for checking a customer's entitlement to use a feature.
 */
export interface CheckEntitlementParams {
  /** The Drip customer ID */
  customerId: string;

  /** Feature key to check (e.g., "search", "api_calls", "tokens") */
  featureKey: string;

  /** Quantity to check against the limit (default: 1) */
  quantity?: number;
}

/**
 * Result of an entitlement check.
 */
export interface EntitlementCheckResult {
  /** Whether the customer is allowed to use this feature */
  allowed: boolean;

  /** The feature that was checked */
  featureKey: string;

  /** Remaining quota in the current period (-1 if unlimited) */
  remaining: number;

  /** The limit for this period (-1 if unlimited) */
  limit: number;

  /** Whether the customer has unlimited access */
  unlimited: boolean;

  /** The period this limit applies to */
  period: 'DAILY' | 'MONTHLY';

  /** When the current period resets (ISO timestamp) */
  periodResetsAt: string;

  /** Reason for denial (only present when allowed=false) */
  reason?: string;
}

// ============================================================================
// Customer Plan Change Types
// ============================================================================

/**
 * A single price override (what a customer pays for one unit type).
 */
export interface PriceOverrideInput {
  /** The unit type to override (e.g. "api_call", "token"). */
  unitType: string;
  /** The per-unit price the customer will pay, as a decimal string. */
  unitPriceUsd: string;
}

/**
 * Per-feature entitlement override (overrides the rule's default limit).
 */
export interface EntitlementOverrideInput {
  /** Override for rules with period=DAILY. */
  dailyLimit?: number;
  /** Override for rules with period=MONTHLY. */
  monthlyLimit?: number;
  /** Mark the feature as unlimited for this customer. */
  unlimited?: boolean;
}

/**
 * Parameters for changing a customer's pricing without grandfathering.
 *
 * Combine `sourcePricingPlanIds` (copy prices from existing plans) with
 * explicit `priceOverrides` (last-write-wins on conflict), and/or adjust
 * commercial terms. At least one field is required.
 */
export interface ApplyPricingChangeParams {
  /**
   * Copy unit prices from these pricing plans onto the customer's contract
   * as overrides. Use this to "move customer X onto Plan Y" without touching
   * the plan itself or any other customer.
   */
  sourcePricingPlanIds?: string[];

  /**
   * Explicit per-unit-type overrides. Combined with `sourcePricingPlanIds`;
   * values here win on conflict.
   */
  priceOverrides?: PriceOverrideInput[];

  /**
   * When true, any existing overrides on the customer's contract are
   * cleared before the new set is written. Default: merge.
   */
  replaceAll?: boolean;

  /** Blanket discount percentage (e.g. "15" = 15%). */
  discountPct?: string;
  /** Minimum spend per period (USDC). */
  minimumUsdc?: string;
  /** Maximum spend per period (USDC). */
  maximumUsdc?: string;
  /** Included free units per period (e.g. `{ api_call: 10000 }`). */
  includedUnits?: Record<string, number>;

  /**
   * Compute proration against the customer's current subscription period.
   * Returns proration fields on the change row. No-op for pure metered
   * customers with no subscription.
   */
  prorate?: boolean;

  /**
   * Exact net proration amount to use instead of computing from the
   * subscription delta. Sign encodes direction: positive = CHARGE,
   * negative = CREDIT, "0" = ZERO (skip ledger entry). Use this for
   * multi-unit changes where summing override prices would produce a
   * meaningless baseline.
   */
  prorationAmountOverride?: string;

  /** Effective date (ISO string). v1 applies immediately regardless. */
  effectiveDate?: string;

  /** Human-readable reason, surfaced in the audit row. */
  reason?: string;
  /** Free-form actor identifier for audit. */
  performedBy?: string;
}

/**
 * Parameters for changing a customer's entitlement plan.
 */
export interface ApplyEntitlementChangeParams {
  /** Target entitlement plan ID. Required. */
  planId: string;
  /** Per-feature overrides. Replaces existing overrides wholesale. */
  overrides?: Record<string, EntitlementOverrideInput>;
  /** Human-readable reason, surfaced in the audit row. */
  reason?: string;
  /** Free-form actor identifier for audit. */
  performedBy?: string;
}

/**
 * The type of change recorded on a CustomerPlanChange row.
 */
export type PlanChangeType = 'PRICING' | 'ENTITLEMENT' | 'BOTH';

/**
 * Current status of a recorded plan change.
 */
export type PlanChangeStatus = 'APPLIED' | 'ROLLED_BACK' | 'SUPERSEDED';

/**
 * Direction of a prorated amount.
 */
export type PlanChangeProrationDirection = 'NONE' | 'CREDIT' | 'CHARGE' | 'ZERO';

/**
 * A recorded pricing or entitlement change on a customer.
 */
export interface CustomerPlanChange {
  id: string;
  businessId: string;
  customerId: string;
  changeType: PlanChangeType;
  status: PlanChangeStatus;
  effectiveFrom: string;
  reason: string | null;
  /** Full snapshot of the previous pricing/entitlement state. */
  previousState: unknown;
  /** Full snapshot of the new pricing/entitlement state. */
  newState: unknown;
  prorationAmountUsd: string | null;
  prorationDirection: PlanChangeProrationDirection | null;
  proratedDays: number | null;
  totalPeriodDays: number | null;
  prorationPeriodStart: string | null;
  prorationPeriodEnd: string | null;
  prorationChargeId: string | null;
  contractId: string | null;
  performedBy: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * Response shape for `listCustomerPlanChanges`.
 */
export interface ListCustomerPlanChangesResult {
  data: CustomerPlanChange[];
  total: number;
}

/**
 * Options for `listCustomerPlanChanges`.
 */
export interface ListCustomerPlanChangesOptions {
  /** Max rows to return (1..200, default 50). */
  limit?: number;
  /** Offset for pagination (default 0). */
  offset?: number;
}

// ============================================================================
// Internal Types (used by recordRun)
// ============================================================================

interface Workflow {
  id: string;
  name: string;
  slug: string;
  productSurface: string;
  description: string | null;
  isActive: boolean;
  createdAt: string;
}

interface CreateWorkflowParams {
  name: string;
  slug: string;
  productSurface?: 'API' | 'RPC' | 'WEBHOOK' | 'AGENT' | 'PIPELINE' | 'CUSTOM';
  description?: string;
  metadata?: Record<string, unknown>;
}

// ============================================================================
// Error Types
// ============================================================================

import { DripError } from './errors.js';

// ============================================================================
// Core SDK Class
// ============================================================================

/**
 * Drip SDK Core - Essential API for pilots and new integrations.
 *
 * Two core concepts:
 * - **Usage tracking**: `trackUsage()` - record usage without billing
 * - **Execution logging**: `recordRun()` - track request/run lifecycle with events
 *
 * For billing (`charge()`), webhooks, and advanced features:
 * ```typescript
 * import { Drip } from '@drip-sdk/node';
 * ```
 *
 * @example
 * ```typescript
 * import { Drip } from '@drip-sdk/node/core';
 *
 * const drip = new Drip({ apiKey: process.env.DRIP_API_KEY! });
 *
 * // Verify connection
 * const health = await drip.ping();
 * console.log(`API healthy: ${health.ok}`);
 *
 * // Track usage (no billing)
 * await drip.trackUsage({
 *   customerId: 'cust_123',
 *   meter: 'api_calls',
 *   quantity: 1,
 * });
 *
 * // Record a complete request/run with events
 * const result = await drip.recordRun({
 *   customerId: 'cust_123',
 *   workflow: 'rpc-request',  // or 'api-request', 'agent-run'
 *   events: [
 *     { eventType: 'request.start' },
 *     { eventType: 'llm.call', quantity: 1500, units: 'tokens' },
 *     { eventType: 'request.end' },
 *   ],
 *   status: 'COMPLETED',
 * });
 *
 * console.log(result.summary);
 * ```
 */
export class Drip {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeout: number;

  /**
   * The type of API key being used.
   *
   * - `'secret'` — Full access (sk_live_... / sk_test_...)
   * - `'public'` — Client-safe, restricted access (pk_live_... / pk_test_...)
   * - `'unknown'` — Key format not recognized (legacy or custom)
   */
  readonly keyType: 'secret' | 'public' | 'unknown';

  /**
   * Creates a new Drip SDK client.
   *
   * @param config - Configuration options (all optional, reads from env vars)
   * @throws {Error} If apiKey is not provided and DRIP_API_KEY env var is not set
   *
   * @example
   * ```typescript
   * // Option 1: Explicit config
   * const drip = new Drip({ apiKey: 'your-api-key' });
   *
   * // Option 2: Auto-config from environment (recommended)
   * // Set DRIP_API_KEY env var, then:
   * const drip = new Drip();
   *
   * // Option 3: Use pre-initialized singleton
   * import { drip } from '@drip-sdk/node';
   * ```
   */
  constructor(config: DripConfig = {}) {
    // Read from config or fall back to environment variables
    const apiKey = config.apiKey ?? (typeof process !== 'undefined' ? process.env.DRIP_API_KEY : undefined);
    const baseUrl = config.baseUrl ?? (typeof process !== 'undefined' ? (process.env.DRIP_API_URL ?? process.env.DRIP_BASE_URL) : undefined);

    if (!apiKey) {
      throw new Error(
        'Drip API key is required. Either pass { apiKey } to constructor or set DRIP_API_KEY environment variable.'
      );
    }

    // Validate API key format early so typos are caught at construction time
    if (!apiKey.startsWith('sk_') && !apiKey.startsWith('pk_')) {
      throw new Error(
        `Invalid API key format: key must start with "sk_" (secret) or "pk_" (public). Got "${apiKey.slice(0, 8)}..."`
      );
    }
    if (apiKey.length < 10) {
      throw new Error(
        'Invalid API key: key is too short. Check that you copied the full key from the Drip dashboard.'
      );
    }

    this.apiKey = apiKey;
    this.baseUrl = baseUrl || 'https://api.drippay.dev/v1';
    this.timeout = config.timeout || 30000;

    // Detect key type from prefix
    if (apiKey.startsWith('sk_')) {
      this.keyType = 'secret';
    } else if (apiKey.startsWith('pk_')) {
      this.keyType = 'public';
    } else {
      this.keyType = 'unknown';
    }
  }

  /**
   * Asserts that the SDK was initialized with a secret key (sk_).
   * @internal
   */
  private assertSecretKey(operation: string): void {
    if (this.keyType === 'public') {
      throw new DripError(
        `${operation} requires a secret key (sk_). You are using a public key (pk_), which cannot access this endpoint. ` +
        'Use a secret key for administrative billing, customer, and entitlement operations.',
        403,
        'PUBLIC_KEY_NOT_ALLOWED',
      );
    }
  }

  /**
   * Makes an authenticated request to the Drip API.
   * @internal
   */
  private async request<T>(
    path: string,
    options: RequestInit = {},
  ): Promise<T> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiKey}`,
          ...options.headers,
        },
      });

      if (res.status === 204) {
        return { success: true } as T;
      }

      const data = await res.json();

      if (!res.ok) {
        throw new DripError(
          data.message || data.error || 'Request failed',
          res.status,
          data.code,
        );
      }

      return data as T;
    } catch (error) {
      if (error instanceof DripError) {
        throw error;
      }
      if (error instanceof Error && error.name === 'AbortError') {
        throw new DripError('Request timed out', 408, 'TIMEOUT');
      }
      throw new DripError(
        error instanceof Error ? error.message : 'Unknown error',
        0,
        'UNKNOWN',
      );
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // ==========================================================================
  // Health Check
  // ==========================================================================

  /**
   * Pings the Drip API to check connectivity and measure latency.
   *
   * Use this to verify:
   * - API key is valid
   * - Base URL is correct
   * - Network connectivity works
   *
   * @returns Health status with latency information
   * @throws {DripError} If the request fails or times out
   *
   * @example
   * ```typescript
   * const health = await drip.ping();
   * if (health.ok) {
   *   console.log(`API healthy, latency: ${health.latencyMs}ms`);
   * } else {
   *   console.error(`API unhealthy: ${health.status}`);
   * }
   * ```
   */
  async ping(): Promise<{ ok: boolean; status: string; latencyMs: number; timestamp: number }> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    let healthBaseUrl = this.baseUrl;
    if (healthBaseUrl.endsWith('/v1/')) {
      healthBaseUrl = healthBaseUrl.slice(0, -4);
    } else if (healthBaseUrl.endsWith('/v1')) {
      healthBaseUrl = healthBaseUrl.slice(0, -3);
    }
    healthBaseUrl = healthBaseUrl.replace(/\/+$/, '');

    const start = Date.now();

    try {
      const response = await fetch(`${healthBaseUrl}/health`, {
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
        },
      });
      const latencyMs = Date.now() - start;

      let status = 'unknown';
      let timestamp = Date.now();

      try {
        const data = await response.json() as { status?: string; timestamp?: number };
        if (typeof data.status === 'string') {
          status = data.status;
        }
        if (typeof data.timestamp === 'number') {
          timestamp = data.timestamp;
        }
      } catch {
        status = response.ok ? 'healthy' : `error:${response.status}`;
      }

      if (!response.ok && status === 'unknown') {
        status = `error:${response.status}`;
      }

      return {
        ok: response.ok && status === 'healthy',
        status,
        latencyMs,
        timestamp,
      };
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        throw new DripError('Request timed out', 408, 'TIMEOUT');
      }
      throw new DripError(
        error instanceof Error ? error.message : 'Unknown error',
        0,
        'UNKNOWN',
      );
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // ==========================================================================
  // Customer Methods
  // ==========================================================================

  /**
   * Creates a new customer in your Drip account.
   *
   * @param params - Customer creation parameters
   * @returns The created customer
   * @throws {DripError} If creation fails (e.g., duplicate customer)
   *
   * @example
   * ```typescript
   * const customer = await drip.createCustomer({
   *   onchainAddress: '0x1234567890abcdef...',
   *   externalCustomerId: 'user_123',
   * });
   * ```
   */
  async createCustomer(params: CreateCustomerParams): Promise<Customer> {
    this.assertSecretKey('createCustomer()');
    return this.request<Customer>('/customers', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Retrieves a customer by their Drip ID.
   *
   * @param customerId - The Drip customer ID
   * @returns The customer details
   * @throws {DripError} If customer not found (404)
   */
  async getCustomer(customerId: string): Promise<Customer> {
    this.assertSecretKey('getCustomer()');
    return this.request<Customer>(`/customers/${customerId}`);
  }

  /**
   * Lists all customers for your business.
   *
   * @param options - Optional filtering and pagination
   * @returns List of customers
   */
  async listCustomers(
    options?: ListCustomersOptions,
  ): Promise<ListCustomersResponse> {
    this.assertSecretKey('listCustomers()');
    const params = new URLSearchParams();

    if (options?.limit) {
      params.set('limit', options.limit.toString());
    }
    if (options?.status) {
      params.set('status', options.status);
    }

    const query = params.toString();
    const path = query ? `/customers?${query}` : '/customers';

    return this.request<ListCustomersResponse>(path);
  }

  // ==========================================================================
  // Customer Spending Caps
  // ==========================================================================

  /**
   * Sets a per-customer spending cap.
   *
   * Multi-level alerts fire at 50%, 80%, 95%, 100% via webhooks.
   *
   * @param customerId - The Drip customer ID
   * @param params - Cap parameters (capType, limitValue, autoBlock)
   * @returns The created/updated spending cap
   *
   * @example
   * ```typescript
   * const cap = await drip.setCustomerSpendingCap('cust_abc123', {
   *   capType: 'MONTHLY_CHARGE_LIMIT',
   *   limitValue: 500,
   * });
   * ```
   */
  async setCustomerSpendingCap(
    customerId: string,
    params: SetSpendingCapParams,
  ): Promise<CustomerSpendingCap> {
    this.assertSecretKey('setCustomerSpendingCap()');
    return this.request<CustomerSpendingCap>(
      `/customers/${customerId}/spending-cap`,
      { method: 'PUT', body: JSON.stringify(params) },
    );
  }

  /**
   * Lists all active spending caps for a customer.
   *
   * @param customerId - The Drip customer ID
   * @returns List of active spending caps with current usage
   */
  async getCustomerSpendingCaps(
    customerId: string,
  ): Promise<{ caps: CustomerSpendingCap[] }> {
    this.assertSecretKey('getCustomerSpendingCaps()');
    return this.request<{ caps: CustomerSpendingCap[] }>(
      `/customers/${customerId}/spending-caps`,
    );
  }

  /**
   * Removes a spending cap for a customer.
   *
   * @param customerId - The Drip customer ID
   * @param capId - The spending cap ID to remove
   */
  async removeCustomerSpendingCap(
    customerId: string,
    capId: string,
  ): Promise<{ success: boolean }> {
    this.assertSecretKey('removeCustomerSpendingCap()');
    return this.request<{ success: boolean }>(
      `/customers/${customerId}/spending-caps/${capId}`,
      { method: 'DELETE' },
    );
  }

  // ==========================================================================
  // Usage Tracking (No Billing)
  // ==========================================================================

  /**
   * Records usage for tracking WITHOUT billing.
   *
   * Use this for:
   * - Pilot programs (track before billing)
   * - Internal team usage
   * - Pre-billing tracking before customer setup
   *
   * For actual billing, use `charge()` from the full SDK.
   *
   * @param params - Usage tracking parameters
   * @returns The tracked usage event
   *
   * @example
   * ```typescript
   * const result = await drip.trackUsage({
   *   customerId: 'cust_abc123',
   *   meter: 'api_calls',
   *   quantity: 100,
   *   description: 'API calls during pilot',
   * });
   *
   * if (result.mode === 'sync') {
   *   console.log(`Tracked: ${result.usageEventId}`);
   * } else {
   *   console.log(`Queued with key: ${result.idempotencyKey}`);
   * }
   * ```
   */
  async trackUsage(params: TrackUsageBatchParams): Promise<TrackUsageBatchResult>;
  async trackUsage(params: TrackUsageSyncParams): Promise<TrackUsageSyncResult>;
  async trackUsage(params: TrackUsageParams): Promise<TrackUsageResult> {
    const idempotencyKey = params.idempotencyKey
      ?? deterministicIdempotencyKey('track', params.customerId, params.meter, params.quantity);
    const mode = params.mode ?? 'sync';
    const path = mode === 'sync' ? '/usage/internal' : '/usage/internal/batch';

    const result = await this.request<TrackUsageResult>(path, {
      method: 'POST',
      body: JSON.stringify({
        customerId: params.customerId,
        usageType: params.meter,
        quantity: params.quantity,
        idempotencyKey,
        units: params.units,
        description: params.description,
        metadata: params.metadata,
      }),
    });

    if (mode === 'batch') {
      return {
        ...(result as TrackUsageBatchResult),
        mode: 'batch',
      };
    }

    return result as TrackUsageSyncResult;
  }

  // ==========================================================================
  // Private Workflow Methods (used by recordRun)
  // ==========================================================================

  private async createWorkflow(params: CreateWorkflowParams): Promise<Workflow> {
    return this.request<Workflow>('/workflows', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  private async listWorkflows(): Promise<{ data: Workflow[]; count: number }> {
    return this.request<{ data: Workflow[]; count: number }>('/workflows');
  }

  // ==========================================================================
  // Run & Event Methods (Execution Ledger)
  // ==========================================================================

  /**
   * Starts a new run for tracking execution.
   *
   * @param params - Run parameters
   * @returns The started run
   *
   * @example
   * ```typescript
   * const run = await drip.startRun({
   *   customerId: 'cust_abc123',
   *   workflowId: 'wf_xyz789',
   * });
   *
   * // Emit events during execution...
   * await drip.emitEvent({ runId: run.id, eventType: 'llm.call', quantity: 1000 });
   *
   * // End the run
   * await drip.endRun(run.id, { status: 'COMPLETED' });
   * ```
   */
  async startRun(params: StartRunParams): Promise<RunResult> {
    return this.request<RunResult>('/runs', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Ends a run with a final status.
   *
   * @param runId - The run ID to end
   * @param params - End parameters including status
   * @returns Updated run info
   */
  async endRun(runId: string, params: EndRunParams): Promise<EndRunResult> {
    return this.request<EndRunResult>(`/runs/${runId}`, {
      method: 'PATCH',
      body: JSON.stringify(params),
    });
  }

  /**
   * Gets run details with summary totals.
   *
   * For full event history with retry chains and anomalies, use `getRunTimeline()`.
   *
   * @param runId - The run ID
   * @returns Run details with totals
   *
   * @example
   * ```typescript
   * const run = await drip.getRun('run_abc123');
   * console.log(`Status: ${run.status}, Events: ${run.totals.eventCount}`);
   * ```
   */
  async getRun(runId: string): Promise<RunDetails> {
    return this.request<RunDetails>(`/runs/${runId}`);
  }

  /**
   * Gets a run's full timeline with events, anomalies, and analytics.
   *
   * @param runId - The run ID
   * @param options - Pagination and filtering options
   * @returns Full timeline with events, anomalies, and summary
   *
   * @example
   * ```typescript
   * const timeline = await drip.getRunTimeline('run_abc123');
   *
   * console.log(`Status: ${timeline.status}`);
   * console.log(`Events: ${timeline.summary.totalEvents}`);
   *
   * for (const event of timeline.events) {
   *   console.log(`${event.eventType}: ${event.outcome}`);
   * }
   * ```
   */
  async getRunTimeline(
    runId: string,
    options?: { limit?: number; cursor?: string; includeAnomalies?: boolean; collapseRetries?: boolean },
  ): Promise<RunTimeline> {
    const params = new URLSearchParams();
    if (options?.limit) params.set('limit', options.limit.toString());
    if (options?.cursor) params.set('cursor', options.cursor);
    if (options?.includeAnomalies !== undefined) params.set('includeAnomalies', String(options.includeAnomalies));
    if (options?.collapseRetries !== undefined) params.set('collapseRetries', String(options.collapseRetries));

    const query = params.toString();
    const path = query ? `/runs/${runId}/timeline?${query}` : `/runs/${runId}/timeline`;

    return this.request<RunTimeline>(path);
  }

  /**
   * Emits an event to a run.
   *
   * @param params - Event parameters
   * @returns The created event
   *
   * @example
   * ```typescript
   * await drip.emitEvent({
   *   runId: run.id,
   *   eventType: 'llm.call',
   *   quantity: 1500,
   *   units: 'tokens',
   *   description: 'GPT-4 completion',
   * });
   * ```
   */
  async emitEvent(params: EmitEventParams): Promise<EventResult> {
    const idempotencyKey = params.idempotencyKey
      ?? deterministicIdempotencyKey('evt', params.runId, params.eventType, params.quantity);

    return this.request<EventResult>('/run-events', {
      method: 'POST',
      body: JSON.stringify({ ...params, idempotencyKey }),
    });
  }

  /**
   * Emits multiple events in a single request.
   *
   * @param events - Array of events to emit
   * @returns Summary of created events
   */
  async emitEventsBatch(
    events: Array<EmitEventParams>,
  ): Promise<{
    success: boolean;
    created: number;
    duplicates: number;
    skipped: number;
    events: Array<{ id: string; eventType: string; isDuplicate: boolean; skipped?: boolean; reason?: string }>;
  }> {
    return this.request('/run-events/batch', {
      method: 'POST',
      body: JSON.stringify({ events }),
    });
  }

  /**
   * Records a complete request/run in a single call.
   *
   * This is the **hero method** for tracking execution. It combines:
   * - Workflow creation (auto-creates if needed)
   * - Run creation
   * - Event emission
   * - Run completion
   *
   * @param params - Run parameters including events
   * @returns The created run with event summary
   *
   * @example
   * ```typescript
   * // RPC provider example
   * const result = await drip.recordRun({
   *   customerId: 'cust_123',
   *   workflow: 'rpc-request',
   *   events: [
   *     { eventType: 'request.start' },
   *     { eventType: 'eth_call', quantity: 1 },
   *     { eventType: 'request.end' },
   *   ],
   *   status: 'COMPLETED',
   * });
   *
   * // API provider example
   * const result = await drip.recordRun({
   *   customerId: 'cust_123',
   *   workflow: 'api-request',
   *   events: [
   *     { eventType: 'request.start' },
   *     { eventType: 'llm.call', quantity: 2000, units: 'tokens' },
   *     { eventType: 'request.end' },
   *   ],
   *   status: 'COMPLETED',
   * });
   *
   * console.log(result.summary);
   * // Output: "✓ Rpc Request: 3 events recorded (152ms)"
   * ```
   */
  async recordRun(params: RecordRunParams): Promise<RecordRunResult> {
    // Try single-call endpoint first; fall back to 4-step orchestration
    // if the server doesn't support it yet (404).
    try {
      return await this.request<RecordRunResult>('/runs/record', {
        method: 'POST',
        body: JSON.stringify({
          customerId: params.customerId,
          workflow: params.workflow,
          events: params.events,
          status: params.status,
          errorMessage: params.errorMessage,
          errorCode: params.errorCode,
          externalRunId: params.externalRunId,
          correlationId: params.correlationId,
          metadata: params.metadata,
        }),
      });
    } catch (err) {
      if (err instanceof DripError && err.statusCode === 404) {
        return this._recordRunFallback(params);
      }
      throw err;
    }
  }

  /**
   * 4-step orchestration fallback for servers without POST /runs/record.
   * @internal
   */
  private async _recordRunFallback(params: RecordRunParams): Promise<RecordRunResult> {
    const startTime = Date.now();

    // Step 1: Resolve workflow
    let workflowId = params.workflow;
    let workflowName = params.workflow;
    const { data: workflows } = await this.listWorkflows();
    const match = workflows.find(
      (w) => w.slug === params.workflow || w.id === params.workflow,
    );
    if (match) {
      workflowId = match.id;
      workflowName = match.name;
    } else {
      const created = await this.request<Workflow>('/workflows', {
        method: 'POST',
        body: JSON.stringify({
          name: params.workflow.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
          slug: params.workflow,
          productSurface: 'CUSTOM',
        }),
      });
      workflowId = created.id;
      workflowName = created.name;
    }

    // Step 2: Start run
    const run = await this.startRun({
      customerId: params.customerId,
      workflowId,
      correlationId: params.correlationId,
      externalRunId: params.externalRunId,
      metadata: params.metadata,
    });

    // Step 3: Emit events
    let eventsCreated = 0;
    let eventsDuplicates = 0;
    if (params.events.length > 0) {
      const batchEvents = params.events.map((evt, i) => ({
        runId: run.id,
        eventType: evt.eventType,
        quantity: evt.quantity ?? 1,
        units: evt.units,
        description: evt.description,
        costUnits: evt.costUnits,
        metadata: evt.metadata,
        idempotencyKey: params.externalRunId
          ? `${params.externalRunId}:${evt.eventType}:${i}`
          : undefined,
      }));
      const batchResult = await this.emitEventsBatch(batchEvents);
      eventsCreated = batchResult.created;
      eventsDuplicates = batchResult.duplicates;
    }

    // Step 4: End run
    const endResult = await this.endRun(run.id, {
      status: params.status,
      errorMessage: params.errorMessage,
      errorCode: params.errorCode,
    });

    const durationMs = Date.now() - startTime;
    const statusIcon = params.status === 'COMPLETED' ? '\u2713' : params.status === 'FAILED' ? '\u2717' : '\u25CB';

    return {
      run: {
        id: run.id,
        workflowId,
        workflowName,
        status: endResult.status as RunStatus,
        durationMs: endResult.durationMs ?? durationMs,
      },
      events: { created: eventsCreated, duplicates: eventsDuplicates },
      totalCostUnits: endResult.totalCostUnits ?? null,
      summary: `${statusIcon} ${workflowName}: ${eventsCreated} events recorded (${endResult.durationMs ?? durationMs}ms)`,
    };
  }

  // ==========================================================================
  // Entitlement Methods
  // ==========================================================================

  /**
   * Checks if a customer is entitled to use a feature.
   *
   * Use this before processing expensive requests to avoid wasting compute
   * on customers who are over their quota.
   *
   * @param params - Entitlement check parameters
   * @returns Whether the customer is allowed, with remaining quota info
   *
   * @example
   * ```typescript
   * const result = await drip.checkEntitlement({
   *   customerId: 'cust_abc123',
   *   featureKey: 'search',
   *   quantity: 1,
   * });
   *
   * if (!result.allowed) {
   *   return res.status(429).json({
   *     error: 'Quota exceeded',
   *     remaining: result.remaining,
   *     resetsAt: result.periodResetsAt,
   *   });
   * }
   *
   * // Process the request...
   * ```
   */
  async checkEntitlement(params: CheckEntitlementParams): Promise<EntitlementCheckResult> {
    this.assertSecretKey('checkEntitlement()');
    return this.request<EntitlementCheckResult>('/entitlements/check', {
      method: 'POST',
      body: JSON.stringify({
        customerId: params.customerId,
        featureKey: params.featureKey,
        quantity: params.quantity ?? 1,
      }),
    });
  }

  // ==========================================================================
  // Customer Plan Changes — per-customer pricing + entitlement swaps
  //
  // These methods eliminate the need to grandfather customers on old plans.
  // Every call captures a full audit snapshot that can be rolled back with
  // `rollbackCustomerPlanChange()`. All require a secret key.
  // ==========================================================================

  /**
   * Change a single customer's pricing — price overrides, discount, spend
   * caps, and included units — without creating a new global pricing plan
   * or affecting any other customer.
   *
   * @param customerId - The Drip customer ID
   * @param params - What to change (at least one field required)
   * @returns The CustomerPlanChange row with proration + audit snapshots
   * @throws {DripError} 404 if customer or any source pricing plan is missing
   * @throws {DripError} 400 if no pricing fields are provided
   *
   * @example
   * ```typescript
   * // Move a customer onto "Pro" pricing by copying from existing plans
   * await drip.applyCustomerPricingChange('cust_123', {
   *   sourcePricingPlanIds: ['plan_pro_api', 'plan_pro_tokens'],
   *   discountPct: '15',
   *   prorate: true,
   *   reason: 'Upgraded after sales call',
   * });
   *
   * // Or set explicit per-unit prices for this customer only
   * await drip.applyCustomerPricingChange('cust_123', {
   *   priceOverrides: [
   *     { unitType: 'api_call', unitPriceUsd: '0.0005' },
   *     { unitType: 'token',    unitPriceUsd: '0.00001' },
   *   ],
   *   replaceAll: true,
   * });
   * ```
   */
  async applyCustomerPricingChange(
    customerId: string,
    params: ApplyPricingChangeParams,
  ): Promise<CustomerPlanChange> {
    this.assertSecretKey('applyCustomerPricingChange()');
    return this.request<CustomerPlanChange>(
      `/customers/${encodeURIComponent(customerId)}/plan-changes/pricing`,
      {
        method: 'POST',
        body: JSON.stringify(params),
      },
    );
  }

  /**
   * Change a single customer's entitlement plan (and/or per-feature
   * overrides) without grandfathering.
   *
   * @param customerId - The Drip customer ID
   * @param params - Target plan + optional per-feature overrides
   * @returns The CustomerPlanChange row
   * @throws {DripError} 404 if customer or plan is missing
   * @throws {DripError} 400 if the target plan is deactivated
   *
   * @example
   * ```typescript
   * await drip.applyCustomerEntitlementChange('cust_123', {
   *   planId: 'plan_pro',
   *   overrides: {
   *     search: { dailyLimit: 10000 },
   *     api_calls: { unlimited: true },
   *   },
   *   reason: 'Upgraded tier',
   * });
   * ```
   */
  async applyCustomerEntitlementChange(
    customerId: string,
    params: ApplyEntitlementChangeParams,
  ): Promise<CustomerPlanChange> {
    this.assertSecretKey('applyCustomerEntitlementChange()');
    return this.request<CustomerPlanChange>(
      `/customers/${encodeURIComponent(customerId)}/plan-changes/entitlement`,
      {
        method: 'POST',
        body: JSON.stringify(params),
      },
    );
  }

  /**
   * List the paginated history of pricing + entitlement changes for a
   * customer, newest first.
   *
   * @param customerId - The Drip customer ID
   * @param options - Optional pagination (limit max 200, default 50)
   */
  async listCustomerPlanChanges(
    customerId: string,
    options: ListCustomerPlanChangesOptions = {},
  ): Promise<ListCustomerPlanChangesResult> {
    this.assertSecretKey('listCustomerPlanChanges()');
    const params = new URLSearchParams();
    if (options.limit !== undefined) params.set('limit', String(options.limit));
    if (options.offset !== undefined) params.set('offset', String(options.offset));
    const query = params.toString();
    const path = `/customers/${encodeURIComponent(customerId)}/plan-changes${query ? `?${query}` : ''}`;
    return this.request<ListCustomerPlanChangesResult>(path);
  }

  /**
   * Retrieve a single plan change by ID.
   *
   * @param customerId - The Drip customer ID
   * @param changeId - The CustomerPlanChange ID
   * @throws {DripError} 404 if not found
   */
  async getCustomerPlanChange(
    customerId: string,
    changeId: string,
  ): Promise<CustomerPlanChange> {
    this.assertSecretKey('getCustomerPlanChange()');
    return this.request<CustomerPlanChange>(
      `/customers/${encodeURIComponent(customerId)}/plan-changes/${encodeURIComponent(changeId)}`,
    );
  }

  /**
   * Roll back a plan change, atomically restoring the previous state and
   * writing an inverse change row to the history for auditability.
   *
   * @param customerId - The Drip customer ID
   * @param changeId - The CustomerPlanChange ID to roll back
   * @param opts - Optional actor attribution
   * @returns The newly created inverse change row
   * @throws {DripError} 404 if the change is missing or belongs to another customer
   * @throws {DripError} 400 if the change has already been rolled back
   *
   * @example
   * ```typescript
   * await drip.rollbackCustomerPlanChange('cust_123', 'chg_abc', {
   *   performedBy: 'admin_alice',
   * });
   * ```
   */
  async rollbackCustomerPlanChange(
    customerId: string,
    changeId: string,
    opts: { performedBy?: string } = {},
  ): Promise<CustomerPlanChange> {
    this.assertSecretKey('rollbackCustomerPlanChange()');
    return this.request<CustomerPlanChange>(
      `/customers/${encodeURIComponent(customerId)}/plan-changes/${encodeURIComponent(changeId)}/rollback`,
      {
        method: 'POST',
        body: JSON.stringify(opts),
      },
    );
  }
}

// Default export for convenience
export default Drip;

// ============================================================================
// Pre-initialized Singleton
// ============================================================================

/**
 * Pre-initialized Drip client singleton.
 *
 * Reads configuration from environment variables:
 * - `DRIP_API_KEY` (required)
 * - `DRIP_BASE_URL` (optional)
 *
 * @example
 * ```typescript
 * import { drip } from '@drip-sdk/node';
 *
 * // One line to track usage
 * await drip.trackUsage({ customerId: 'cust_123', meter: 'api_calls', quantity: 1 });
 * ```
 *
 * @throws {Error} on first use if DRIP_API_KEY is not set
 */
let _singleton: Drip | null = null;

function getSingleton(): Drip {
  if (!_singleton) {
    _singleton = new Drip();
  }
  return _singleton;
}

/**
 * Pre-initialized Drip client singleton.
 *
 * Uses lazy initialization - only creates the client when first accessed.
 * Reads `DRIP_API_KEY` from environment variables.
 *
 * @example
 * ```typescript
 * import { drip } from '@drip-sdk/node';
 *
 * // Track usage with one line
 * await drip.trackUsage({ customerId: 'cust_123', meter: 'api_calls', quantity: 1 });
 *
 * // Record a run
 * await drip.recordRun({
 *   customerId: 'cust_123',
 *   workflow: 'agent-run',
 *   events: [{ eventType: 'llm.call', quantity: 1000, units: 'tokens' }],
 *   status: 'COMPLETED',
 * });
 * ```
 */
export const drip: Drip = new Proxy({} as Drip, {
  get(_target, prop) {
    const instance = getSingleton();
    const value = instance[prop as keyof Drip];
    if (typeof value === 'function') {
      return value.bind(instance);
    }
    return value;
  },
});
