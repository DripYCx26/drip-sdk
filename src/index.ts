/**
 * Drip SDK - Usage-based billing for Node.js
 *
 * The official SDK for integrating with the Drip billing platform.
 * Provides methods for managing customers, recording usage, handling charges,
 * and configuring webhooks.
 *
 * @packageDocumentation
 */

import { createHash, createHmac, timingSafeEqual, webcrypto } from 'crypto';
import { StreamMeter, type StreamMeterOptions } from './stream-meter.js';
import { deterministicIdempotencyKey } from './idempotency.js';
import {
  ResilienceManager,
  type ResilienceConfig,
  type ResilienceHealth,
  type MetricsSummary,
  createDefaultResilienceConfig,
  createDisabledResilienceConfig,
  createHighThroughputResilienceConfig,
} from './resilience.js';

// ============================================================================
// Retry Utility
// ============================================================================

/**
 * Default retry configuration.
 */
const DEFAULT_RETRY_CONFIG = {
  maxAttempts: 3,
  baseDelayMs: 100,
  maxDelayMs: 5000,
} as const;

/**
 * Retry options for API calls.
 */
export interface RetryOptions {
  /**
   * Maximum number of retry attempts.
   * @default 3
   */
  maxAttempts?: number;

  /**
   * Base delay between retries in milliseconds (exponential backoff).
   * @default 100
   */
  baseDelayMs?: number;

  /**
   * Maximum delay between retries in milliseconds.
   * @default 5000
   */
  maxDelayMs?: number;

  /**
   * Custom function to determine if an error is retryable.
   * By default, retries on network errors and 5xx status codes.
   */
  isRetryable?: (error: unknown) => boolean;
}

/**
 * Default function to determine if an error is retryable.
 */
function defaultIsRetryable(error: unknown): boolean {
  // Retry on network errors
  if (error instanceof Error) {
    if (error.message.includes('fetch') || error.message.includes('network')) {
      return true;
    }
  }

  // Retry on 5xx errors and timeouts
  if (error instanceof DripError) {
    return error.statusCode >= 500 || error.statusCode === 408 || error.statusCode === 429;
  }

  return false;
}

/**
 * Executes a function with exponential backoff retry.
 * @internal
 */
async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? DEFAULT_RETRY_CONFIG.maxAttempts;
  const baseDelayMs = options.baseDelayMs ?? DEFAULT_RETRY_CONFIG.baseDelayMs;
  const maxDelayMs = options.maxDelayMs ?? DEFAULT_RETRY_CONFIG.maxDelayMs;
  const isRetryable = options.isRetryable ?? defaultIsRetryable;

  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;

      // Don't retry if it's the last attempt or error isn't retryable
      if (attempt === maxAttempts || !isRetryable(error)) {
        throw error;
      }

      // Exponential backoff with jitter
      const delay = Math.min(
        baseDelayMs * Math.pow(2, attempt - 1) + Math.random() * 100,
        maxDelayMs,
      );

      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  // Should never reach here, but TypeScript needs it
  throw lastError;
}

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
   *   The onboarding, billing, run/event, pricing-plan, subscription, webhook,
   *   and entitlement flows in this SDK should use a secret key.
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

  /**
   * Enable production resilience features (rate limiting, retry with backoff,
   * circuit breaker, metrics).
   *
   * Enabled by default with production settings (100 req/s, 3 retries).
   * Set to `false` to disable for testing or low-level control.
   *
   * - `true`/`undefined`: Use default production settings (100 req/s, 3 retries)
   * - `'high-throughput'`: Optimized for high throughput (1000 req/s, 2 retries)
   * - `ResilienceConfig`: Custom configuration object
   * - `false`: Disabled
   *
   * @example
   * ```typescript
   * // Enable with defaults
   * const drip = new Drip({ apiKey: '...', resilience: true });
   *
   * // High throughput mode
   * const drip = new Drip({ apiKey: '...', resilience: 'high-throughput' });
   *
   * // Custom config
   * const drip = new Drip({
   *   apiKey: '...',
   *   resilience: {
   *     rateLimiter: { requestsPerSecond: 500, burstSize: 1000, enabled: true },
   *     retry: { maxRetries: 5, enabled: true },
   *     circuitBreaker: { failureThreshold: 10, enabled: true },
   *     collectMetrics: true,
   *   },
   * });
   * ```
   */
  resilience?: boolean | 'high-throughput' | Partial<ResilienceConfig>;
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
  isInternal: boolean;

  /** Customer status */
  status: 'ACTIVE' | 'LOW_BALANCE' | 'PAUSED';

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
   * Number of customers to skip (for pagination).
   * @default 0
   */
  offset?: number;

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

/**
 * Customer balance information.
 */
export interface BalanceResult {
  /** Customer ID */
  customerId: string;

  /** On-chain address */
  onchainAddress: string;

  /** Balance in USDC (6 decimals) - matches backend field name */
  balanceUsdc: string;

  /** Pending charges in USDC */
  pendingChargesUsdc: string;

  /** Available USDC (balance minus pending) */
  availableUsdc: string;

  /** ISO timestamp of last balance sync */
  lastSyncedAt: string | null;
}

// ============================================================================
// Customer Spending Cap Types
// ============================================================================

/** Cap types for per-customer spending limits */
export type SpendingCapType = 'DAILY_CHARGE_LIMIT' | 'MONTHLY_CHARGE_LIMIT' | 'SINGLE_CHARGE_LIMIT';

/**
 * Parameters for setting a per-customer spending cap.
 */
export interface SetSpendingCapParams {
  /** Cap type: daily, monthly, or single-charge limit */
  capType: SpendingCapType;

  /** Spending limit in USDC */
  limitValue: number;

  /** Auto-block charges when cap is reached (default: true) */
  autoBlock?: boolean;
}

/**
 * A per-customer spending cap with current usage tracking.
 */
export interface CustomerSpendingCap {
  /** Cap ID */
  id: string;

  /** Cap type */
  capType: string;

  /** Limit value in USDC */
  limitValue: string;

  /** Current period usage in USDC */
  currentUsage: string;

  /** Start of the current cap period */
  periodStart: string;

  /** Whether cap is active */
  isActive: boolean;

  /** Whether charges are auto-blocked at 100% */
  autoBlock: boolean;

  /** Last alert level emitted (50, 80, 95, 100) */
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
// Entitlement Plan Types
// ============================================================================

/**
 * Parameters for creating an entitlement plan.
 */
export interface CreateEntitlementPlanParams {
  /** Plan name */
  name: string;
  /** Unique slug (lowercase alphanumeric, hyphens, underscores) */
  slug: string;
  /** Optional description */
  description?: string;
  /** Whether this is the default plan for new customers */
  isDefault?: boolean;
}

/**
 * An entitlement plan.
 */
export interface EntitlementPlan {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  isDefault: boolean;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

/**
 * Parameters for adding a feature rule to an entitlement plan.
 */
export interface CreateEntitlementRuleParams {
  /** Feature key (e.g., "api_calls", "tokens") */
  featureKey: string;
  /** Type of limit */
  limitType: 'COUNT' | 'AMOUNT';
  /** Time period for the limit */
  period: 'DAILY' | 'MONTHLY';
  /** Numeric limit value */
  limitValue: number;
  /** If true, this feature has unlimited access */
  unlimited?: boolean;
}

/**
 * A feature rule within an entitlement plan.
 */
export interface EntitlementRule {
  id: string;
  planId: string;
  featureKey: string;
  limitType: 'COUNT' | 'AMOUNT';
  period: 'DAILY' | 'MONTHLY';
  limitValue: number;
  unlimited: boolean;
}

/**
 * Parameters for assigning an entitlement plan to a customer.
 */
export interface AssignEntitlementParams {
  /** The entitlement plan ID to assign */
  planId: string;
  /** Optional per-customer overrides */
  overrides?: Record<string, { limitValue?: number; unlimited?: boolean }>;
}

/**
 * A customer's entitlement assignment including usage.
 */
export interface CustomerEntitlement {
  planId: string;
  planName: string;
  planSlug: string;
  rules: EntitlementRule[];
  overrides: Record<string, { limitValue?: number; unlimited?: boolean }>;
  usage: Record<string, { used: number; limit: number; remaining: number; period: string }>;
}

// ============================================================================
// Contract Types
// ============================================================================

/**
 * Parameters for creating a contract.
 */
export interface CreateContractParams {
  /** The Drip customer ID */
  customerId: string;
  /** Contract name */
  name: string;
  /** Start date (ISO datetime) */
  startDate: string;
  /** End date (ISO datetime, optional) */
  endDate?: string;
  /** Minimum spend in USDC */
  minimumUsdc?: string;
  /** Maximum spend in USDC */
  maximumUsdc?: string;
  /** Volume discount percentage */
  discountPct?: number;
  /** Prepaid amount in USDC */
  prepaidAmountUsdc?: string;
  /** Whether unused prepaid balance rolls over */
  prepaidRollover?: boolean;
  /** Included units per usage type */
  includedUnits?: Record<string, number>;
  /** Arbitrary metadata */
  metadata?: Record<string, unknown>;
}

/**
 * A per-customer pricing contract.
 */
export interface Contract {
  id: string;
  customerId: string;
  name: string;
  status: 'ACTIVE' | 'EXPIRED' | 'CANCELLED';
  startDate: string;
  endDate: string | null;
  minimumUsdc: string | null;
  maximumUsdc: string | null;
  discountPct: number | null;
  prepaidAmountUsdc: string | null;
  prepaidBalanceUsdc: string | null;
  prepaidRollover: boolean;
  includedUnits: Record<string, number> | null;
  metadata: Record<string, unknown> | null;
  overrides: ContractPriceOverride[];
  createdAt: string;
  updatedAt: string;
}

/**
 * A price override within a contract.
 */
export interface ContractPriceOverride {
  unitType: string;
  unitPriceUsdc: string;
}

/**
 * Parameters for adding a price override to a contract.
 */
export interface CreateContractOverrideParams {
  /** The usage unit type to override */
  unitType: string;
  /** The override price in USDC (e.g., "0.0005") */
  unitPriceUsd: string;
}

// ============================================================================
// Customer Plan Change Types
// ============================================================================

/** A single per-unit price override (what a customer pays for one unit type). */
export interface PlanChangePriceOverrideInput {
  unitType: string;
  unitPriceUsd: string;
}

/** Per-feature entitlement override. */
export interface PlanChangeEntitlementOverrideInput {
  dailyLimit?: number;
  monthlyLimit?: number;
  unlimited?: boolean;
}

/**
 * Parameters for `applyCustomerPricingChange`. Accepts any combination of
 * source pricing plan IDs, explicit overrides, and commercial terms. At
 * least one field is required.
 */
export interface ApplyPricingChangeParams {
  sourcePricingPlanIds?: string[];
  priceOverrides?: PlanChangePriceOverrideInput[];
  replaceAll?: boolean;
  discountPct?: string;
  minimumUsdc?: string;
  maximumUsdc?: string;
  includedUnits?: Record<string, number>;
  prorate?: boolean;
  /**
   * Exact net proration amount to use instead of computing from the
   * subscription delta. Sign encodes direction: positive = CHARGE,
   * negative = CREDIT, "0" = ZERO. Use for multi-unit changes where
   * summing override prices would produce a meaningless baseline.
   */
  prorationAmountOverride?: string;
  effectiveDate?: string;
  reason?: string;
  performedBy?: string;
}

/** Parameters for `applyCustomerEntitlementChange`. */
export interface ApplyEntitlementChangeParams {
  planId: string;
  overrides?: Record<string, PlanChangeEntitlementOverrideInput>;
  reason?: string;
  performedBy?: string;
}

export type PlanChangeType = 'PRICING' | 'ENTITLEMENT' | 'BOTH';
export type PlanChangeStatus = 'APPLIED' | 'ROLLED_BACK' | 'SUPERSEDED';
export type PlanChangeProrationDirection = 'NONE' | 'CREDIT' | 'CHARGE' | 'ZERO';

/**
 * A recorded pricing or entitlement change on a customer. Returned by
 * apply / get / rollback / list operations.
 */
export interface CustomerPlanChange {
  id: string;
  businessId: string;
  customerId: string;
  changeType: PlanChangeType;
  status: PlanChangeStatus;
  effectiveFrom: string;
  reason: string | null;
  previousState: unknown;
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

/** Response shape for `listCustomerPlanChanges`. */
export interface ListCustomerPlanChangesResult {
  data: CustomerPlanChange[];
  total: number;
}

/** Pagination options for `listCustomerPlanChanges`. */
export interface ListCustomerPlanChangesOptions {
  limit?: number;
  offset?: number;
}

// ============================================================================
// Usage & Charge Types
// ============================================================================

/**
 * Parameters for recording usage and charging a customer.
 */
export interface ChargeParams {
  /**
   * The Drip customer ID to charge.
   *
   * Either `customerId` or `externalCustomerId` is required. If you pass
   * `externalCustomerId` instead, Drip will look up (or auto-provision) the
   * matching customer on first use.
   */
  customerId?: string;

  /**
   * Your own database's customer ID. Use this if you'd rather not round-trip
   * through `POST /customers` first — on first use, Drip auto-provisions an
   * internal customer for the pair `(business, externalCustomerId)` and
   * billing proceeds against it.
   *
   * Either `customerId` or `externalCustomerId` is required.
   * @example "user_42"
   */
  externalCustomerId?: string;

  /**
   * The usage meter/type to record against.
   * Must match a meter configured in your pricing plan.
   * @example "api_calls", "compute_seconds", "storage_gb"
   */
  meter: string;

  /**
   * The quantity of usage to record.
   * Will be multiplied by the meter's unit price.
   */
  quantity: number;

  /**
   * Unique key to prevent duplicate charges and map each call to a single event.
   * Auto-generated if not provided. Retrying with the same key returns the original charge.
   * @example "req_abc123"
   */
  idempotencyKey?: string;

  /**
   * Additional metadata to attach to this usage event.
   */
  metadata?: Record<string, unknown>;
}

/**
 * Result of a successful charge operation.
 */
export interface ChargeResult {
  /** Whether the charge was successful */
  success: boolean;

  /** The usage event ID */
  usageEventId: string;

  /** True if this was a deduplicated replay (returned cached result from previous request) */
  isDuplicate: boolean;

  /** Details about the charge */
  charge: {
    /** Unique charge ID */
    id: string;

    /** Amount charged in USDC (6 decimals) */
    amountUsdc: string;

    /** Amount in native token */
    amountToken: string;

    /** Blockchain transaction hash (null until settled) */
    txHash: string | null;

    /** Current status of the charge */
    status: ChargeStatus;
  };
}

/**
 * Possible charge statuses.
 */
export type ChargeStatus =
  | 'PENDING'
  | 'PENDING_SETTLEMENT'
  | 'CONFIRMED'
  | 'FAILED'
  | 'REFUNDED';

/**
 * trackUsage dispatch mode.
 *
 * - `sync` (default) → `POST /usage` (billing-aware: creates a charge if a
 *   pricing plan matches the unit type, otherwise records without charging).
 * - `batch` → `POST /usage/async` (queued, returns 202 immediately).
 * - `internal` → `POST /usage/internal` (visibility-only; never bills).
 */
export type TrackUsageMode = 'batch' | 'sync' | 'internal';

/**
 * Parameters for tracking usage without billing.
 * Use this for internal visibility, pilots, or pre-billing tracking.
 *
 * **Minimum viable payload:** only `customerId` is required. `meter` defaults
 * to `"generic"` and `quantity` defaults to `1` on the server side, so you
 * can start emitting events with zero ceremony:
 *
 * ```ts
 * await drip.trackUsage({ customerId: customer.id });
 * ```
 */
export interface TrackUsageParams {
  /**
   * The Drip customer ID to track usage for.
   *
   * Either `customerId` or `externalCustomerId` is required.
   * @example "cust_abc123"
   */
  customerId?: string;

