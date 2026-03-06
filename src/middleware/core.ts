/**
 * Drip Middleware Core
 *
 * Framework-agnostic core logic for the withDrip wrapper.
 * This module handles customer resolution, balance checks, charging,
 * and x402 payment flow orchestration.
 */

import { createHash as cryptoCreateHash, randomBytes } from 'crypto';
import { Drip, DripError, type ChargeResult } from '../index.js';
import type {
  WithDripConfig,
  X402PaymentProof,
  X402PaymentRequest,
  X402ResponseHeaders,
  MiddlewareState,
  GenericRequest,
} from './types.js';
import { DripMiddlewareError } from './types.js';

// ============================================================================
// Constants
// ============================================================================

const DEFAULT_PAYMENT_EXPIRY_SEC = 5 * 60; // 5 minutes
const MAX_TIMESTAMP_AGE_SEC = 5 * 60; // 5 minutes max age for payment proof timestamps
const DEFAULT_METADATA_MAX_STRING_LENGTH = 256;

const SENSITIVE_METADATA_KEY_PATTERN =
  /(authorization|api[_-]?key|secret|password|token|prompt|completion|output|input|request|response|body|cookie|set-cookie|email|phone|ssn|address|creditcard|card)/i;

const DEFAULT_REDACT_METADATA_KEYS = [
  'authorization',
  'apiKey',
  'api_key',
  'secret',
  'password',
  'token',
  'prompt',
  'prompts',
  'input',
  'inputs',
  'output',
  'outputs',
  'request',
  'response',
  'body',
  'query',
] as const;

// Required headers for x402 payment proof
const REQUIRED_PAYMENT_HEADERS = [
  'x-payment-signature',
  'x-payment-session-key',
  'x-payment-smart-account',
  'x-payment-timestamp',
  'x-payment-amount',
  'x-payment-recipient',
  'x-payment-usage-id',
  'x-payment-nonce',
] as const;

// ============================================================================
// Header Utilities
// ============================================================================

/**
 * Normalize header name to lowercase.
 */
function normalizeHeaderName(name: string): string {
  return name.toLowerCase();
}

/**
 * Get a header value from a request, handling case-insensitivity.
 */
export function getHeader(
  headers: Record<string, string | string[] | undefined>,
  name: string,
): string | undefined {
  const normalized = normalizeHeaderName(name);

  // Try exact match first
  if (headers[normalized] !== undefined) {
    const value = headers[normalized];
    return Array.isArray(value) ? value[0] : value;
  }

  // Fall back to case-insensitive search
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === normalized) {
      return Array.isArray(value) ? value[0] : value;
    }
  }

  return undefined;
}

// ============================================================================
// x402 Payment Proof Parsing
// ============================================================================

/**
 * Check if a request contains x402 payment proof headers.
 */
export function hasPaymentProof(
  headers: Record<string, string | string[] | undefined>,
): boolean {
  return REQUIRED_PAYMENT_HEADERS.every(
    (header) => getHeader(headers, header) !== undefined,
  );
}

/**
 * Parse x402 payment proof from request headers.
 * Returns null if headers are missing or invalid.
 */
export function parsePaymentProof(
  headers: Record<string, string | string[] | undefined>,
): X402PaymentProof | null {
  const signature = getHeader(headers, 'x-payment-signature');
  const sessionKeyId = getHeader(headers, 'x-payment-session-key');
  const smartAccount = getHeader(headers, 'x-payment-smart-account');
  const timestampStr = getHeader(headers, 'x-payment-timestamp');
  const amount = getHeader(headers, 'x-payment-amount');
  const recipient = getHeader(headers, 'x-payment-recipient');
  const usageId = getHeader(headers, 'x-payment-usage-id');
  const nonce = getHeader(headers, 'x-payment-nonce');

  // All headers required
  if (
    !signature ||
    !sessionKeyId ||
    !smartAccount ||
    !timestampStr ||
    !amount ||
    !recipient ||
    !usageId ||
    !nonce
  ) {
    return null;
  }

  const timestamp = parseInt(timestampStr, 10);
  if (isNaN(timestamp)) {
    return null;
  }

  // Validate timestamp freshness (prevent replay attacks)
  const now = Math.floor(Date.now() / 1000);
  if (now - timestamp > MAX_TIMESTAMP_AGE_SEC) {
    return null; // Timestamp too old
  }

  // Validate hex format for blockchain addresses/signatures
  // Must start with 0x and contain only valid hex characters
  const isValidHex = (value: string, minLength: number): boolean => {
    if (!value.startsWith('0x')) return false;
    const hex = value.slice(2);
    if (hex.length < minLength) return false;
    return /^[a-fA-F0-9]+$/.test(hex);
  };

  // Signature must be at least 65 bytes (130 hex chars) for ECDSA
  // Session key and smart account are 32 and 20 bytes respectively
  if (
    !isValidHex(signature, 130) ||
    !isValidHex(sessionKeyId, 64) ||
    !isValidHex(smartAccount, 40)
  ) {
    return null;
  }

  return {
    signature,
    sessionKeyId,
    smartAccount,
    timestamp,
    amount,
    recipient,
    usageId,
    nonce,
  };
}

