/**
 * Drip Middleware Types
 *
 * Shared type definitions for the withDrip wrapper and framework-specific adapters.
 * These types ensure consistent behavior across Next.js, Express, and other frameworks.
 */

import type { Drip, Customer, ChargeResult, DripError } from '../index.js';

// ============================================================================
// Configuration Types
// ============================================================================

/**
 * Configuration for the withDrip middleware.
 */
export interface WithDripConfig<TRequest = unknown> {
  /**
   * The usage meter to charge against.
   * Must match a meter configured in your Drip pricing plan.
   * @example "api_calls", "compute_seconds", "tokens"
   */
  meter: string;

  /**
   * The quantity to charge. Can be a static number or a function
   * that calculates quantity based on the request.
   * @example 1
   * @example (req) => req.body.tokens.length
   */
  quantity: number | ((request: TRequest) => number | Promise<number>);

  /**
   * API key for Drip. Defaults to DRIP_API_KEY environment variable.
   */
  apiKey?: string;

  /**
   * Base URL for Drip API. Defaults to DRIP_API_URL or production.
   */
  baseUrl?: string;

  /**
   * A function that resolves the customer ID from a verified authentication
   * source (e.g., session token, JWT). Must be explicitly provided.
   * Trusting unauthenticated client headers or query parameters is insecure.
   */
  customerResolver: (request: TRequest) => string | Promise<string>;

  /**
   * Custom idempotency key generator.
   * By default, generates from request method, path, and customer ID.
   */
  idempotencyKey?: (request: TRequest) => string | Promise<string>;

  /**
   * Custom error handler for Drip errors.
   * Return a response to override default error handling.
   */
  onError?: (error: DripError, request: TRequest) => unknown | Promise<unknown>;

  /**
   * Called after successful charge. Useful for logging/metrics.
   */
  onCharge?: (charge: ChargeResult, request: TRequest) => void | Promise<void>;

  /**
   * Whether to skip charging in development/test environments.
   * @default false
   */
  skipInDevelopment?: boolean;

  /**
   * Custom metadata to attach to each charge.
   */
  metadata?: Record<string, unknown> | ((request: TRequest) => Record<string, unknown>);

  /**
   * Optional allowlist of metadata keys permitted to be sent.
   * If provided, keys not in the list are dropped.
   */
  metadataAllowlist?: string[];

  /**
   * Optional explicit metadata keys to redact before sending.
   * Matching is case-insensitive.
   */
  redactMetadataKeys?: string[];
}

// ============================================================================
// Context Types
// ============================================================================

/**
 * Context passed to the wrapped handler after payment verification.
 */
export interface DripContext {
  /**
   * The Drip SDK client instance.
   */
  drip: Drip;

  /**
   * The resolved customer ID.
   */
  customerId: string;

  /**
   * The charge result from this request.
   */
  charge: ChargeResult;

  /**
   * Whether this was a replayed request (idempotency key matched).
   */
  isDuplicate: boolean;
}

// ============================================================================
// x402 Payment Types
// ============================================================================

/**
 * x402 payment proof extracted from request headers.
 */
export interface X402PaymentProof {
  signature: string;
  sessionKeyId: string;
  smartAccount: string;
  timestamp: number;
  amount: string;
  recipient: string;
  usageId: string;
  nonce: string;
}

/**
 * x402 payment request returned in 402 responses.
 */
export interface X402PaymentRequest {
  amount: string;
  recipient: string;
  usageId: string;
  description: string;
  expiresAt: number;
  nonce: string;
  timestamp: number;
}

/**
 * Headers to include in 402 Payment Required responses.
 */
export type X402ResponseHeaders = {
  'X-Payment-Required': 'true';
  'X-Payment-Amount': string;
  'X-Payment-Recipient': string;
  'X-Payment-Usage-Id': string;
  'X-Payment-Description': string;
  'X-Payment-Expires': string;
  'X-Payment-Nonce': string;
  'X-Payment-Timestamp': string;
} & Record<string, string>;

// ============================================================================
// Result Types
// ============================================================================

/**
 * Result of customer resolution.
 */
export interface CustomerResolutionResult {
  success: boolean;
  customerId?: string;
  error?: string;
}

/**
 * Result of balance check.
 */
export interface BalanceCheckResult {
  sufficient: boolean;
  balance?: string;
  required?: string;
  shortfall?: string;
}

/**
 * Internal state for middleware processing.
 */
export interface MiddlewareState {
  customerId: string;
  quantity: number;
  idempotencyKey: string;
  hasPaymentProof: boolean;
  paymentProof?: X402PaymentProof;
}

// ============================================================================
// Error Types
// ============================================================================

/**
 * Error codes specific to the withDrip middleware.
 */
export type DripMiddlewareErrorCode =
  | 'CUSTOMER_NOT_FOUND'
  | 'CUSTOMER_RESOLUTION_FAILED'
  | 'PAYMENT_REQUIRED'
  | 'PAYMENT_VERIFICATION_FAILED'
  | 'CHARGE_FAILED'
  | 'CONFIGURATION_ERROR'
  | 'INTERNAL_ERROR';

/**
 * Error thrown by the withDrip middleware.
 */
export class DripMiddlewareError extends Error {
  constructor(
    message: string,
    public readonly code: DripMiddlewareErrorCode,
    public readonly statusCode: number,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'DripMiddlewareError';
    Object.setPrototypeOf(this, DripMiddlewareError.prototype);
  }
}

// ============================================================================
// Framework Adapter Types
// ============================================================================

/**
 * Generic request interface that frameworks must implement.
 */
export interface GenericRequest {
  method: string;
  url: string;
  headers: Record<string, string | string[] | undefined>;
  query?: Record<string, string | string[] | undefined>;
  body?: unknown;
}

/**
 * Generic response builder for framework adapters.
 */
export interface ResponseBuilder {
  status(code: number): this;
  header(name: string, value: string): this;
  json(body: unknown): unknown;
}