  /**
   * Your own database's customer ID. If no Drip customer exists yet for this
   * external ID, one is auto-provisioned as an internal customer on first
   * use — no need to call `createCustomer()` first.
   *
   * Either `customerId` or `externalCustomerId` is required.
   * @example "user_42"
   */
  externalCustomerId?: string;

  /**
   * The meter/usage type (e.g., 'api_calls', 'tokens').
   * Optional — defaults to `"generic"` when omitted.
   */
  meter?: string;

  /**
   * The quantity of usage to record.
   * Optional — defaults to `1` when omitted.
   */
  quantity?: number;

  /**
   * Unique key to prevent duplicate records.
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
   * - `sync` (default): `POST /usage` — billing-aware. If a pricing plan
   *   matches the unit type, a charge is created and batch-settled on-chain
   *   when the threshold is met.
   * - `batch`: `POST /usage/async` — queued, returns 202 immediately. Use
   *   when you need fast response times and can handle eventual consistency
   *   via webhooks.
   * - `internal`: `POST /usage/internal` — visibility-only, never bills.
   *   Use for internal dashboards / quotas / rate limiting that should not
   *   create charges.
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

/**
 * A detailed charge record.
 */
export interface Charge {
  /** Unique charge ID */
  id: string;

  /** Associated usage event ID */
  usageId: string;

  /** Customer ID */
  customerId: string;

  /** Customer details */
  customer: {
    id: string;
    onchainAddress: string;
    externalCustomerId: string | null;
  };

  /** Usage event details */
  usageEvent: {
    id: string;
    type: string;
    quantity: string;
    metadata: Record<string, unknown> | null;
  };

  /** Amount in USDC */
  amountUsdc: string;

  /** Amount in native token */
  amountToken: string;

  /** Transaction hash (if submitted) */
  txHash: string | null;

  /** Block number (if confirmed) */
  blockNumber: string | null;

  /** Current status */
  status: ChargeStatus;

  /** Failure reason (if failed) */
  failureReason: string | null;

  /** ISO timestamp of creation */
  createdAt: string;

  /** ISO timestamp of confirmation */
  confirmedAt: string | null;
}

/**
 * Options for listing charges.
 */
export interface ListChargesOptions {
  /**
   * Filter by customer ID.
   */
  customerId?: string;

  /**
   * Filter by charge status.
   */
  status?: ChargeStatus;

  /**
   * Maximum number of charges to return (1-100).
   * @default 100
   */
  limit?: number;

  /**
   * Number of charges to skip (for pagination).
   * @default 0
   */
  offset?: number;
}

/**
 * Response from listing charges.
 */
export interface ListChargesResponse {
  /** Array of charges */
  data: Charge[];

  /** Total count returned */
  count: number;
}

// ============================================================================
// Webhook Types
// ============================================================================

/**
 * Available webhook event types.
 */
export type WebhookEventType =
  | 'customer.balance.low'
  | 'usage.recorded'
  | 'charge.succeeded'
  | 'charge.failed'
  | 'customer.deposit.confirmed'
  | 'customer.withdraw.confirmed'
  | 'customer.usage_cap.reached'
  | 'customer.spending.warning'
  | 'customer.spending.blocked'
  | 'customer.spending.exceeded'
  | 'webhook.endpoint.unhealthy'
  | 'customer.created'
  | 'api_key.created'
  | 'pricing_plan.updated'
  | 'transaction.created'
  | 'transaction.pending'
  | 'transaction.confirmed'
  | 'transaction.failed'
  | 'subscription.created'
  | 'subscription.renewed'
  | 'subscription.cancelled'
  | 'subscription.paused'
  | 'subscription.resumed'
  | 'subscription.trial_ended'
  | 'subscription.payment_failed'
  | 'withdrawal.created'
  | 'withdrawal.completed'
  | 'withdrawal.failed';

/**
 * Per-endpoint routing filters for webhooks.
 * All specified criteria use AND logic — a webhook must match ALL filters.
 * Within each filter type, values use OR logic (match any).
 */
export interface WebhookFilters {
  /** Only receive events for these usage types / endpoint names */
  usageTypes?: string[];

  /** Only receive events for these customer IDs */
  customerIds?: string[];

  /** Only receive events with these severity levels */
  severities?: ('low' | 'medium' | 'high' | 'critical')[];
}

/**
 * Parameters for creating a webhook.
 */
export interface CreateWebhookParams {
  /**
   * The URL to send webhook events to.
   * Must be HTTPS in production.
   * @example "https://api.yourapp.com/webhooks/drip"
   */
  url: string;

  /**
   * Array of event types to subscribe to.
   * @example ["charge.succeeded", "charge.failed"]
   */
  events: WebhookEventType[];

  /**
   * Optional description for the webhook.
   */
  description?: string;

  /**
   * Optional per-endpoint routing filters.
   * When set, only events matching ALL filter criteria are delivered.
   * @example { usageTypes: ["tokens"], customerIds: ["cust_abc123"] }
   */
  filters?: WebhookFilters;
}

/**
 * Parameters for updating a webhook.
 *
 * @example
 * ```typescript
 * // Add filters to route only specific events
 * await drip.updateWebhook('wh_abc123', {
 *   filters: { customerIds: ['cust_xyz'], severities: ['high', 'critical'] },
 * });
 *
 * // Remove all filters (receive all events again)
 * await drip.updateWebhook('wh_abc123', { filters: null });
 * ```
 */
export interface UpdateWebhookParams {
  /** New webhook URL */
  url?: string;

  /** New event subscriptions */
  events?: WebhookEventType[];

  /** New description */
  description?: string;

  /** Enable/disable the webhook */
  isActive?: boolean;

  /** Per-endpoint filters. Set to `null` to remove all filters. */
  filters?: WebhookFilters | null;
}

/**
 * A webhook configuration.
 */
export interface Webhook {
  /** Unique webhook ID */
  id: string;

  /** Webhook endpoint URL */
  url: string;

  /** Subscribed event types */
  events: string[];

  /** Description */
  description: string | null;

  /** Per-endpoint routing filters, or null if no filters are set */
  filters: WebhookFilters | null;

  /** Whether the webhook is active */
  isActive: boolean;

  /** Health status of the webhook endpoint */
  healthStatus: 'HEALTHY' | 'DEGRADED' | 'UNHEALTHY';

  /** Number of consecutive delivery failures */
  consecutiveFailures: number;

  /** ISO timestamp of last health status change */
  lastHealthChange: string | null;

  /** ISO timestamp of creation */
  createdAt: string;

  /** ISO timestamp of last update */
  updatedAt: string;

  /** Delivery statistics */
  stats?: {
    total: number;
    delivered: number;
    failed: number;
    pending: number;
  };
}

/**
 * Response from creating a webhook.
 */
export interface CreateWebhookResponse extends Webhook {
  /**
   * The webhook signing secret.
   * Only returned once at creation time - save it securely!
   */
  secret: string;

  /** Reminder to save the secret */
  message: string;
}

/**
 * Response from listing webhooks.
 */
export interface ListWebhooksResponse {
  /** Array of webhooks */
  data: Webhook[];

  /** Total count */
  count: number;
}

/**
 * Response from deleting a webhook.
 */
export interface DeleteWebhookResponse {
  /** Whether the deletion was successful */
  success: boolean;
}

// ============================================================================
// Checkout Types
// ============================================================================

/**
 * Parameters for creating a checkout session.
 * This is the primary way to get money into a customer's account.
 */
export interface CheckoutParams {
  /**
   * Existing customer ID (optional).
   * If not provided, a new customer is created after payment.
   */
  customerId?: string;

  /**
   * Your internal customer/user ID for new customers.
   * Used to link the Drip customer to your system.
   */
  externalCustomerId?: string;

  /**
   * Amount in cents (e.g., 5000 = $50.00).
   * Minimum: 500 ($5.00)
   * Maximum: 1000000 ($10,000.00)
   */
  amount: number;

  /**
   * URL to redirect after successful payment.
   * Query params will be added: session_id, customer_id, status
   */
  returnUrl: string;

  /**
   * URL to redirect if user cancels (optional).
   */
  cancelUrl?: string;

  /**
   * Custom metadata to attach to this checkout.
   */
  metadata?: Record<string, unknown>;
}

/**
 * Result of creating a checkout session.
 */
export interface CheckoutResult {
  /** Checkout session ID */
  id: string;

  /** URL to redirect user to for payment */
  url: string;

  /** ISO timestamp when session expires (30 minutes) */
  expiresAt: string;

  /** Amount in USD */
  amountUsd: number;
}

// ============================================================================
// Withdrawal Types (Fiat Off-Ramp)
// ============================================================================

/** Status of a withdrawal. */
export type WithdrawalStatus =
  | 'PENDING'
  | 'ONCHAIN_PENDING'
  | 'ONCHAIN_CONFIRMED'
  | 'OFFRAMP_PENDING'
  | 'OFFRAMP_PROCESSING'
  | 'COMPLETE'
  | 'FAILED'
  | 'CANCELLED';

/**
 * Parameters for creating a withdrawal (USDC → fiat).
 */
export interface WithdrawParams {
  /**
   * Amount in USDC to withdraw (e.g., "100.00").
   * Minimum: $10.00
   * Maximum: $100,000.00
   */
  amountUsdc: string;

  /**
   * Idempotency key to safely retry requests.
   */
  idempotencyKey: string;

  /**
   * Optional bank description for display (e.g., "Chase ****1234").
   */
  bankDescription?: string;
}

/**
 * Result of creating a withdrawal.
 */
export interface WithdrawalResult {
  /** Withdrawal ID */
  id: string;

  /** Current status */
  status: WithdrawalStatus;

  /** Gross withdrawal amount in USDC */
  amountUsdc: string;

  /** Off-ramp fee in USDC (~1% for ACH) */
  feeUsdc: string;

  /** Net amount after fee (what merchant receives in fiat) */
  netAmountUsdc: string;

  /** Fiat currency (default: USD) */
  fiatCurrency: string;

  /** Bank description (e.g., "Chase ****1234") */
  bankDescription: string | null;

  /** ISO timestamp when withdrawal was created */
  createdAt: string;

  /** ISO timestamp when withdrawal completed (null if pending) */
  completedAt: string | null;
}

/**
 * Result of a fee estimate.
 */
export interface WithdrawalFeeEstimate {
  amountUsdc: string;
  feeUsdc: string;
  netAmountUsdc: string;
  feeBps: number;
  feePercent: string;
  method: string;
  estimatedArrival: string;
}

// ============================================================================
// Subscription Types
// ============================================================================

/** Status of a subscription. */
export type SubscriptionStatus =
  | 'ACTIVE'
  | 'PAUSED'
  | 'CANCELLED'
  | 'EXPIRED'
  | 'PAST_DUE'
  | 'TRIALING';

/** Billing interval for a subscription. */
export type SubscriptionInterval = 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'ANNUAL';

/**
 * Parameters for creating a new subscription.
 */
export interface CreateSubscriptionParams {
  /** Customer ID to subscribe */
  customerId: string;

  /** Human-readable subscription name */
  name: string;

  /** Optional description */
  description?: string;

  /** Billing interval */
  interval: SubscriptionInterval;

  /** Price per interval in USDC */
  priceUsdc: number;

  /** Custom metadata */
  metadata?: Record<string, unknown>;

  /** Trial period in days (omit for no trial) */
  trialDays?: number;

  /** Included usage units per period (for hybrid subscriptions) */
  includedUsage?: number;

  /** Usage type for overage metering (links to PricingPlan) */
  overageUnitType?: string;
}

/**
 * Parameters for updating an existing subscription.
 */
export interface UpdateSubscriptionParams {
  /** Updated subscription name */
  name?: string;

  /** Updated description */
  description?: string;

  /** Updated price (takes effect at next billing period) */
  priceUsdc?: number;

  /** Updated metadata */
  metadata?: Record<string, unknown>;

  /** Updated included usage */
  includedUsage?: number;

  /** Updated overage unit type */
  overageUnitType?: string;
}

/**
 * Parameters for cancelling a subscription.
 */
export interface CancelSubscriptionParams {
  /** Cancel immediately instead of at period end. @default false */
  immediate?: boolean;
}

/**
 * Parameters for pausing a subscription.
 */
export interface PauseSubscriptionParams {
  /** ISO date-time string for auto-resume (optional) */
  resumeDate?: string;
}

/**
 * A Drip subscription record.
 */
export interface Subscription {
  /** Unique subscription ID */
  id: string;

  /** Business ID */
  businessId: string;

  /** Customer ID */
  customerId: string;

  /** Subscription name */
  name: string;

  /** Optional description */
  description: string | null;

  /** Billing interval */
  interval: SubscriptionInterval;

  /** Price per interval in USDC */
  priceUsdc: string;

  /** Current status */
  status: SubscriptionStatus;

  /** Start of current billing period (ISO timestamp) */
  currentPeriodStart: string;

  /** End of current billing period (ISO timestamp) */
  currentPeriodEnd: string;

  /** When cancellation was requested (ISO timestamp, null if not cancelled) */
  cancelledAt: string | null;

  /** Whether subscription cancels at end of current period */
  cancelAtPeriodEnd: boolean;

  /** When subscription was paused (ISO timestamp) */
  pausedAt: string | null;

  /** Scheduled resume date (ISO timestamp) */
  resumesAt: string | null;

  /** Trial period start (ISO timestamp) */
  trialStart: string | null;

  /** Trial period end (ISO timestamp) */
  trialEnd: string | null;

  /** Included usage units per period */
  includedUsage: number | null;

  /** Usage type for overage metering */
  overageUnitType: string | null;

  /** Custom metadata */
  metadata: Record<string, unknown> | null;

  /** ISO timestamp of creation */
  createdAt: string;

  /** ISO timestamp of last update */
  updatedAt: string;
}

/**
 * Options for listing subscriptions.
 */
export interface ListSubscriptionsOptions {
  /** Filter by customer ID */
  customerId?: string;

  /** Filter by status */
  status?: SubscriptionStatus;

  /** Maximum results (1-100, default 100) */
  limit?: number;
}

/**
 * Response from listing subscriptions.
 */
export interface ListSubscriptionsResponse {
  /** Array of subscriptions */
  data: Subscription[];

  /** Total count */
  count: number;
}

// ============================================================================
// Run & Event Types (Execution Ledger)
// ============================================================================

/**
 * Parameters for creating a new workflow.
 */
export interface CreateWorkflowParams {
  /** Human-readable workflow name */
  name: string;

  /** URL-safe identifier (lowercase alphanumeric with underscores/hyphens) */
  slug: string;

  /** Type of workflow */
  productSurface?: 'API' | 'RPC' | 'WEBHOOK' | 'AGENT' | 'PIPELINE' | 'CUSTOM';

  /** Optional description */
  description?: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * A workflow definition.
 */
export interface Workflow {
  id: string;
  name: string;
  slug: string;
  productSurface: string;
  /** Blockchain chain (if applicable) */
  chain: string | null;
  description: string | null;
  isActive: boolean;
  createdAt: string;
}

/**
 * Parameters for starting a new agent run.
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
 * Parameters for emitting an event to a run.
 */
export interface EmitEventParams {
  /** Run ID to attach this event to */
  runId: string;