// ============================================================================
// x402 Response Generation
// ============================================================================

/**
 * Generate x402 payment request headers for 402 responses.
 */
export function generatePaymentRequest(params: {
  amount: string;
  recipient: string;
  usageId: string;
  description?: string;
  expiresInSec?: number;
}): {
  headers: X402ResponseHeaders;
  paymentRequest: X402PaymentRequest;
} {
  const now = Math.floor(Date.now() / 1000);
  const expiresAt = now + (params.expiresInSec ?? DEFAULT_PAYMENT_EXPIRY_SEC);
  // Use cryptographically secure random bytes for nonce generation
  const nonce = `${now}-${randomBytes(16).toString('hex')}`;

  // Ensure usageId is properly formatted
  let usageId = params.usageId;
  if (!usageId.startsWith('0x')) {
    // Hash the string to get a bytes32
    usageId = hashString(usageId);
  }

  const headers: X402ResponseHeaders = {
    'X-Payment-Required': 'true',
    'X-Payment-Amount': params.amount,
    'X-Payment-Recipient': params.recipient,
    'X-Payment-Usage-Id': usageId,
    'X-Payment-Description': params.description ?? 'API usage charge',
    'X-Payment-Expires': String(expiresAt),
    'X-Payment-Nonce': nonce,
    'X-Payment-Timestamp': String(now),
  };

  const paymentRequest: X402PaymentRequest = {
    amount: params.amount,
    recipient: params.recipient,
    usageId,
    description: params.description ?? 'API usage charge',
    expiresAt,
    nonce,
    timestamp: now,
  };

  return { headers, paymentRequest };
}

/**
 * Hash a string into a bytes32 hex value using SHA-256.
 * In production, the server will use keccak256.
 */
function hashString(input: string): string {
  const hash = cryptoCreateHash('sha256').update(input).digest('hex');
  return `0x${hash}`;
}

function isMetadataPrimitive(value: unknown): value is string | number | boolean | null {
  if (value === null) {
    return true;
  }
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

function sanitizeChargeMetadata<TRequest>(
  metadata: Record<string, unknown> | undefined,
  config: Pick<WithDripConfig<TRequest>, 'metadataAllowlist' | 'redactMetadataKeys'>,
): Record<string, unknown> {
  if (!metadata) {
    return {};
  }

  const allowlist = config.metadataAllowlist
    ? new Set([...config.metadataAllowlist, ...config.metadataAllowlist.map((k) => k.toLowerCase())])
    : null;

  const redactKeys = config.redactMetadataKeys
    ? new Set([...config.redactMetadataKeys, ...config.redactMetadataKeys.map((k) => k.toLowerCase())])
    : new Set([...DEFAULT_REDACT_METADATA_KEYS, ...DEFAULT_REDACT_METADATA_KEYS.map((k) => k.toLowerCase())]);

  const sanitized: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(metadata)) {
    const normalizedKey = key.toLowerCase();

    if (allowlist && !allowlist.has(key) && !allowlist.has(normalizedKey)) {
      continue;
    }
    if (redactKeys.has(key) || redactKeys.has(normalizedKey)) {
      continue;
    }
    if (SENSITIVE_METADATA_KEY_PATTERN.test(key)) {
      continue;
    }
    if (!isMetadataPrimitive(value)) {
      continue;
    }
    if (typeof value === 'number' && !Number.isFinite(value)) {
      continue;
    }
    if (typeof value === 'string' && value.length > DEFAULT_METADATA_MAX_STRING_LENGTH) {
      sanitized[key] = value.slice(0, DEFAULT_METADATA_MAX_STRING_LENGTH);
      continue;
    }

    sanitized[key] = value;
  }

  return sanitized;
}

// ============================================================================
// Customer Resolution
// ============================================================================

/**
 * Resolve customer ID from a request based on configuration.
 */
export async function resolveCustomerId<TRequest extends GenericRequest>(
  request: TRequest,
  config: WithDripConfig<TRequest>,
): Promise<string> {
  const resolver = config.customerResolver ?? 'header';

  if (typeof resolver === 'function') {
    return resolver(request);
  }

  if (resolver === 'header') {
    const customerId =
      getHeader(request.headers, 'x-drip-customer-id') ??
      getHeader(request.headers, 'x-customer-id');

    if (!customerId) {
      throw new DripMiddlewareError(
        'Missing customer ID. Include X-Drip-Customer-Id header.',
        'CUSTOMER_RESOLUTION_FAILED',
        400,
      );
    }

    return customerId;
  }

  if (resolver === 'query') {
    const query = request.query ?? {};
    const customerId = query['drip_customer_id'] ?? query['customer_id'];
    const id = Array.isArray(customerId) ? customerId[0] : customerId;

    if (!id) {
      throw new DripMiddlewareError(
        'Missing customer ID. Include drip_customer_id query parameter.',
        'CUSTOMER_RESOLUTION_FAILED',
        400,
      );
    }

    return id;
  }

  throw new DripMiddlewareError(
    `Invalid customer resolver: ${resolver as string}`,
    'CONFIGURATION_ERROR',
    500,
  );
}

/**
 * Resolve quantity from configuration.
 */
export async function resolveQuantity<TRequest>(
  request: TRequest,
  config: WithDripConfig<TRequest>,
): Promise<number> {
  if (typeof config.quantity === 'function') {
    return config.quantity(request);
  }
  return config.quantity;
}

/**
 * Generate idempotency key for a request.
 */
export async function generateIdempotencyKey<TRequest extends GenericRequest>(
  request: TRequest,
  customerId: string,
  config: WithDripConfig<TRequest>,
): Promise<string> {
  if (config.idempotencyKey) {
    return config.idempotencyKey(request);
  }

  // Default: hash of method + url + customer + body content.
  // CRIT-09: No Date.now() — timestamps break idempotency on retry.
  // The body hash ensures identical requests produce the same key,
  // while different payloads produce different keys.
  const bodyStr = typeof request.body === 'string'
    ? request.body
    : (request.body ? JSON.stringify(request.body) : '');
  const components = [request.method, request.url, customerId, bodyStr];
  return `drip_${hashString(components.join('|')).slice(2, 18)}`;
}

// ============================================================================
// Drip Client Factory
// ============================================================================

/**
 * Create a Drip client from configuration.
 */
export function createDripClient<TRequest>(
  config: WithDripConfig<TRequest>,
): Drip {
  const apiKey = config.apiKey ?? process.env.DRIP_API_KEY;

  if (!apiKey) {
    throw new DripMiddlewareError(
      'Missing Drip API key. Set DRIP_API_KEY environment variable or pass apiKey in config.',
      'CONFIGURATION_ERROR',
      500,
    );
  }

  return new Drip({
    apiKey,
    baseUrl: config.baseUrl ?? process.env.DRIP_API_URL,
  });
}

// ============================================================================
// Core Middleware Logic
// ============================================================================

/**
 * Process a request through the Drip billing flow.
 *
 * This is the core logic used by all framework adapters.
 * It handles:
 * 1. Customer resolution
 * 2. Balance checking (if no payment proof)
 * 3. Charging the customer
 * 4. x402 payment flow orchestration
 */
/**
 * Success result from processRequest.
 */
export interface ProcessRequestSuccess {
  success: true;
  state: MiddlewareState;
  charge: ChargeResult;
  drip: Drip;
  isDuplicate: boolean;
}

/**
 * Failure result from processRequest.
 */
export interface ProcessRequestFailure {
  success: false;
  error: DripMiddlewareError;
  paymentRequired?: {
    headers: X402ResponseHeaders;
    paymentRequest: X402PaymentRequest;
  };
}

export type ProcessRequestResult = ProcessRequestSuccess | ProcessRequestFailure;