  /** Event type (e.g., "agent.step", "rpc.request") */
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

  /** Idempotency key (auto-generated if not provided) */
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

// ============================================================================
// Meter Types
// ============================================================================

/**
 * A meter (usage type) from a pricing plan.
 */
export interface Meter {
  /** Pricing plan ID */
  id: string;

  /** Human-readable name */
  name: string;

  /** The meter/usage type identifier (use this in charge() calls) */
  meter: string;

  /** Price per unit in USD */
  unitPriceUsd: string;

  /** Whether this meter is active */
  isActive: boolean;
}

/**
 * Response from listing meters.
 */
export interface ListMetersResponse {
  /** Array of available meters */
  data: Meter[];

  /** Total count */
  count: number;
}

// ============================================================================
// Pricing Plan Types
// ============================================================================

/**
 * Supported pricing models.
 *
 * - `FLAT` — single `unitPriceUsd` applied to every unit.
 * - `TIERED` — graduated pricing; each tier applies only to units within its range.
 * - `VOLUME` — entire quantity priced at the single tier that contains it.
 * - `PACKAGE` — bundled billing; quantity rounded up to nearest `packageSize`.
 * - `PER_SEAT` — per-user/seat pricing; `unitPriceUsd` is price per seat per period.
 */
export type PricingModel = 'FLAT' | 'TIERED' | 'VOLUME' | 'PACKAGE' | 'PER_SEAT';

/**
 * A pricing tier within a TIERED, VOLUME, or PACKAGE plan.
 */
export interface PricingTier {
  /** Tier ID */
  id: string;

  /** Minimum quantity for this tier (inclusive) */
  minQuantity: string;

  /** Maximum quantity for this tier (exclusive), or null for unbounded */
  maxQuantity: string | null;

  /** Price per unit in USD for this tier */
  unitPriceUsd: string;

  /** Optional flat fee in USD applied once when this tier is entered */
  flatFeeUsd: string | null;

  /** For PACKAGE model: number of units per package */
  packageSize: number | null;
}

/**
 * A tier input when creating or updating a pricing plan.
 */
export interface PricingTierInput {
  /** Minimum quantity for this tier (inclusive, starts at 0) */
  minQuantity: number;

  /** Maximum quantity for this tier (exclusive), or null for unbounded (last tier) */
  maxQuantity?: number | null;

  /** Price per unit in USD for this tier */
  unitPriceUsd: number;

  /** Optional flat fee in USD */
  flatFeeUsd?: number | null;

  /** For PACKAGE model: number of units per package */
  packageSize?: number | null;
}

/**
 * A full pricing plan record.
 */
export interface PricingPlan {
  /** Pricing plan ID */
  id: string;

  /** Human-readable name */
  name: string;

  /** The usage type this plan prices (e.g., "api_call", "token") */
  unitType: string;

  /** Price per unit in USD (string for decimal precision) */
  unitPriceUsd: string;

  /** Currency (always "USDC") */
  currency: string;

  /** Whether this plan is active */
  isActive: boolean;

  /** Pricing model */
  pricingModel: PricingModel;

  /** Pricing tiers (empty for FLAT and PER_SEAT models) */
  tiers: PricingTier[];

  /** ISO 8601 creation timestamp */
  createdAt: string;

  /** ISO 8601 last-updated timestamp */
  updatedAt: string;
}

/**
 * Parameters for creating a new pricing plan.
 */
export interface CreatePricingPlanParams {
  /** Human-readable name */
  name: string;

  /** Usage type this plan prices (e.g., "api_call", "token", "compute_second") */
  unitType: string;

  /** Price per unit in USD (max 6 decimal places, e.g., 0.001) */
  unitPriceUsd: number;

  /** Whether this plan is active (default: true) */
  isActive?: boolean;

  /** Pricing model (default: "FLAT") */
  pricingModel?: PricingModel;

  /** Pricing tiers (required for TIERED, VOLUME, PACKAGE; must be empty for FLAT, PER_SEAT) */
  tiers?: PricingTierInput[];
}

/**
 * Parameters for updating an existing pricing plan.
 * Price-affecting changes (unitPriceUsd, pricingModel, tiers) create a new version.
 */
export interface UpdatePricingPlanParams {
  /** Updated plan name */
  name?: string;

  /** Updated price per unit in USD */
  unitPriceUsd?: number;

  /** Enable or disable the plan */
  isActive?: boolean;

  /** Change the pricing model (requires tiers if switching to TIERED/VOLUME/PACKAGE) */
  pricingModel?: PricingModel;

  /** Replace plan tiers (required when switching to tier-based models) */
  tiers?: PricingTierInput[];
}

/**
 * Response from listing pricing plans.
 */
export interface ListPricingPlansResponse {
  /** Array of pricing plans */
  data: PricingPlan[];

  /** Total count */
  count: number;
}

// ============================================================================
// Cost Estimation Types
// ============================================================================

/**
 * Custom pricing map for cost estimation.
 * Maps usage type to unit price (e.g., { "api_call": "0.005", "token": "0.0001" })
 */
export type CustomPricing = Record<string, string>;

/**
 * Parameters for estimating costs from historical usage events.
 */
export interface EstimateFromUsageParams {
  /** Filter to a specific customer (optional) */
  customerId?: string;

  /** Start of the period to estimate */
  periodStart: Date | string;

  /** End of the period to estimate */
  periodEnd: Date | string;

  /** Default price for usage types without pricing plans */
  defaultUnitPrice?: string;

  /** Include events that already have charges (default: true) */
  includeChargedEvents?: boolean;

  /** Filter to specific usage types */
  usageTypes?: string[];

  /** Custom pricing overrides (takes precedence over DB pricing) */
  customPricing?: CustomPricing;
}

/**
 * A usage item for hypothetical cost estimation.
 */
export interface HypotheticalUsageItem {
  /** The usage type (e.g., "api_call", "token") */
  usageType: string;

  /** The quantity of usage */
  quantity: number;

  /** Override unit price for this specific item */
  unitPriceOverride?: string;
}

/**
 * Parameters for estimating costs from hypothetical usage.
 */
export interface EstimateFromHypotheticalParams {
  /** List of usage items to estimate */
  items: HypotheticalUsageItem[];

  /** Default price for usage types without pricing plans */
  defaultUnitPrice?: string;

  /** Custom pricing overrides (takes precedence over DB pricing) */
  customPricing?: CustomPricing;
}

/**
 * A line item in the cost estimate.
 */
export interface CostEstimateLineItem {
  /** The usage type */
  usageType: string;

  /** Total quantity */
  quantity: string;

  /** Unit price used */
  unitPrice: string;

  /** Estimated cost in USDC */
  estimatedCostUsdc: string;

  /** Number of events (for usage-based estimates) */
  eventCount?: number;

  /** Whether a pricing plan was found for this usage type */
  hasPricingPlan: boolean;
}

/**
 * Response from cost estimation.
 */
export interface CostEstimateResponse {
  /** Business ID (optional - may not be returned by all endpoints) */
  businessId?: string;

  /** Customer ID (if filtered) */
  customerId?: string;

  /** Period start (for usage-based estimates) */
  periodStart?: string;

  /** Period end (for usage-based estimates) */
  periodEnd?: string;

  /** Breakdown by usage type */
  lineItems: CostEstimateLineItem[];

  /** Subtotal in USDC */
  subtotalUsdc: string;

  /** Total estimated cost in USDC */
  estimatedTotalUsdc: string;

  /** Currency (always USDC) */
  currency: 'USDC';

  /** Indicates this is an estimate, not a charge */
  isEstimate: true;

  /** When the estimate was generated */
  generatedAt: string;

  /** Notes about the estimate (e.g., missing pricing plans, custom pricing applied) */
  notes: string[];
}

// ============================================================================
// Record Run Types (Simplified API)
// ============================================================================

/**
 * A single event to record in a run.
 */
export interface RecordRunEvent {
  /** Event type (e.g., "agent.step", "tool.call") */
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
 * This is the simplified API that combines workflow, run, and events.
 */
export interface RecordRunParams {
  /** Customer ID this run belongs to */
  customerId: string;

  /**
   * Workflow identifier. Can be:
   * - An existing workflow ID (e.g., "wf_abc123")
   * - A slug that will be auto-created if it doesn't exist (e.g., "my_agent")
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
  workflowName: string | null;
  customerId: string;
  status: RunStatus;
  correlationId: string | null;
  metadata: Record<string, unknown> | null;
  errorMessage: string | null;
  errorCode: string | null;
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
// Wrap API Call Types
// ============================================================================

/**
 * Parameters for wrapping an external API call with usage tracking.
 * This ensures usage is recorded even if there's a crash/failure after the API call.
 */
export interface WrapApiCallParams<T> {
  /**
   * The Drip customer ID to charge.
   *
   * Either `customerId` or `externalCustomerId` is required.
   */
  customerId?: string;

  /**
   * Your own database's customer ID. First use auto-provisions an internal
   * Drip customer — no need to call `createCustomer()` first.
   *
   * Either `customerId` or `externalCustomerId` is required.
   */
  externalCustomerId?: string;

  /**
   * The usage meter/type to record against.
   * Must match a meter configured in your pricing plan.
   */
  meter: string;

  /**
   * The async function that makes the external API call.
   * This is the call you want to track (e.g., OpenAI, Anthropic, etc.)
   */
  call: () => Promise<T>;

  /**
   * Function to extract the usage quantity from the API call result.
   * @example (result) => result.usage.total_tokens
   */
  extractUsage: (result: T) => number;

  /**
   * Custom idempotency key prefix.
   * If not provided, a unique key is generated.
   * The key ensures retries don't double-charge.
   */
  idempotencyKey?: string;

  /**
   * Additional metadata to attach to this usage event.
   */
  metadata?: Record<string, unknown>;

  /**
   * Retry configuration for the Drip charge call.
   * The external API call is NOT retried (only called once).
   */
  retryOptions?: RetryOptions;
}

/**
 * Result of a wrapped API call.
 */
export interface WrapApiCallResult<T> {
  /**
   * The result from the external API call.
   */
  result: T;

  /**
   * The charge result from Drip.
   */
  charge: ChargeResult;

  /**
   * The idempotency key used (useful for debugging).
   */
  idempotencyKey: string;
}

// ============================================================================
// Error Types
// ============================================================================

import { DripError } from './errors.js';
export { DripError };

// ============================================================================
// Portal Session Types
// ============================================================================

/**
 * Parameters for creating a portal session.
 */
export interface CreatePortalSessionParams {
  /**
   * The customer ID (internal or external) to create a portal session for.
   * The customer must belong to your business.
   */
  customerId: string;

  /**
   * How long the portal link is valid, in minutes.
   * Clamped to 5–1440 (24 hours). Default: 60.
   */
  expiresInMinutes?: number;
}

/**
 * A portal session returned by `createPortalSession()`.
 */
export interface PortalSession {
  /** Unique session ID (use this to revoke the session). */
  id: string;

  /** Opaque token embedded in the portal URL. */
  token: string;

  /** The resolved internal customer ID. */
  customerId: string;

  /** ISO-8601 expiry timestamp. */
  expiresAt: string;

  /** Relative URL path for the portal (e.g. "/portal/abc123..."). */
  url: string;
}

// ============================================================================
// Async Usage Types
// ============================================================================

/**
 * Result of an async charge operation.
 * The charge is queued for background processing — subscribe to
 * `charge.succeeded` / `charge.failed` webhooks for final status.
 */
export interface ChargeAsyncResult {
  /** Whether the request was accepted */
  success: boolean;

  /** The usage event ID */
  usageEventId: string;

  /** True if this was a deduplicated replay */
  isDuplicate: boolean;

  /** Details about the queued charge */
  charge: {
    /** Unique charge ID */
    id: string;

    /** Amount in USDC (6 decimals) */
    amountUsdc: string;

    /** Current status (typically PENDING) */
    status: string;

    /** Estimated time until confirmation (e.g. "30s") */
    estimatedConfirmationTime?: string;
  };

  /** Human-readable message */
  message: string;
}

// ============================================================================
// Events Types
// ============================================================================

/**
 * Options for listing execution events.
 */
export interface ListEventsOptions {
  /** Filter by customer ID */
  customerId?: string;

  /** Filter by run ID */
  runId?: string;

  /** Filter by event type */
  eventType?: string;

  /** Filter by outcome (SUCCESS, FAILURE, etc.) */
  outcome?: string;

  /** Maximum results (1-100, default 100) */
  limit?: number;

  /** Number of events to skip for pagination */
  offset?: number;
}

/**
 * An execution event record.
 */
export interface ExecutionEvent {
  /** Unique event ID */
  id: string;

  /** Customer ID */
  customerId: string;

  /** Run ID (if part of a run) */
  runId?: string;

  /** Event type / action name */
  eventType: string;

  /** Outcome of the event */
  outcome: string;

  /** Human-readable explanation */
  explanation?: string;

  /** ISO-8601 timestamp */
  createdAt: string;

  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Paginated list of events.
 */
export interface ListEventsResponse {
  /** Array of events */
  data: ExecutionEvent[];

  /** Total count matching filters */
  total: number;

  /** Applied limit */
  limit: number;

  /** Applied offset */
  offset: number;
}

/**
 * Causality trace for an event — ancestors, children, and retry chain.
 */
export interface EventTrace {
  /** The event ID that was traced */
  eventId: string;

  /** Parent chain from root to this event's parent */
  ancestors: ExecutionEvent[];

  /** Direct child events caused by this event */
  children: ExecutionEvent[];

  /** If retried, all retry attempts */
  retryChain: ExecutionEvent[];
}

// ============================================================================
// Payload Mapping Engine
// ============================================================================

/**
 * A declarative transform from an arbitrary customer JSON shape into Drip's
 * canonical usage event. Created once via `createPayloadMapping`, then
 * referenced by slug in `POST /v1/ingest/:sourceName`.
 */
export interface PayloadMapping {
  id: string;
  businessId: string;
  name: string;
  /** URL-safe slug, unique per business. Used as `:mappingSlug` in ingest. */
  sourceName: string;
  sampleInput: Record<string, unknown> | null;
  transformRules: Record<string, unknown>;
  targetUnitType: string;
  /** Restricted JSONPath expression resolving to a numeric quantity. */
  targetQuantityPath: string;
  /** Restricted JSONPath expression resolving to a Drip customer ID. */
  targetCustomerIdPath: string;
  /** Optional path for the idempotency key; Drip auto-generates a fallback otherwise. */
  targetIdempotencyPath: string | null;
  /** Map of metadata key → JSONPath expression. */
  targetMetadataMap: Record<string, string> | null;
  /** Optional literal `actionName` applied to every transformed event. */
  targetActionName: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

/**
 * An immutable historical snapshot of a {@link PayloadMapping}. A new version
 * is appended on every update for audit and rollback.
 */
export interface PayloadMappingVersion {
  id: string;
  mappingId: string;
  businessId: string;
  version: number;
  name: string;
  sourceName: string;
  sampleInput: Record<string, unknown> | null;
  transformRules: Record<string, unknown>;
  targetUnitType: string;
  targetQuantityPath: string;
  targetCustomerIdPath: string;
  targetIdempotencyPath: string | null;
  targetMetadataMap: Record<string, string> | null;
  targetActionName: string | null;
  createdBy: string | null;
  createdAt: string;
}

export interface CreatePayloadMappingParams {
  name: string;
  sourceName: string;
  sampleInput?: Record<string, unknown>;
  transformRules?: Record<string, unknown>;
  targetUnitType: string;
  targetQuantityPath: string;
  targetCustomerIdPath: string;
  targetIdempotencyPath?: string;
  targetMetadataMap?: Record<string, string>;
  targetActionName?: string;
  isActive?: boolean;
}

export type UpdatePayloadMappingParams = Partial<CreatePayloadMappingParams>;

export interface PayloadMappingDryRunResult {
  /** The `CreateEventInput` Drip would record against this mapping. */
  event: Record<string, unknown>;
}

export interface PayloadMappingIngestResult {
  event: Record<string, unknown>;
  /** True when the idempotency key matched an existing event. */
  replayed: boolean;
}

// ============================================================================
// Main SDK Class
// ============================================================================

/**
 * The main Drip SDK client.
 *
 * @example
 * ```typescript
 * import { Drip } from '@drip-sdk/node';
 *
 * const drip = new Drip({
 *   apiKey: process.env.DRIP_API_KEY!,
 * });
 *
 * // Create a customer
 * const customer = await drip.createCustomer({
 *   onchainAddress: '0x...',
 *   externalCustomerId: 'user_123',
 * });
 *
 * // Record usage and charge
 * const result = await drip.charge({
 *   customerId: customer.id,
 *   meter: 'api_calls',
 *   quantity: 100,
 * });
 *
 * console.log(`Charged ${result.charge.amountUsdc} USDC`);
 * ```
 */
export class Drip {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeout: number;
  private readonly resilience: ResilienceManager | null;

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
   * @param config - Configuration options
   * @throws {Error} If apiKey is not provided
   *
   * @example
   * ```typescript
   * // Basic usage
   * const drip = new Drip({
   *   apiKey: 'sk_live_...',
   * });
   *
   * // With production resilience (recommended)
   * const drip = new Drip({
   *   apiKey: 'sk_live_...',
   *   resilience: true,
   * });
   *
   * // High throughput mode
   * const drip = new Drip({
   *   apiKey: 'sk_live_...',
   *   resilience: 'high-throughput',
   * });
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

    // Setup resilience manager — enabled by default for production safety.
    // Explicit `false` disables it for testing or low-level control.
    if (config.resilience === false) {
      this.resilience = null;
    } else if (config.resilience === 'high-throughput') {
      this.resilience = new ResilienceManager(createHighThroughputResilienceConfig());
    } else if (config.resilience && typeof config.resilience === 'object') {
      this.resilience = new ResilienceManager(config.resilience);
    } else {
      // Default: enabled with standard config (resilience: true or undefined)
      this.resilience = new ResilienceManager(createDefaultResilienceConfig());
    }
  }

  /**
   * Asserts that the SDK was initialized with a secret key (sk_).
   * Throws a clear error if a public key is being used for a secret-key-only operation.
   * @internal
   */
  private assertSecretKey(operation: string): void {
    if (this.keyType === 'public') {
      throw new DripError(
        `${operation} requires a secret key (sk_). You are using a public key (pk_), which cannot access this endpoint. ` +
        `Use a secret key for administrative billing, customer, pricing, subscription, and webhook operations.`,
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
    // Extract method for metrics
    const method = (options.method ?? 'GET').toUpperCase();

    // Use resilience manager if enabled
    if (this.resilience) {
      return this.resilience.execute(
        () => this.rawRequest<T>(path, options),
        method,
        path
      );
    }

    return this.rawRequest<T>(path, options);
  }

  /**
   * Execute the actual HTTP request (internal).
   * @internal
   */
  private async rawRequest<T>(
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

      // Handle 204 No Content
      if (res.status === 204) {
        return { success: true } as T;
      }

      let data: Record<string, unknown>;
      try {
        data = await res.json();
      } catch {
        // Response body is not valid JSON (e.g., HTML error page, empty body)
        if (!res.ok) {
          throw new DripError(
            `Request failed with status ${res.status} (non-JSON response)`,
            res.status,
            'INVALID_RESPONSE',
          );
        }
        throw new DripError(
          `Failed to parse JSON response from ${path}`,
          res.status,
          'PARSE_ERROR',
        );
      }

      if (!res.ok) {
        const message = typeof data.message === 'string' ? data.message
          : typeof data.error === 'string' ? data.error
          : 'Request failed';
        const code = typeof data.code === 'string' ? data.code : undefined;
        throw new DripError(message, res.status, code, data);
      }

      // The generic T is the caller's expected shape. We trust the backend
      // contract here — runtime validation belongs at the API boundary
      // (backend Zod schemas), not in the SDK hot path.
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
  // Health Check Methods
  // ==========================================================================

  /**
   * Pings the Drip API to check connectivity and measure latency.
   *
   * @returns Health status with latency information
   * @throws {DripError} If the request fails or times out
   *
   * @example
   * ```typescript
   * const health = await drip.ping();
   * if (health.ok) {
   *   console.log(`API healthy, latency: ${health.latencyMs}ms`);
   * }
   * ```
   */
  async ping(): Promise<{ ok: boolean; status: string; latencyMs: number; timestamp: number }> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    // Safely construct health endpoint URL
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

      // Try to parse JSON, but handle non-JSON responses gracefully
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
        // Non-JSON response, derive status from HTTP code
        status = response.ok ? 'healthy' : `error:${response.status}`;
      }

      // For non-OK HTTP responses, set appropriate status
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
  // Resilience Methods
  // ==========================================================================

  /**
   * Get SDK metrics (requires resilience to be enabled).
   *
   * Returns aggregated metrics including success rates, latencies, and errors.
   *
   * @returns Metrics summary or null if resilience is not enabled
   *
   * @example
   * ```typescript
   * const drip = new Drip({ apiKey: '...', resilience: true });
   * // ... make some requests ...
   *
   * const metrics = drip.getMetrics();
   * if (metrics) {
   *   console.log(`Success rate: ${metrics.successRate.toFixed(1)}%`);
   *   console.log(`P95 latency: ${metrics.p95LatencyMs.toFixed(0)}ms`);
   * }
   * ```
   */
  getMetrics(): MetricsSummary | null {
    return this.resilience?.getMetrics() ?? null;
  }

  /**
   * Get SDK health status (requires resilience to be enabled).
   *
   * Returns health status including circuit breaker state and rate limiter status.
   *
   * @returns Health status or null if resilience is not enabled
   *
   * @example
   * ```typescript
   * const drip = new Drip({ apiKey: '...', resilience: true });
   *
   * const health = drip.getHealth();
   * if (health) {
   *   console.log(`Circuit: ${health.circuitBreaker.state}`);
   *   console.log(`Available tokens: ${health.rateLimiter.availableTokens}`);
   * }
   * ```
   */
  getHealth(): ResilienceHealth | null {
    return this.resilience?.getHealth() ?? null;
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
   *   metadata: { plan: 'pro' },
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
   *
   * @example
   * ```typescript
   * const customer = await drip.getCustomer('cust_abc123');
   * console.log(customer.onchainAddress);
   * ```
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
   *
   * @example
   * ```typescript
   * // List all customers
   * const { data: customers } = await drip.listCustomers();
   *
   * // List with filters
   * const { data: activeCustomers } = await drip.listCustomers({
   *   status: 'ACTIVE',
   *   limit: 50,
   * });
   * ```
   */
  async listCustomers(
    options?: ListCustomersOptions,
  ): Promise<ListCustomersResponse> {
    this.assertSecretKey('listCustomers()');
    const params = new URLSearchParams();

    if (options?.limit) {
      params.set('limit', options.limit.toString());
    }
    if (options?.offset) {
      params.set('offset', options.offset.toString());
    }
    if (options?.status) {
      params.set('status', options.status);
    }

    const query = params.toString();
    const path = query ? `/customers?${query}` : '/customers';

    return this.request<ListCustomersResponse>(path);
  }

  /**
   * Gets or creates a customer by external ID. Never throws on duplicate.
   *
   * Equivalent to `createCustomer()` but idempotent — if a customer with
   * the given `externalCustomerId` already exists, it is returned instead
   * of throwing a 409 error.
   *
   * @param externalCustomerId - Your internal user/account ID
   * @param metadata - Optional metadata (only used on first creation)
   * @returns The customer (created or existing)
   *
   * @example
   * ```typescript
   * // Safe to call on every request — only creates once
   * const customer = await drip.getOrCreateCustomer('user_123');
   * await drip.charge({ customerId: customer.id, meter: 'api_calls', quantity: 1 });
   * ```
   */
  async getOrCreateCustomer(
    externalCustomerId: string,
    metadata?: Record<string, unknown>,
  ): Promise<Customer> {
    try {
      return await this.createCustomer({ externalCustomerId, metadata });
    } catch (error) {
      if (!(error instanceof DripError) || error.statusCode !== 409) {
        throw error;
      }
      // Customer already exists — use existingCustomerId from 409 body
      const existingId = error.data?.existingCustomerId as string | undefined;
      if (existingId) {
        return this.getCustomer(existingId);
      }
      // Fallback: list and search (for older backends without existingCustomerId)
      const { data } = await this.listCustomers({ limit: 100 });
      const match = data.find((c) => c.externalCustomerId === externalCustomerId);
      if (match) {
        return this.getCustomer(match.id);
      }
      throw new DripError(
        `Customer with externalCustomerId '${externalCustomerId}' exists but could not be resolved`,
        409,
        'CUSTOMER_RESOLUTION_FAILED',
      );
    }
  }

  /**
   * Gets the current balance for a customer.
   *
   * @param customerId - The Drip customer ID
   * @returns Current balance in USDC and native token
   *
   * @example
   * ```typescript
   * const balance = await drip.getBalance('cust_abc123');
   * console.log(`Balance: ${balance.balanceUsdc} USDC`);
   * ```
   */
  async getBalance(customerId: string): Promise<BalanceResult> {
    this.assertSecretKey('getBalance()');
    return this.request<BalanceResult>(`/customers/${customerId}/balance`);
  }

  // ==========================================================================
  // Customer Spending Caps
  // ==========================================================================

  /**
   * Sets a per-customer spending cap.
   *
   * Caps limit how much a single customer can be charged per day, per month,
   * or per individual charge. Multi-level alerts fire at 50%, 80%, 95%, and
   * 100% via `customer.spending.warning` / `customer.spending.blocked` webhooks.
   *
   * @param customerId - The Drip customer ID
   * @param params - Cap parameters
   * @returns The created/updated spending cap
   *
   * @example
   * ```typescript
   * // Limit customer to $500/month
   * const cap = await drip.setCustomerSpendingCap('cust_abc123', {
   *   capType: 'MONTHLY_CHARGE_LIMIT',
   *   limitValue: 500,
   *   autoBlock: true,
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
   *
   * @example
   * ```typescript
   * const { caps } = await drip.getCustomerSpendingCaps('cust_abc123');
   * for (const cap of caps) {
   *   console.log(`${cap.capType}: ${cap.currentUsage}/${cap.limitValue} USDC`);
   * }
   * ```
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
   *
   * @example
   * ```typescript
   * await drip.removeCustomerSpendingCap('cust_abc123', 'cap_xyz');
   * ```
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
  // Customer Provisioning Methods
  // ==========================================================================

  /**
   * Provisions (or re-provisions) an ERC-4337 smart account for a customer.
   * On testnet, auto-funds with USDC.
   */
  async provisionCustomer(customerId: string): Promise<Customer> {
    this.assertSecretKey('provisionCustomer()');
    return this.request<Customer>(
      `/customers/${customerId}/provision`,
      { method: 'POST', body: JSON.stringify({}) },
    );
  }

  /**
   * Syncs a customer's on-chain balance from the blockchain.
   */
  async syncCustomerBalance(customerId: string): Promise<{ balance: string }> {
    this.assertSecretKey('syncCustomerBalance()');
    return this.request<{ balance: string }>(
      `/customers/${customerId}/sync-balance`,
      { method: 'POST', body: JSON.stringify({}) },
    );
  }

  /**
   * Assigns an entitlement plan to a customer with optional overrides.
   */
  async assignCustomerEntitlement(
    customerId: string,
    params: AssignEntitlementParams,
  ): Promise<CustomerEntitlement> {
    this.assertSecretKey('assignCustomerEntitlement()');
    return this.request<CustomerEntitlement>(
      `/customers/${customerId}/entitlement`,
      { method: 'PUT', body: JSON.stringify(params) },
    );
  }

  /**
   * Gets a customer's assigned entitlement plan and current usage.
   */
  async getCustomerEntitlement(customerId: string): Promise<CustomerEntitlement> {
    this.assertSecretKey('getCustomerEntitlement()');
    return this.request<CustomerEntitlement>(
      `/customers/${customerId}/entitlement`,
    );
  }

  // ==========================================================================
  // Contract Methods
  // ==========================================================================

  /**
   * Creates a per-customer pricing contract.
   */
  async createContract(params: CreateContractParams): Promise<Contract> {
    this.assertSecretKey('createContract()');
    return this.request<Contract>(
      '/contracts',
      { method: 'POST', body: JSON.stringify(params) },
    );
  }

  /**
   * Lists contracts for your business.
   */
  async listContracts(options?: {
    customerId?: string;
    status?: 'ACTIVE' | 'EXPIRED' | 'CANCELLED';
  }): Promise<{ contracts: Contract[] }> {
    this.assertSecretKey('listContracts()');
    const params = new URLSearchParams();
    if (options?.customerId) params.set('customerId', options.customerId);
    if (options?.status) params.set('status', options.status);
    const qs = params.toString();
    return this.request<{ contracts: Contract[] }>(
      `/contracts${qs ? `?${qs}` : ''}`,
    );
  }

  /**
   * Gets a specific contract by ID.
   */
  async getContract(contractId: string): Promise<Contract> {
    this.assertSecretKey('getContract()');
    return this.request<Contract>(`/contracts/${contractId}`);
  }

  /**
   * Updates a contract.
   */
  async updateContract(
    contractId: string,
    params: Partial<Pick<CreateContractParams, 'name' | 'endDate' | 'minimumUsdc' | 'maximumUsdc' | 'discountPct' | 'prepaidRollover' | 'includedUnits' | 'metadata'>>,
  ): Promise<Contract> {
    this.assertSecretKey('updateContract()');
    return this.request<Contract>(
      `/contracts/${contractId}`,
      { method: 'PATCH', body: JSON.stringify(params) },
    );
  }

  /**
   * Cancels a contract.
   */
  async deleteContract(contractId: string): Promise<{ success: boolean }> {
    this.assertSecretKey('deleteContract()');
    return this.request<{ success: boolean }>(
      `/contracts/${contractId}`,
      { method: 'DELETE' },
    );
  }

  /**
   * Adds a price override to a contract.
   */
  async addContractOverride(
    contractId: string,
    params: CreateContractOverrideParams,
  ): Promise<ContractPriceOverride> {
    this.assertSecretKey('addContractOverride()');
    return this.request<ContractPriceOverride>(
      `/contracts/${contractId}/overrides`,
      { method: 'POST', body: JSON.stringify(params) },
    );
  }

  /**
   * Removes a price override from a contract.
   */
  async removeContractOverride(
    contractId: string,
    unitType: string,
  ): Promise<{ success: boolean }> {
    this.assertSecretKey('removeContractOverride()');
    return this.request<{ success: boolean }>(
      `/contracts/${contractId}/overrides/${encodeURIComponent(unitType)}`,
      { method: 'DELETE' },
    );
  }

  // ==========================================================================
  // Entitlement Plan Methods
  // ==========================================================================

  /**
   * Creates an entitlement plan.
   */
  async createEntitlementPlan(params: CreateEntitlementPlanParams): Promise<EntitlementPlan> {
    this.assertSecretKey('createEntitlementPlan()');
    return this.request<EntitlementPlan>(
      '/entitlement-plans',
      { method: 'POST', body: JSON.stringify(params) },
    );
  }

  /**
   * Lists all entitlement plans.
   */
  async listEntitlementPlans(): Promise<{ plans: EntitlementPlan[] }> {
    this.assertSecretKey('listEntitlementPlans()');
    return this.request<{ plans: EntitlementPlan[] }>('/entitlement-plans');
  }

  /**
   * Gets a specific entitlement plan.
   */
  async getEntitlementPlan(planId: string): Promise<EntitlementPlan> {
    this.assertSecretKey('getEntitlementPlan()');
    return this.request<EntitlementPlan>(`/entitlement-plans/${planId}`);
  }

  /**
   * Updates an entitlement plan.
   */
  async updateEntitlementPlan(
    planId: string,
    params: Partial<Pick<CreateEntitlementPlanParams, 'name' | 'description'> & { isDefault?: boolean; isActive?: boolean }>,
  ): Promise<EntitlementPlan> {
    this.assertSecretKey('updateEntitlementPlan()');
    return this.request<EntitlementPlan>(
      `/entitlement-plans/${planId}`,
      { method: 'PATCH', body: JSON.stringify(params) },
    );
  }

  /**
   * Deactivates an entitlement plan.
   */
  async deleteEntitlementPlan(planId: string): Promise<{ success: boolean }> {
    this.assertSecretKey('deleteEntitlementPlan()');
    return this.request<{ success: boolean }>(
      `/entitlement-plans/${planId}`,
      { method: 'DELETE' },
    );
  }

  /**
   * Adds a feature rule to an entitlement plan.
   */
  async addEntitlementRule(
    planId: string,
    params: CreateEntitlementRuleParams,
  ): Promise<EntitlementRule> {
    this.assertSecretKey('addEntitlementRule()');
    return this.request<EntitlementRule>(
      `/entitlement-plans/${planId}/rules`,
      { method: 'POST', body: JSON.stringify(params) },
    );
  }

  /**
   * Lists rules for an entitlement plan.
   */
  async listEntitlementRules(planId: string): Promise<{ rules: EntitlementRule[] }> {
    this.assertSecretKey('listEntitlementRules()');
    return this.request<{ rules: EntitlementRule[] }>(
      `/entitlement-plans/${planId}/rules`,
    );
  }

  /**
   * Updates an entitlement rule.
   */
  async updateEntitlementRule(
    ruleId: string,
    params: Partial<Pick<CreateEntitlementRuleParams, 'limitValue' | 'unlimited'>>,
  ): Promise<EntitlementRule> {
    this.assertSecretKey('updateEntitlementRule()');
    return this.request<EntitlementRule>(
      `/entitlement-rules/${ruleId}`,
      { method: 'PATCH', body: JSON.stringify(params) },
    );
  }

  /**
   * Deletes an entitlement rule.
   */
  async deleteEntitlementRule(ruleId: string): Promise<{ success: boolean }> {
    this.assertSecretKey('deleteEntitlementRule()');
    return this.request<{ success: boolean }>(
      `/entitlement-rules/${ruleId}`,
      { method: 'DELETE' },
    );
  }

  // ==========================================================================
  // Charge Methods — REMOVED
  // ==========================================================================
  // `charge()` and `chargeAsync()` were removed. All usage should flow through
  // `trackUsage()`, which records the event and lets the backend decide
  // whether to create a billable charge (based on the pricing plan) and when
  // to batch-settle on-chain. Direct "charge" semantics are a server concern,
  // not a client concern.
  //
  // Migration:
  //   drip.charge({ customerId, meter, quantity })
  //     → drip.trackUsage({ customerId, meter, quantity })
  //
  //   drip.chargeAsync({ customerId, meter, quantity })
  //     → drip.trackUsage({ customerId, meter, quantity, mode: 'batch' })
  //
  // `getCharge()` / `listCharges()` remain for read-only reconciliation.

  /**
   * Wraps an external API call with guaranteed usage recording.
   *
   * **This solves the crash-before-record problem:**
   * ```typescript
   * // DANGEROUS - usage lost if crash between lines 1 and 2:
   * const response = await openai.chat.completions.create({...}); // line 1
   * await drip.charge({ tokens: response.usage.total_tokens });   // line 2
   *
   * // SAFE - wrapApiCall guarantees recording with retry:
   * const { result } = await drip.wrapApiCall({
   *   call: () => openai.chat.completions.create({...}),
   *   extractUsage: (r) => r.usage.total_tokens,
   *   ...
   * });
   * ```
   *
   * How it works:
   * 1. Generates idempotency key BEFORE the API call
   * 2. Makes the external API call (once, no retry)
   * 3. Records usage in Drip with retry + idempotency
   * 4. If recording fails transiently, retries are safe (no double-charge)
   *
   * @param params - Wrap parameters including the call and usage extractor
   * @returns The API result and charge details
   * @throws {DripError} If the Drip charge fails after retries
   * @throws {Error} If the external API call fails
   *
   * @example
   * ```typescript
   * // OpenAI example
   * const { result, charge } = await drip.wrapApiCall({
   *   customerId: 'cust_abc123',
   *   meter: 'tokens',
   *   call: () => openai.chat.completions.create({
   *     model: 'gpt-4',
   *     messages: [{ role: 'user', content: 'Hello!' }],
   *   }),
   *   extractUsage: (r) => r.usage?.total_tokens ?? 0,
   * });
   *
   * console.log(result.choices[0].message.content);
   * console.log(`Charged: ${charge.charge.amountUsdc} USDC`);
   * ```
   *
   * @example
   * ```typescript
   * // Anthropic example
   * const { result, charge } = await drip.wrapApiCall({
   *   customerId: 'cust_abc123',
   *   meter: 'tokens',
   *   call: () => anthropic.messages.create({
   *     model: 'claude-3-opus-20240229',
   *     max_tokens: 1024,
   *     messages: [{ role: 'user', content: 'Hello!' }],
   *   }),
   *   extractUsage: (r) => r.usage.input_tokens + r.usage.output_tokens,
   * });
   * ```
   *
   * @example
   * ```typescript
   * // With custom retry options
   * const { result } = await drip.wrapApiCall({
   *   customerId: 'cust_abc123',
   *   meter: 'api_calls',
   *   call: () => fetch('https://api.example.com/expensive'),
   *   extractUsage: () => 1, // Fixed cost per call
   *   retryOptions: {
   *     maxAttempts: 5,
   *     baseDelayMs: 200,
   *   },
   * });
   * ```
   */
  async wrapApiCall<T>(params: WrapApiCallParams<T>): Promise<WrapApiCallResult<T>> {
    const identity = params.customerId ?? params.externalCustomerId;
    if (!identity) {
      throw new DripError(
        'wrapApiCall(): either customerId or externalCustomerId is required',
        400,
        'VALIDATION_ERROR',
      );
    }
    // Generate idempotency key BEFORE the call - this is the key insight!
    // Even if we crash after the API call, retrying with the same key is safe.
    // CRIT-09: Process-scoped deterministic key with startup entropy + counter.
    // quantity isn't known until after the call, so we use identity + meter.
    // Startup entropy plus monotonic counter ensures uniqueness across cold starts.
    const idempotencyKey = params.idempotencyKey
      ?? deterministicIdempotencyKey('wrap', identity, params.meter);

    // Step 1: Make the external API call (no retry - we don't control this)
    const result = await params.call();

    // Step 2: Extract usage from the result
    const quantity = params.extractUsage(result);

    // Step 3: Record usage in Drip with retry (idempotency makes this safe).
    // Uses trackUsage() instead of the removed charge() method; the backend
    // still creates a charge if a pricing plan matches the unit type.
    const charge = await retryWithBackoff(
      () =>
        this.trackUsage({
          customerId: params.customerId,
          externalCustomerId: params.externalCustomerId,
          meter: params.meter,
          quantity,
          idempotencyKey,
          metadata: params.metadata,
        }),
      params.retryOptions,
    );

    return {
      result,
      charge: charge as unknown as ChargeResult,
      idempotencyKey,
    };
  }

  /**
   * Records usage for internal visibility WITHOUT billing.
   *
   * Use this for:
   * - Tracking internal team usage without charging
   * - Pilot programs where you want visibility before billing
   * - Pre-billing tracking before customer has on-chain wallet
   *
   * This does NOT:
   * - Create a Charge record
   * - Require customer balance
   * - Require blockchain/wallet setup
   *
   * For billing, use `charge()` instead.
   *
   * @param params - The usage tracking parameters
   * @returns The tracked usage event
   *
   * @example
   * ```typescript
   * const result = await drip.trackUsage({
   *   customerId: 'cust_abc123',
   *   meter: 'api_calls',
   *   quantity: 100,
   *   description: 'API calls during trial period',
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
    const identity = params.customerId ?? params.externalCustomerId;
    if (!identity) {
      throw new DripError(
        'trackUsage(): either customerId or externalCustomerId is required',
        400,
        'VALIDATION_ERROR',
      );
    }
    // Apply defaults locally so the deterministic idempotency key is stable
    // even when callers omit meter/quantity. These match the server defaults.
    const meter = params.meter ?? 'generic';
    const quantity = params.quantity ?? 1;
    const idempotencyKey = params.idempotencyKey
      ?? deterministicIdempotencyKey('track', identity, meter, quantity);
    const mode = params.mode ?? 'sync';
    // Default path is `/usage` (billing-aware: creates a charge if a pricing
    // plan matches, otherwise tracks without a charge). `mode: 'batch'` uses
    // `/usage/async` for fire-and-forget. `mode: 'internal'` opts into the
    // visibility-only path that never creates charges.
    const path =
      mode === 'internal'
        ? '/usage/internal'
        : mode === 'batch'
          ? '/usage/async'
          : '/usage';

    const result = await this.request<TrackUsageResult>(path, {
      method: 'POST',
      body: JSON.stringify({
        customerId: params.customerId,
        externalCustomerId: params.externalCustomerId,
        usageType: meter,
        quantity,
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

  // `chargeAsync()` removed — use `trackUsage({ ..., mode: 'batch' })`.

  /**
   * Lists execution events with optional filters.
   *
   * @param options - Optional filtering and pagination
   * @returns Paginated list of events
   *
   * @example
   * ```typescript
   * // List all events for a customer
   * const { data: events } = await drip.listEvents({
   *   customerId: 'cust_abc123',
   * });
   *
   * // Filter by event type
   * const { data: toolCalls } = await drip.listEvents({
   *   eventType: 'tool_call',
   *   outcome: 'SUCCESS',
   * });
   * ```
   */
  async listEvents(options?: ListEventsOptions): Promise<ListEventsResponse> {
    this.assertSecretKey('listEvents()');
    const params = new URLSearchParams();

    if (options?.customerId) {
      params.set('customerId', options.customerId);
    }
    if (options?.runId) {
      params.set('runId', options.runId);
    }
    if (options?.eventType) {
      params.set('eventType', options.eventType);
    }
    if (options?.outcome) {
      params.set('outcome', options.outcome);
    }
    if (options?.limit !== undefined) {
      params.set('limit', String(options.limit));
    }
    if (options?.offset !== undefined) {
      params.set('offset', String(options.offset));
    }

    const query = params.toString();
    return this.request<ListEventsResponse>(`/events${query ? `?${query}` : ''}`);
  }

  /**
   * Retrieves a single execution event by ID.
   *
   * @param eventId - The event ID
   * @returns Full event details
   * @throws {DripError} If event not found (404)
   *
   * @example
   * ```typescript
   * const event = await drip.getEvent('evt_abc123');
   * console.log(`${event.eventType}: ${event.outcome}`);
   * ```
   */
  async getEvent(eventId: string): Promise<ExecutionEvent> {
    this.assertSecretKey('getEvent()');
    return this.request<ExecutionEvent>(`/events/${eventId}`);
  }

  /**
   * Gets the full causality trace for an event.
   *
   * Returns ancestors (parent chain), children (caused by this event),
   * and retry chain (if the event was retried).
   *
   * @param eventId - The event ID to trace
   * @returns Causality trace with ancestors, children, and retry chain
   * @throws {DripError} If event not found (404)
   *
   * @example
   * ```typescript
   * const trace = await drip.getEventTrace('evt_abc123');
   * console.log(`Ancestors: ${trace.ancestors.length}`);
   * console.log(`Children: ${trace.children.length}`);
   * ```
   */
  async getEventTrace(eventId: string): Promise<EventTrace> {
    this.assertSecretKey('getEventTrace()');
    return this.request<EventTrace>(`/events/${eventId}/trace`);
  }

  /**
   * Retrieves a specific charge by ID.
   *
   * @param chargeId - The charge ID
   * @returns The charge details
   * @throws {DripError} If charge not found (404)
   *
   * @example
   * ```typescript
   * const charge = await drip.getCharge('chg_abc123');
   * console.log(`Status: ${charge.status}`);
   * ```
   */
  async getCharge(chargeId: string): Promise<Charge> {
    this.assertSecretKey('getCharge()');
    return this.request<Charge>(`/charges/${chargeId}`);
  }

  /**
   * Lists charges for your business.
   *
   * @param options - Optional filtering and pagination
   * @returns List of charges
   *
   * @example
   * ```typescript
   * // List all charges
   * const { data: charges } = await drip.listCharges();
   *
   * // List charges for a specific customer
   * const { data: customerCharges } = await drip.listCharges({
   *   customerId: 'cust_abc123',
   *   status: 'CONFIRMED',
   * });
   * ```
   */
  async listCharges(options?: ListChargesOptions): Promise<ListChargesResponse> {
    this.assertSecretKey('listCharges()');
    const params = new URLSearchParams();

    if (options?.customerId) {
      params.set('customerId', options.customerId);
    }
    if (options?.status) {
      params.set('status', options.status);
    }
    if (options?.limit) {
      params.set('limit', options.limit.toString());
    }
    if (options?.offset) {
      params.set('offset', options.offset.toString());
    }

    const query = params.toString();
    const path = query ? `/charges?${query}` : '/charges';

    return this.request<ListChargesResponse>(path);
  }

  // ==========================================================================
  // Entitlement Methods (Quota Management)
  // ==========================================================================

  /**
   * Check if a customer is allowed to use a feature based on their entitlement plan.
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
  // Eliminates the need to grandfather customers on old plans. Every apply
  // call captures a full audit snapshot that can be rolled back.
  // All methods require a secret key.
  // ==========================================================================

  /**
   * Change a single customer's pricing without creating a new global plan.
   *
   * @param customerId - The Drip customer ID
   * @param params - What to change (at least one field required)
   * @returns A CustomerPlanChange row with audit + proration details
   *
   * @example
   * ```typescript
   * await drip.applyCustomerPricingChange('cust_123', {
   *   sourcePricingPlanIds: ['plan_pro_api'],
   *   discountPct: '15',
   *   prorate: true,
   *   reason: 'Upgraded after sales call',
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
   * Change a single customer's entitlement plan (and/or per-feature overrides)
   * without grandfathering.
   *
   * @example
   * ```typescript
   * await drip.applyCustomerEntitlementChange('cust_123', {
   *   planId: 'plan_pro',
   *   overrides: { search: { dailyLimit: 10000 } },
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
    return this.request<ListCustomerPlanChangesResult>(
      `/customers/${encodeURIComponent(customerId)}/plan-changes${query ? `?${query}` : ''}`,
    );
  }

  /** Retrieve a single plan change by ID. */
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
   * writing an inverse change row to the history.
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

  // ==========================================================================
  // Checkout Methods (Fiat On-Ramp)
  // ==========================================================================

  /**
   * Creates a checkout session to add funds to a customer's account.
   *
   * This is the PRIMARY method for getting money into Drip. It returns a URL
   * to a hosted checkout page where customers can pay via:
   * - Bank transfer (ACH) - $0.50 flat fee, 1-2 business days
   * - Debit card - 1.5% fee, instant
   * - Direct USDC - no fee, instant
   *
   * After payment, the customer is redirected to your returnUrl with:
   * - session_id: The checkout session ID
   * - customer_id: The Drip customer ID
   * - status: "success" or "failed"
   *
   * @param params - Checkout parameters
   * @returns Checkout session with redirect URL
   *
   * @example
   * ```typescript
   * // Basic checkout
   * const { url } = await drip.checkout({
   *   customerId: 'cust_abc123',
   *   amount: 5000, // $50.00
   *   returnUrl: 'https://myapp.com/dashboard',
   * });
   *
   * // Redirect user to checkout
   * res.redirect(url);
   * ```
   *
   * @example
   * ```typescript
   * // Checkout for new customer
   * const { url, id } = await drip.checkout({
   *   externalCustomerId: 'user_123', // Your user ID
   *   amount: 10000, // $100.00
   *   returnUrl: 'https://myapp.com/welcome',
   *   metadata: { plan: 'pro' },
   * });
   * ```
   */
  async checkout(params: CheckoutParams): Promise<CheckoutResult> {
    const response = await this.request<{
      id: string;
      url: string;
      expires_at: string;
      amount_usd: number;
    }>('/checkout', {
      method: 'POST',
      body: JSON.stringify({
        customer_id: params.customerId,
        external_customer_id: params.externalCustomerId,
        amount: params.amount,
        return_url: params.returnUrl,
        cancel_url: params.cancelUrl,
        metadata: params.metadata,
      }),
    });

    return {
      id: response.id,
      url: response.url,
      expiresAt: response.expires_at,
      amountUsd: response.amount_usd,
    };
  }

  // ==========================================================================
  // Withdrawal Methods (Fiat Off-Ramp)
  // ==========================================================================

  /**
   * Creates a withdrawal to convert USDC to fiat (bank transfer).
   *
   * **Fee:** ~1% for ACH (deducted from withdrawal amount).
   * Drip does not take a cut — fee goes to the off-ramp provider.
   *
   * **Flow:**
   * 1. USDC withdrawn from your settlement balance
   * 2. Converted to fiat via Infinite
   * 3. ACH to your bank account (1-3 business days)
   *
   * @param params - Withdrawal parameters
   * @returns Withdrawal details with fee breakdown
   *
   * @example
   * ```typescript
   * const withdrawal = await drip.withdraw({
   *   amountUsdc: '500.00',
   *   idempotencyKey: 'withdraw_2024_q1',
   * });
   * console.log(withdrawal.netAmountUsdc); // "495.00" (after 1% fee)
   * ```
   */
  async withdraw(params: WithdrawParams): Promise<WithdrawalResult> {
    const response = await this.request<{
      id: string;
      status: string;
      amount_usdc: string;
      fee_usdc: string;
      net_amount_usdc: string;
      fiat_currency: string;
      bank_description: string | null;
      created_at: string;
      completed_at: string | null;
    }>('/withdrawals', {
      method: 'POST',
      body: JSON.stringify({
        amount_usdc: params.amountUsdc,
        idempotency_key: params.idempotencyKey,
        bank_description: params.bankDescription,
      }),
    });

    return {
      id: response.id,
      status: response.status as WithdrawalStatus,
      amountUsdc: response.amount_usdc,
      feeUsdc: response.fee_usdc,
      netAmountUsdc: response.net_amount_usdc,
      fiatCurrency: response.fiat_currency,
      bankDescription: response.bank_description,
      createdAt: response.created_at,
      completedAt: response.completed_at,
    };
  }

  /**
   * Lists withdrawals for the authenticated business.
   *
   * @param options - Filter and pagination options
   * @returns Array of withdrawals with total count
   */
  async listWithdrawals(options: {
    status?: WithdrawalStatus;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ withdrawals: WithdrawalResult[]; total: number }> {
    const params = new URLSearchParams();
    if (options.status) params.set('status', options.status);
    if (options.limit !== undefined) params.set('limit', options.limit.toString());
    if (options.offset !== undefined) params.set('offset', options.offset.toString());

    const qs = params.toString();
    const response = await this.request<{
      withdrawals: Array<{
        id: string;
        status: string;
        amount_usdc: string;
        fee_usdc: string;
        net_amount_usdc: string;
        fiat_currency: string;
        bank_description: string | null;
        created_at: string;
        completed_at: string | null;
      }>;
      total: number;
    }>(`/withdrawals${qs ? `?${qs}` : ''}`);

    return {
      withdrawals: response.withdrawals.map((w) => ({
        id: w.id,
        status: w.status as WithdrawalStatus,
        amountUsdc: w.amount_usdc,
        feeUsdc: w.fee_usdc,
        netAmountUsdc: w.net_amount_usdc,
        fiatCurrency: w.fiat_currency,
        bankDescription: w.bank_description,
        createdAt: w.created_at,
        completedAt: w.completed_at,
      })),
      total: response.total,
    };
  }

  /**
   * Gets a withdrawal fee estimate for a given amount.
   *
   * @param amountUsdc - Amount to withdraw
   * @returns Fee breakdown and estimated arrival time
   *
   * @example
   * ```typescript
   * const estimate = await drip.estimateWithdrawalFee('1000.00');
   * console.log(estimate.feeUsdc); // "10.00" (1%)
   * console.log(estimate.estimatedArrival); // "1-3 business days"
   * ```
   */
  async estimateWithdrawalFee(amountUsdc: string): Promise<WithdrawalFeeEstimate> {
    const response = await this.request<{
      amount_usdc: string;
      fee_usdc: string;
      net_amount_usdc: string;
      fee_bps: number;
      fee_percent: string;
      method: string;
      estimated_arrival: string;
    }>(`/withdrawals/fee-estimate?amount_usdc=${encodeURIComponent(amountUsdc)}`);

    return {
      amountUsdc: response.amount_usdc,
      feeUsdc: response.fee_usdc,
      netAmountUsdc: response.net_amount_usdc,
      feeBps: response.fee_bps,
      feePercent: response.fee_percent,
      method: response.method,
      estimatedArrival: response.estimated_arrival,
    };
  }

  /**
   * Cancels a pending withdrawal.
   *
   * Only works if the withdrawal is still in PENDING status
   * (before on-chain processing begins).
   *
   * @param withdrawalId - The withdrawal ID to cancel
   */
  async cancelWithdrawal(withdrawalId: string): Promise<{ id: string; status: string; cancelled: boolean }> {
    return this.request<{ id: string; status: string; cancelled: boolean }>(
      `/withdrawals/${withdrawalId}`,
      { method: 'DELETE' },
    );
  }

  // ==========================================================================
  // Webhook Methods
  // ==========================================================================

  /**
   * Creates a new webhook endpoint.
   *
   * The webhook secret is only returned once at creation time.
   * Store it securely for verifying webhook signatures.
   *
   * @param config - Webhook configuration
   * @returns The created webhook with its secret
   *
   * @example
   * ```typescript
   * const webhook = await drip.createWebhook({
   *   url: 'https://api.yourapp.com/webhooks/drip',
   *   events: ['charge.succeeded', 'charge.failed'],
   *   description: 'Main webhook endpoint',
   * });
   *
   * // IMPORTANT: Save this secret securely!
   * console.log(`Webhook secret: ${webhook.secret}`);
   * ```
   */
  async createWebhook(
    config: CreateWebhookParams,
  ): Promise<CreateWebhookResponse> {
    this.assertSecretKey('createWebhook()');
    return this.request<CreateWebhookResponse>('/webhooks', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  }

  /**
   * Updates an existing webhook endpoint.
   *
   * @param webhookId - The webhook ID to update
   * @param params - Fields to update
   * @returns The updated webhook
   *
   * @example
   * ```typescript
   * // Add per-endpoint filters
   * const updated = await drip.updateWebhook('wh_abc123', {
   *   filters: { customerIds: ['cust_xyz'], severities: ['high'] },
   * });
   *
   * // Remove all filters
   * await drip.updateWebhook('wh_abc123', { filters: null });
   * ```
   */
  async updateWebhook(
    webhookId: string,
    params: UpdateWebhookParams,
  ): Promise<Webhook> {
    this.assertSecretKey('updateWebhook()');
    return this.request<Webhook>(`/webhooks/${webhookId}`, {
      method: 'PATCH',
      body: JSON.stringify(params),
    });
  }

  /**
   * Lists all webhook endpoints for your business.
   *
   * @returns List of webhooks with delivery statistics
   *
   * @example
   * ```typescript
   * const { data: webhooks } = await drip.listWebhooks();
   * webhooks.forEach(wh => {
   *   console.log(`${wh.url}: ${wh.stats?.successfulDeliveries} successful`);
   * });
   * ```
   */
  async listWebhooks(): Promise<ListWebhooksResponse> {
    this.assertSecretKey('listWebhooks()');
    return this.request<ListWebhooksResponse>('/webhooks');
  }

  /**
   * Retrieves a specific webhook by ID.
   *
   * @param webhookId - The webhook ID
   * @returns The webhook details with statistics
   * @throws {DripError} If webhook not found (404)
   *
   * @example
   * ```typescript
   * const webhook = await drip.getWebhook('wh_abc123');
   * console.log(`Events: ${webhook.events.join(', ')}`);
   * ```
   */
  async getWebhook(webhookId: string): Promise<Webhook> {
    this.assertSecretKey('getWebhook()');
    return this.request<Webhook>(`/webhooks/${webhookId}`);
  }

  /**
   * Deletes a webhook endpoint.
   *
   * @param webhookId - The webhook ID to delete
   * @returns Success confirmation
   * @throws {DripError} If webhook not found (404)
   *
   * @example
   * ```typescript
   * await drip.deleteWebhook('wh_abc123');
   * console.log('Webhook deleted');
   * ```
   */
  async deleteWebhook(webhookId: string): Promise<DeleteWebhookResponse> {
    this.assertSecretKey('deleteWebhook()');
    return this.request<DeleteWebhookResponse>(`/webhooks/${webhookId}`, {
      method: 'DELETE',
    });
  }

  /**
   * Tests a webhook by sending a test event.
   *
   * @param webhookId - The webhook ID to test
   * @returns Test result
   *
   * @example
   * ```typescript
   * const result = await drip.testWebhook('wh_abc123');
   * console.log(`Test status: ${result.status}`);
   * ```
   */
  async testWebhook(
    webhookId: string,
  ): Promise<{ message: string; deliveryId: string | null; status: string }> {
    this.assertSecretKey('testWebhook()');
    return this.request<{
      message: string;
      deliveryId: string | null;
      status: string;
    }>(`/webhooks/${webhookId}/test`, {
      method: 'POST',
    });
  }

  /**
   * Rotates the signing secret for a webhook.
   *
   * After rotation, update your application to use the new secret.
   *
   * @param webhookId - The webhook ID
   * @returns The new secret
   *
   * @example
   * ```typescript
   * const { secret } = await drip.rotateWebhookSecret('wh_abc123');
   * console.log(`New secret: ${secret}`);
   * // Update your application with the new secret!
   * ```
   */
  async rotateWebhookSecret(
    webhookId: string,
  ): Promise<{ secret: string; message: string }> {
    this.assertSecretKey('rotateWebhookSecret()');
    return this.request<{ secret: string; message: string }>(
      `/webhooks/${webhookId}/rotate-secret`,
      { method: 'POST' },
    );
  }

  // ==========================================================================
  // Subscription Methods
  // ==========================================================================

  /**
   * Creates a new recurring subscription for a customer.
   *
   * @param params - Subscription creation parameters
   * @returns The created subscription
   *
   * @example
   * ```typescript
   * const sub = await drip.createSubscription({
   *   customerId: 'cust_abc123',
   *   name: 'Pro Plan',
   *   interval: 'MONTHLY',
   *   priceUsdc: 49.99,
   * });
   * ```
   */
  async createSubscription(
    params: CreateSubscriptionParams,
  ): Promise<Subscription> {
    this.assertSecretKey('createSubscription');
    return this.request<Subscription>('/subscriptions', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Retrieves a subscription by ID.
   *
   * @param subscriptionId - The subscription ID
   * @returns The subscription details
   *
   * @example
   * ```typescript
   * const sub = await drip.getSubscription('sub_abc123');
   * console.log(`Status: ${sub.status}, Next billing: ${sub.currentPeriodEnd}`);
   * ```
   */
  async getSubscription(subscriptionId: string): Promise<Subscription> {
    this.assertSecretKey('getSubscription()');
    return this.request<Subscription>(`/subscriptions/${subscriptionId}`);
  }

  /**
   * Lists subscriptions for your business.
   *
   * @param options - Filter and pagination options
   * @returns List of subscriptions
   *
   * @example
   * ```typescript
   * const { data: subs } = await drip.listSubscriptions({ status: 'ACTIVE' });
   * subs.forEach(s => console.log(`${s.name}: $${s.priceUsdc}/${s.interval}`));
   * ```
   */
  async listSubscriptions(
    options?: ListSubscriptionsOptions,
  ): Promise<ListSubscriptionsResponse> {
    this.assertSecretKey('listSubscriptions()');
    const params: Record<string, string> = {};
    if (options?.customerId) params.customerId = options.customerId;
    if (options?.status) params.status = options.status;
    if (options?.limit) params.limit = String(options.limit);

    const queryString = Object.entries(params)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join('&');

    const path = queryString ? `/subscriptions?${queryString}` : '/subscriptions';
    return this.request<ListSubscriptionsResponse>(path);
  }

  /**
   * Updates a subscription. Price changes take effect at the next billing period.
   *
   * @param subscriptionId - The subscription ID
   * @param params - Fields to update
   * @returns The updated subscription
   *
   * @example
   * ```typescript
   * const updated = await drip.updateSubscription('sub_abc123', {
   *   priceUsdc: 99.99,
   *   name: 'Enterprise Plan',
   * });
   * ```
   */
  async updateSubscription(
    subscriptionId: string,
    params: UpdateSubscriptionParams,
  ): Promise<Subscription> {
    this.assertSecretKey('updateSubscription');
    return this.request<Subscription>(`/subscriptions/${subscriptionId}`, {
      method: 'PATCH',
      body: JSON.stringify(params),
    });
  }

  /**
   * Cancels a subscription. By default, cancels at the end of the current billing period.
   *
   * @param subscriptionId - The subscription ID
   * @param params - Cancellation options
   * @returns The cancelled subscription
   *
   * @example
   * ```typescript
   * // Cancel at end of period (default)
   * await drip.cancelSubscription('sub_abc123');
   *
   * // Cancel immediately
   * await drip.cancelSubscription('sub_abc123', { immediate: true });
   * ```
   */
  async cancelSubscription(
    subscriptionId: string,
    params?: CancelSubscriptionParams,
  ): Promise<Subscription> {
    this.assertSecretKey('cancelSubscription');
    return this.request<Subscription>(
      `/subscriptions/${subscriptionId}/cancel`,
      {
        method: 'POST',
        body: JSON.stringify(params ?? {}),
      },
    );
  }

  /**
   * Pauses an active subscription. No charges will be created while paused.
   *
   * @param subscriptionId - The subscription ID
   * @param params - Pause options (optional auto-resume date)
   * @returns The paused subscription
   *
   * @example
   * ```typescript
   * // Pause indefinitely
   * await drip.pauseSubscription('sub_abc123');
   *
   * // Pause with auto-resume
   * await drip.pauseSubscription('sub_abc123', {
   *   resumeDate: '2026-04-01T00:00:00Z',
   * });
   * ```
   */
  async pauseSubscription(
    subscriptionId: string,
    params?: PauseSubscriptionParams,
  ): Promise<Subscription> {
    this.assertSecretKey('pauseSubscription');
    return this.request<Subscription>(
      `/subscriptions/${subscriptionId}/pause`,
      {
        method: 'POST',
        body: JSON.stringify(params ?? {}),
      },
    );
  }

  /**
   * Resumes a paused subscription. Starts a new billing period.
   *
   * @param subscriptionId - The subscription ID
   * @returns The resumed subscription
   *
   * @example
   * ```typescript
   * const sub = await drip.resumeSubscription('sub_abc123');
   * console.log(`Resumed, next billing: ${sub.currentPeriodEnd}`);
   * ```
   */
  async resumeSubscription(subscriptionId: string): Promise<Subscription> {
    this.assertSecretKey('resumeSubscription');
    return this.request<Subscription>(
      `/subscriptions/${subscriptionId}/resume`,
      { method: 'POST' },
    );
  }

  // ==========================================================================
  // Run & Event Methods (Execution Ledger)
  // ==========================================================================

  /**
   * Creates a new workflow definition.
   *
   * @param params - Workflow creation parameters
   * @returns The created workflow
   *
   * @example
   * ```typescript
   * const workflow = await drip.createWorkflow({
   *   name: 'Document Processing',
   *   slug: 'doc_processing',
   *   productSurface: 'AGENT',
   * });
   * ```
   */
  async createWorkflow(params: CreateWorkflowParams): Promise<Workflow> {
    return this.request<Workflow>('/workflows', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Lists all workflows for your business.
   *
   * @returns List of workflows
   */
  async listWorkflows(): Promise<{ data: Workflow[]; count: number }> {
    return this.request<{ data: Workflow[]; count: number }>('/workflows');
  }

  /**
   * Starts a new agent run for tracking execution.
   *
   * @param params - Run parameters
   * @returns The started run
   *
   * @example
   * ```typescript
   * const run = await drip.startRun({
   *   customerId: 'cust_abc123',
   *   workflowId: 'wf_xyz789',
   *   correlationId: 'req_unique_123',
   * });
   *
   * // Emit events during execution...
   *
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
   *
   * @example
   * ```typescript
   * await drip.endRun(run.id, {
   *   status: 'COMPLETED',
   * });
   *
   * // Or with error:
   * await drip.endRun(run.id, {
   *   status: 'FAILED',
   *   errorMessage: 'Customer validation failed',
   *   errorCode: 'VALIDATION_ERROR',
   * });
   * ```
   */
  async endRun(
    runId: string,
    params: EndRunParams,
  ): Promise<{
    id: string;
    status: RunStatus;
    endedAt: string | null;
    durationMs: number | null;
    eventCount: number;
    totalCostUnits: string | null;
  }> {
    return this.request(`/runs/${runId}`, {
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
   * This is the key endpoint for debugging "what happened" in an execution.
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
   * Each event is assigned a unique idempotency key (auto-generated if not provided).
   * This maps each inference or API call to a single trackable event.
   * Use `Drip.generateIdempotencyKey()` for deterministic key generation.
   *
   * @param params - Event parameters
   * @returns The created event
   *
   * @example
   * ```typescript
   * await drip.emitEvent({
   *   runId: run.id,
   *   eventType: 'agent.validate',
   *   quantity: 1,
   *   description: 'Validated prescription format',
   *   costUnits: 0.001,
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
   *
   * @example
   * ```typescript
   * const result = await drip.emitEventsBatch([
   *   { runId: run.id, eventType: 'agent.step1', quantity: 1 },
   *   { runId: run.id, eventType: 'agent.step2', quantity: 100, units: 'tokens' },
   * ]);
   *
   * console.log(`Created: ${result.created}, Duplicates: ${result.duplicates}`);
   * ```
   */
  async emitEventsBatch(
    events: Array<Omit<EmitEventParams, 'runId'> & {
      runId?: string;
      customerId?: string;
      workflowId?: string;
    }>,
  ): Promise<{
    success: boolean;
    created: number;
    duplicates: number;
    skipped: number;
    events: Array<{ id: string; eventType: string; isDuplicate: boolean; skipped?: boolean; reason?: string }>;
  }> {
    // Auto-generate deterministic idempotencyKey for any event that doesn't have one.
    // Uses the same deterministic helper as emitEvent() — no Date.now() or Math.random().
    const normalized = events.map((e) => ({
      ...e,
      idempotencyKey: e.idempotencyKey
        ?? deterministicIdempotencyKey('evt', e.runId, e.customerId, e.eventType, e.quantity),
    }));
    return this.request('/run-events/batch', {
      method: 'POST',
      body: JSON.stringify({ events: normalized }),
    });
  }

  // ==========================================================================
  // Simplified API Methods
  // ==========================================================================

  /**
   * Lists all available meters (usage types) for your business.
   *
   * Use this to discover what meter names are valid for the `charge()` method.
   * Meters are defined by your pricing plans.
   *
   * @returns List of available meters with their prices
   *
   * @example
   * ```typescript
   * const { data: meters } = await drip.listMeters();
   *
   * console.log('Available meters:');
   * for (const meter of meters) {
   *   console.log(`  ${meter.meter}: $${meter.unitPriceUsd}/unit`);
   * }
   *
   * // Use in charge():
   * await drip.charge({
   *   customerId: 'cust_123',
   *   meter: meters[0].meter,  // Use a valid meter name
   *   quantity: 100,
   * });
   * ```
   */
  async listMeters(): Promise<ListMetersResponse> {
    this.assertSecretKey('listMeters()');
    const response = await this.request<{
      data: Array<{
        id: string;
        name: string;
        unitType: string;
        unitPriceUsd: string;
        isActive: boolean;
      }>;
      count: number;
    }>('/pricing-plans');

    return {
      data: response.data.map((plan) => ({
        id: plan.id,
        name: plan.name,
        meter: plan.unitType,
        unitPriceUsd: plan.unitPriceUsd,
        isActive: plan.isActive,
      })),
      count: response.count,
    };
  }

  // ==========================================================================
  // Pricing Plan Management Methods
  // ==========================================================================

  /**
   * Creates a new pricing plan for a usage type.
   *
   * Each `unitType` can only have one active plan. If you need to change prices,
   * use `updatePricingPlan()` (which creates a new version) or deactivate the
   * existing plan first.
   *
   * @param params - Pricing plan creation parameters
   * @returns The created pricing plan
   *
   * @example
   * ```typescript
   * // Simple flat-rate plan
   * const plan = await drip.createPricingPlan({
   *   name: 'API Calls',
   *   unitType: 'api_call',
   *   unitPriceUsd: 0.001,
   * });
   *
   * // Tiered pricing plan
   * const tiered = await drip.createPricingPlan({
   *   name: 'Token Usage',
   *   unitType: 'token',
   *   unitPriceUsd: 0.0001,
   *   pricingModel: 'TIERED',
   *   tiers: [
   *     { minQuantity: 0, maxQuantity: 10000, unitPriceUsd: 0.0001 },
   *     { minQuantity: 10000, maxQuantity: 100000, unitPriceUsd: 0.00008 },
   *     { minQuantity: 100000, maxQuantity: null, unitPriceUsd: 0.00005 },
   *   ],
   * });
   * ```
   */
  async createPricingPlan(
    params: CreatePricingPlanParams,
  ): Promise<PricingPlan> {
    this.assertSecretKey('createPricingPlan()');
    return this.request<PricingPlan>('/pricing-plans', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Retrieves a pricing plan by ID.
   *
   * @param planId - The pricing plan ID
   * @returns The pricing plan with tiers
   *
   * @example
   * ```typescript
   * const plan = await drip.getPricingPlan('plan_abc123');
   * console.log(`${plan.name}: $${plan.unitPriceUsd}/${plan.unitType}`);
   * console.log(`Model: ${plan.pricingModel}, Tiers: ${plan.tiers.length}`);
   * ```
   */
  async getPricingPlan(planId: string): Promise<PricingPlan> {
    this.assertSecretKey('getPricingPlan()');
    return this.request<PricingPlan>(`/pricing-plans/${planId}`);
  }

  /**
   * Lists all pricing plans for your business, including tiers.
   *
   * Unlike `listMeters()` (which returns a simplified view), this returns
   * full plan details including pricing model, tiers, and timestamps.
   *
   * @returns List of pricing plans with full details
   *
   * @example
   * ```typescript
   * const { data: plans } = await drip.listPricingPlans();
   *
   * for (const plan of plans) {
   *   console.log(`${plan.name} (${plan.unitType}): $${plan.unitPriceUsd}/unit [${plan.pricingModel}]`);
   *   if (plan.tiers.length > 0) {
   *     plan.tiers.forEach(t =>
   *       console.log(`  ${t.minQuantity}–${t.maxQuantity ?? '∞'}: $${t.unitPriceUsd}`)
   *     );
   *   }
   * }
   * ```
   */
  async listPricingPlans(): Promise<ListPricingPlansResponse> {
    this.assertSecretKey('listPricingPlans()');
    return this.request<ListPricingPlansResponse>('/pricing-plans');
  }

  /**
   * Updates a pricing plan.
   *
   * - **Metadata changes** (name, isActive) update the plan in place.
   * - **Price changes** (unitPriceUsd, pricingModel, tiers) create a new version,
   *   preserving billing history for existing charges.
   *
   * @param planId - The pricing plan ID
   * @param params - Fields to update
   * @returns The updated (or new version) pricing plan
   *
   * @example
   * ```typescript
   * // Rename a plan (metadata-only, no versioning)
   * await drip.updatePricingPlan('plan_abc123', { name: 'Premium API Calls' });
   *
   * // Change price (creates a new version for billing history)
   * const updated = await drip.updatePricingPlan('plan_abc123', {
   *   unitPriceUsd: 0.002,
   * });
   *
   * // Switch to tiered pricing
   * await drip.updatePricingPlan('plan_abc123', {
   *   pricingModel: 'TIERED',
   *   tiers: [
   *     { minQuantity: 0, maxQuantity: 1000, unitPriceUsd: 0.001 },
   *     { minQuantity: 1000, maxQuantity: null, unitPriceUsd: 0.0005 },
   *   ],
   * });
   *
   * // Deactivate a plan (stops new charges, preserves history)
   * await drip.updatePricingPlan('plan_abc123', { isActive: false });
   * ```
   */
  async updatePricingPlan(
    planId: string,
    params: UpdatePricingPlanParams,
  ): Promise<PricingPlan> {
    this.assertSecretKey('updatePricingPlan()');
    return this.request<PricingPlan>(`/pricing-plans/${planId}`, {
      method: 'PATCH',
      body: JSON.stringify(params),
    });
  }

  /**
   * Soft-deletes a pricing plan by deactivating it.
   *
   * The plan is preserved for historical charges but will no longer be used
   * for new usage events. You can reactivate it via `updatePricingPlan()`.
   *
   * @param planId - The pricing plan ID
   *
   * @example
   * ```typescript
   * await drip.deletePricingPlan('plan_abc123');
   *
   * // Reactivate later if needed:
   * await drip.updatePricingPlan('plan_abc123', { isActive: true });
   * ```
   */
  async deletePricingPlan(planId: string): Promise<void> {
    this.assertSecretKey('deletePricingPlan()');
    await this.request<void>(`/pricing-plans/${planId}`, {
      method: 'DELETE',
    });
  }

  /**
   * Looks up the active pricing plan for a specific usage type.
   *
   * Convenience method for finding the plan that would apply to a given
   * meter/usage type without listing all plans.
   *
   * @param unitType - The usage type (e.g., "api_call", "token")
   * @returns The active pricing plan for this unit type
   *
   * @example
   * ```typescript
   * const plan = await drip.getPricingPlanByType('api_call');
   * console.log(`API calls cost $${plan.unitPriceUsd} each`);
   * ```
   */
  async getPricingPlanByType(unitType: string): Promise<PricingPlan> {
    this.assertSecretKey('getPricingPlanByType()');
    return this.request<PricingPlan>(
      `/pricing-plans/by-type/${encodeURIComponent(unitType)}`,
    );
  }

  // ==========================================================================
  // Cost Estimation Methods
  // ==========================================================================

  /**
   * Estimates costs from historical usage events.
   *
   * Use this to preview what existing usage would cost before creating charges,
   * or to run "what-if" scenarios with custom pricing.
   *
   * @param params - Parameters for the estimate
   * @returns Cost estimate with line item breakdown
   *
   * @example
   * ```typescript
   * // Estimate costs for last month's usage
   * const estimate = await drip.estimateFromUsage({
   *   periodStart: new Date('2024-01-01'),
   *   periodEnd: new Date('2024-01-31'),
   * });
   *
   * console.log(`Estimated total: $${estimate.estimatedTotalUsdc}`);
   * ```
   *
   * @example
   * ```typescript
   * // "What-if" scenario with custom pricing
   * const estimate = await drip.estimateFromUsage({
   *   periodStart: new Date('2024-01-01'),
   *   periodEnd: new Date('2024-01-31'),
   *   customPricing: {
   *     'api_call': '0.005',  // What if we charged $0.005 per call?
   *     'token': '0.0001',    // What if we charged $0.0001 per token?
   *   },
   * });
   * ```
   */
  async estimateFromUsage(params: EstimateFromUsageParams): Promise<CostEstimateResponse> {
    const periodStart = params.periodStart instanceof Date
      ? params.periodStart.toISOString()
      : params.periodStart;
    const periodEnd = params.periodEnd instanceof Date
      ? params.periodEnd.toISOString()
      : params.periodEnd;

    return this.request<CostEstimateResponse>('/cost-estimate/from-usage', {
      method: 'POST',
      body: JSON.stringify({
        customerId: params.customerId,
        periodStart,
        periodEnd,
        defaultUnitPrice: params.defaultUnitPrice,
        includeChargedEvents: params.includeChargedEvents,
        usageTypes: params.usageTypes,
        customPricing: params.customPricing,
      }),
    });
  }

  /**
   * Estimates costs from hypothetical usage.
   *
   * Use this for "what-if" scenarios, budget planning, or to preview
   * costs before usage occurs.
   *
   * @param params - Parameters for the estimate
   * @returns Cost estimate with line item breakdown
   *
   * @example
   * ```typescript
   * // Estimate what 10,000 API calls and 1M tokens would cost
   * const estimate = await drip.estimateFromHypothetical({
   *   items: [
   *     { usageType: 'api_call', quantity: 10000 },
   *     { usageType: 'token', quantity: 1000000 },
   *   ],
   * });
   *
   * console.log(`Estimated total: $${estimate.estimatedTotalUsdc}`);
   * for (const item of estimate.lineItems) {
   *   console.log(`  ${item.usageType}: ${item.quantity} × $${item.unitPrice} = $${item.estimatedCostUsdc}`);
   * }
   * ```
   *
   * @example
   * ```typescript
   * // Compare different pricing scenarios
   * const currentPricing = await drip.estimateFromHypothetical({
   *   items: [{ usageType: 'api_call', quantity: 100000 }],
   * });
   *
   * const newPricing = await drip.estimateFromHypothetical({
   *   items: [{ usageType: 'api_call', quantity: 100000 }],
   *   customPricing: { 'api_call': '0.0005' },  // 50% discount
   * });
   *
   * console.log(`Current: $${currentPricing.estimatedTotalUsdc}`);
   * console.log(`With 50% discount: $${newPricing.estimatedTotalUsdc}`);
   * ```
   */
  async estimateFromHypothetical(params: EstimateFromHypotheticalParams): Promise<CostEstimateResponse> {
    return this.request<CostEstimateResponse>('/cost-estimate/hypothetical', {
      method: 'POST',
      body: JSON.stringify({
        items: params.items,
        defaultUnitPrice: params.defaultUnitPrice,
        customPricing: params.customPricing,
      }),
    });
  }

  /**
   * Records a complete agent run in a single call.
   *
   * This is the **simplified API** that combines:
   * - Workflow creation (if needed)
   * - Run creation
   * - Event emission
   * - Run completion
   *
   * Use this instead of the individual `startRun()`, `emitEvent()`, `endRun()` calls
   * when you have all the run data available at once.
   *
   * @param params - Run parameters including events
   * @returns The created run with event summary
   *
   * @example
   * ```typescript
   * // Record a complete agent run in one call
   * const result = await drip.recordRun({
   *   customerId: 'cust_123',
   *   workflow: 'doc_processing',  // Auto-creates if doesn't exist
   *   events: [
   *     { eventType: 'agent.start', description: 'Started processing' },
   *     { eventType: 'tool.ocr', quantity: 3, units: 'pages', costUnits: 0.15 },
   *     { eventType: 'tool.validate', quantity: 1, costUnits: 0.05 },
   *     { eventType: 'agent.complete', description: 'Finished successfully' },
   *   ],
   *   status: 'COMPLETED',
   * });
   *
   * console.log(`Run ${result.run.id}: ${result.summary}`);
   * console.log(`Events: ${result.events.created} created`);
   * ```
   *
   * @example
   * ```typescript
   * // Record a failed run with error details
   * const result = await drip.recordRun({
   *   customerId: 'cust_123',
   *   workflow: 'doc_processing',
   *   events: [
   *     { eventType: 'agent.start', description: 'Started processing' },
   *     { eventType: 'tool.ocr', quantity: 1, units: 'pages' },
   *     { eventType: 'error', description: 'OCR failed: image too blurry' },
   *   ],
   *   status: 'FAILED',
   *   errorMessage: 'OCR processing failed',
   *   errorCode: 'OCR_QUALITY_ERROR',
   * });
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
        status: endResult.status,
        durationMs: endResult.durationMs ?? durationMs,
      },
      events: { created: eventsCreated, duplicates: eventsDuplicates },
      totalCostUnits: endResult.totalCostUnits ?? null,
      summary: `${statusIcon} ${workflowName}: ${eventsCreated} events recorded (${endResult.durationMs ?? durationMs}ms)`,
    };
  }

  /**
   * Generates a deterministic idempotency key.
   *
   * Use this to ensure "one logical action = one event" even with retries.
   * The key is generated from customerId + runId + stepName + sequence.
   *
   * @param params - Key generation parameters
   * @returns A deterministic idempotency key
   *
   * @example
   * ```typescript
   * const key = Drip.generateIdempotencyKey({
   *   customerId: 'cust_123',
   *   runId: 'run_456',
   *   stepName: 'validate_prescription',
   *   sequence: 1,
   * });
   *
   * await drip.emitEvent({
   *   runId: 'run_456',
   *   eventType: 'agent.validate',
   *   idempotencyKey: key,
   * });
   * ```
   */
  static generateIdempotencyKey(params: {
    customerId: string;
    runId?: string;
    stepName: string;
    sequence?: number;
  }): string {
    const components = [
      params.customerId,
      params.runId ?? 'no_run',
      params.stepName,
      String(params.sequence ?? 0),
    ];

    const str = components.join('|');
    const hash = createHash('sha256').update(str).digest('hex').slice(0, 32);

    return `drip_${hash}_${params.stepName.slice(0, 16)}`;
  }

  // ==========================================================================
  // Utility Methods
  // ==========================================================================

  /**
   * Verifies a webhook signature using HMAC-SHA256.
   *
   * Call this when receiving webhook events to ensure they're authentic.
   * This is an async method that uses the Web Crypto API for secure verification.
   *
   * @param payload - The raw request body (string)
   * @param signature - The x-drip-signature header value
   * @param secret - Your webhook secret
   * @returns Promise resolving to whether the signature is valid
   *
   * @example
   * ```typescript
   * app.post('/webhooks/drip', async (req, res) => {
   *   const isValid = await Drip.verifyWebhookSignature(
   *     req.rawBody,
   *     req.headers['x-drip-signature'],
   *     process.env.DRIP_WEBHOOK_SECRET!,
   *   );
   *
   *   if (!isValid) {
   *     return res.status(401).send('Invalid signature');
   *   }
   *
   *   // Process the webhook...
   * });
   * ```
   */
  static async verifyWebhookSignature(
    payload: string,
    signature: string,
    secret: string,
    tolerance = 300, // 5 minutes default
  ): Promise<boolean> {
    if (!payload || !signature || !secret) {
      return false;
    }

    try {
      // Parse signature format: t=timestamp,v1=hexsignature
      const parts = signature.split(',');
      const timestampPart = parts.find((p) => p.startsWith('t='));
      const signaturePart = parts.find((p) => p.startsWith('v1='));

      if (!timestampPart || !signaturePart) {
        return false;
      }

      const timestamp = parseInt(timestampPart.slice(2), 10);
      const providedSignature = signaturePart.slice(3);

      if (isNaN(timestamp)) {
        return false;
      }

      // Check timestamp tolerance
      const now = Math.floor(Date.now() / 1000);
      if (Math.abs(now - timestamp) > tolerance) {
        return false;
      }

      // Compute expected signature using timestamp.payload format
      const signaturePayload = `${timestamp}.${payload}`;
      const encoder = new TextEncoder();
      const keyData = encoder.encode(secret);
      const payloadData = encoder.encode(signaturePayload);

      // Get the subtle crypto API - use globalThis.crypto for browsers/edge runtimes,
      // or fall back to Node.js webcrypto for Node.js 18+
      const subtle = globalThis.crypto?.subtle ?? webcrypto.subtle;

      // Import the secret as an HMAC key
      const cryptoKey = await subtle.importKey(
        'raw',
        keyData,
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign'],
      );

      // Sign the payload
      const signatureBuffer = await subtle.sign(
        'HMAC',
        cryptoKey,
        payloadData,
      );

      // Convert to hex string
      const expectedSignature = Array.from(new Uint8Array(signatureBuffer))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');

      // Constant-time comparison to prevent timing attacks
      if (providedSignature.length !== expectedSignature.length) {
        return false;
      }

      let result = 0;
      for (let i = 0; i < providedSignature.length; i++) {
        result |= providedSignature.charCodeAt(i) ^ expectedSignature.charCodeAt(i);
      }

      return result === 0;
    } catch {
      return false;
    }
  }

  /**
   * Synchronously verifies a webhook signature using HMAC-SHA256.
   *
   * This method uses Node.js crypto module and is only available in Node.js environments.
   * For edge runtimes or browsers, use the async `verifyWebhookSignature` method instead.
   *
   * @param payload - The raw request body (string)
   * @param signature - The x-drip-signature header value
   * @param secret - Your webhook secret
   * @returns Whether the signature is valid
   *
   * @example
   * ```typescript
   * app.post('/webhooks/drip', (req, res) => {
   *   const isValid = Drip.verifyWebhookSignatureSync(
   *     req.rawBody,
   *     req.headers['x-drip-signature'],
   *     process.env.DRIP_WEBHOOK_SECRET!,
   *   );
   *
   *   if (!isValid) {
   *     return res.status(401).send('Invalid signature');
   *   }
   *
   *   // Process the webhook...
   * });
   * ```
   */
  static verifyWebhookSignatureSync(
    payload: string,
    signature: string,
    secret: string,
    tolerance = 300, // 5 minutes default
  ): boolean {
    if (!payload || !signature || !secret) {
      return false;
    }

    try {
      // Parse signature format: t=timestamp,v1=hexsignature
      const parts = signature.split(',');
      const timestampPart = parts.find((p) => p.startsWith('t='));
      const signaturePart = parts.find((p) => p.startsWith('v1='));

      if (!timestampPart || !signaturePart) {
        return false;
      }

      const timestamp = parseInt(timestampPart.slice(2), 10);
      const providedSignature = signaturePart.slice(3);

      if (isNaN(timestamp)) {
        return false;
      }

      // Check timestamp tolerance
      const now = Math.floor(Date.now() / 1000);
      if (Math.abs(now - timestamp) > tolerance) {
        return false;
      }

      // Compute expected signature using timestamp.payload format
      const signaturePayload = `${timestamp}.${payload}`;
      const expectedSignature = createHmac('sha256', secret)
        .update(signaturePayload)
        .digest('hex');

      // Use timingSafeEqual for constant-time comparison
      const sigBuffer = Buffer.from(providedSignature);
      const expectedBuffer = Buffer.from(expectedSignature);

      if (sigBuffer.length !== expectedBuffer.length) {
        return false;
      }

      return timingSafeEqual(sigBuffer, expectedBuffer);
    } catch {
      return false;
    }
  }

  /**
   * Generates a webhook signature for testing purposes.
   *
   * This method creates a signature in the same format the Drip backend uses,
   * allowing you to test your webhook handling code locally.
   *
   * @param payload - The webhook payload (JSON string)
   * @param secret - The webhook secret
   * @param timestamp - Optional timestamp (defaults to current time)
   * @returns Signature in format: t=timestamp,v1=hexsignature
   *
   * @example
   * ```typescript
   * const payload = JSON.stringify({ type: 'charge.succeeded', data: {...} });
   * const signature = Drip.generateWebhookSignature(payload, 'whsec_test123');
   *
   * // Use in tests:
   * const isValid = Drip.verifyWebhookSignatureSync(payload, signature, 'whsec_test123');
   * console.log(isValid); // true
   * ```
   */
  static generateWebhookSignature(
    payload: string,
    secret: string,
    timestamp?: number,
  ): string {
    const ts = timestamp ?? Math.floor(Date.now() / 1000);
    const signaturePayload = `${ts}.${payload}`;
    const signature = createHmac('sha256', secret)
      .update(signaturePayload)
      .digest('hex');

    return `t=${ts},v1=${signature}`;
  }

  // ==========================================================================
  // StreamMeter Factory
  // ==========================================================================

  /**
   * Creates a StreamMeter for accumulating usage and charging once.
   *
   * Perfect for LLM token streaming where you want to:
   * - Accumulate tokens locally (no API call per token)
   * - Charge once at the end of the stream
   * - Handle partial failures (charge for what was delivered)
   *
   * @param options - StreamMeter configuration
   * @returns A new StreamMeter instance
   *
   * @example
   * ```typescript
   * const meter = drip.createStreamMeter({
   *   customerId: 'cust_abc123',
   *   meter: 'tokens',
   * });
   *
   * // Accumulate tokens as they stream
   * for await (const chunk of llmStream) {
   *   meter.addSync(chunk.tokens);
   *   yield chunk;
   * }
   *
   * // Single charge at end
   * const result = await meter.flush();
   * console.log(`Charged ${result.charge?.amountUsdc} for ${result.quantity} tokens`);
   * ```
   *
   * @example
   * ```typescript
   * // With auto-flush threshold
   * const meter = drip.createStreamMeter({
   *   customerId: 'cust_abc123',
   *   meter: 'tokens',
   *   flushThreshold: 10000, // Charge every 10k tokens
   * });
   *
   * for await (const chunk of longStream) {
   *   await meter.add(chunk.tokens); // May auto-flush
   * }
   *
   * await meter.flush(); // Final flush for remaining tokens
   * ```
   */
  createStreamMeter(options: StreamMeterOptions): StreamMeter {
    // StreamMeter previously used charge(); it now dispatches through
    // trackUsage() which hits the same `/usage` billing endpoint.
    return new StreamMeter(
      (params) =>
        this.trackUsage({
          customerId: params.customerId,
          externalCustomerId: params.externalCustomerId,
          meter: params.meter,
          quantity: params.quantity,
          idempotencyKey: params.idempotencyKey,
          metadata: params.metadata,
        }) as unknown as Promise<ChargeResult>,
      options,
    );
  }

  // ==========================================================================
  // Portal Session Methods
  // ==========================================================================

  /**
   * Creates a portal session for a customer.
   *
   * Portal sessions generate a token that lets your customer access a
   * read-only dashboard showing their balance, charges, session keys,
   * and settlement history — without needing a wallet connection.
   *
   * Send the returned `url` to your customer (email, in-app link, etc.).
   *
   * Requires a secret key (sk_).
   *
   * @param params - Portal session configuration
   * @returns The created session with token and URL
   *
   * @example
   * ```typescript
   * const session = await drip.createPortalSession({
   *   customerId: 'cust_abc123',
   *   expiresInMinutes: 120, // 2 hours (default: 60)
   * });
   *
   * // Send this URL to your customer
   * console.log(session.url); // "/portal/abc..."
   *
   * // Or construct a full URL
   * const fullUrl = `https://app.drippay.dev${session.url}`;
   * ```
   */
  async createPortalSession(
    params: CreatePortalSessionParams,
  ): Promise<PortalSession> {
    this.assertSecretKey('createPortalSession()');
    return this.request<PortalSession>('/portal-sessions', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  /**
   * Revokes a portal session, immediately invalidating the token.
   *
   * Use this when a customer should no longer have portal access
   * (e.g. account deactivated, token compromised).
   *
   * Requires a secret key (sk_).
   *
   * @param sessionId - The portal session ID to revoke
   *
   * @example
   * ```typescript
   * await drip.revokePortalSession('ps_abc123');
   * // Token is now invalid — customer sees "link expired" error
   * ```
   */
  async revokePortalSession(sessionId: string): Promise<{ success: boolean }> {
    this.assertSecretKey('revokePortalSession()');
    return this.request<{ success: boolean }>(`/portal-sessions/${sessionId}`, {
      method: 'DELETE',
    });
  }

  // ==========================================================================
  // Payload Mapping Engine — REMOVED
  // ==========================================================================
  //
  // The payload mapping routes (`/payload-mappings`, `/ingest/:slug`) are
  // not implemented on the backend. The previous SDK methods returned 404
  // Not Found at runtime, which was worse than not exposing them at all.
  // The interfaces (`PayloadMapping`, `CreatePayloadMappingParams`, etc.)
  // are still exported from this file for a future revival of the feature.
}

// Re-export StreamMeter types
export { StreamMeter } from './stream-meter.js';
export type { StreamMeterOptions, StreamMeterFlushResult } from './stream-meter.js';

// Re-export Resilience types and utilities
export {
  ResilienceManager,
  RateLimiter,
  CircuitBreaker,
  MetricsCollector,
  RetryExhaustedError,
  CircuitBreakerOpenError,
  createDefaultResilienceConfig,
  createDisabledResilienceConfig,
  createHighThroughputResilienceConfig,
  calculateBackoff,
  isRetryableError,
} from './resilience.js';

export type {
  ResilienceConfig,
  ResilienceHealth,
  RateLimiterConfig,
  RetryConfig,
  CircuitBreakerConfig,
  CircuitState,
  RequestMetrics,
  MetricsSummary,
} from './resilience.js';

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
 *
 * // One line to charge
 * await drip.charge({ customerId: 'cust_123', meter: 'api_calls', quantity: 1 });
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
 * // Charge with one line
 * await drip.charge({ customerId: 'cust_123', meter: 'api_calls', quantity: 1 });
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