export async function processRequest<TRequest extends GenericRequest>(
  request: TRequest,
  config: WithDripConfig<TRequest>,
): Promise<ProcessRequestResult> {
  // Check if we should skip in development
  if (config.skipInDevelopment && process.env.NODE_ENV === 'development') {
    // Return a mock successful charge for development
    const drip = createDripClient(config);
    const mockCharge: ChargeResult = {
      success: true,
      usageEventId: 'dev_usage_event',
      isDuplicate: false,
      charge: {
        id: 'dev_charge',
        amountUsdc: '0.00',
        amountToken: '0',
        txHash: '0x0',
        status: 'CONFIRMED' as const,
      },
    };
    return {
      success: true,
      state: {
        customerId: 'dev_customer',
        quantity: typeof config.quantity === 'number' ? config.quantity : 1,
        idempotencyKey: 'dev_idempotency',
        hasPaymentProof: false,
      },
      charge: mockCharge,
      drip,
      isDuplicate: false,
    };
  }

  try {
    // Create Drip client
    const drip = createDripClient(config);

    // Resolve customer and quantity
    const customerId = await resolveCustomerId(request, config);
    const quantity = await resolveQuantity(request, config);
    const idempotencyKey = await generateIdempotencyKey(request, customerId, config);

    // Check for payment proof
    const paymentProofPresent = hasPaymentProof(request.headers);
    const paymentProof = paymentProofPresent
      ? parsePaymentProof(request.headers)
      : undefined;

    const state: MiddlewareState = {
      customerId,
      quantity,
      idempotencyKey,
      hasPaymentProof: paymentProofPresent,
      paymentProof: paymentProof ?? undefined,
    };

    // Resolve metadata
    const rawMetadata = typeof config.metadata === 'function'
      ? config.metadata(request)
      : config.metadata;
    const metadata = sanitizeChargeMetadata(rawMetadata, config);

    // Attempt to charge
    try {
      const chargeResult = await drip.charge({
        customerId,
        meter: config.meter,
        quantity,
        idempotencyKey,
        metadata,
      });

      // Call onCharge callback if provided
      if (config.onCharge) {
        await config.onCharge(chargeResult, request);
      }

      return {
        success: true,
        state,
        charge: chargeResult,
        drip,
        isDuplicate: chargeResult.isDuplicate ?? false,
      };
    } catch (error) {
      if (error instanceof DripError) {
        // Handle 402 Payment Required
        if (error.statusCode === 402) {
          // Require DRIP_RECIPIENT_ADDRESS to be configured
          const recipient = process.env.DRIP_RECIPIENT_ADDRESS;
          if (!recipient) {
            throw new DripMiddlewareError(
              'DRIP_RECIPIENT_ADDRESS environment variable must be configured for x402 payment flow.',
              'CONFIGURATION_ERROR',
              500,
            );
          }

          // Try to extract amount from error message (format: "... amount: X.XX USDC")
          // or use quantity as fallback (1 unit = 0.01 USDC default)
          let amount = '0.01';
          const amountMatch = error.message.match(/amount[:\s]+([0-9.]+)/i);
          if (amountMatch) {
            amount = amountMatch[1];
          } else {
            // Calculate based on quantity with default rate of 0.0001 USDC per unit
            const calculatedAmount = (quantity * 0.0001).toFixed(6);
            amount = calculatedAmount;
          }

          // Generate payment request for x402 flow
          const { headers, paymentRequest } = generatePaymentRequest({
            amount,
            recipient,
            usageId: idempotencyKey,
            description: `${config.meter} usage charge`,
          });

          return {
            success: false,
            error: new DripMiddlewareError(
              'Insufficient balance. Payment required.',
              'PAYMENT_REQUIRED',
              402,
            ),
            paymentRequired: { headers, paymentRequest },
          };
        }

        // Handle other Drip errors
        if (config.onError) {
          await config.onError(error, request);
        }

        throw new DripMiddlewareError(
          error.message,
          'CHARGE_FAILED',
          error.statusCode,
          { code: error.code },
        );
      }

      throw error;
    }
  } catch (error) {
    if (error instanceof DripMiddlewareError) {
      return { success: false, error };
    }

    // Wrap unexpected errors
    const message = error instanceof Error ? error.message : 'Unknown error';
    return {
      success: false,
      error: new DripMiddlewareError(message, 'INTERNAL_ERROR', 500),
    };
  }
}

// ============================================================================
// Exports
// ============================================================================

export type { WithDripConfig, X402PaymentProof, X402PaymentRequest };
