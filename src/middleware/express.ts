/**
 * Drip Express Adapter
 *
 * Provides the `dripMiddleware` for Express.js applications.
 * Handles the complete x402 payment flow automatically.
 *
 * @example
 * ```typescript
 * import express from 'express';
 * import { dripMiddleware } from '@drip-sdk/node/express';
 *
 * const app = express();
 *
 * // Apply to specific routes
 * app.use('/api/paid', dripMiddleware({
 *   meter: 'api_calls',
 *   quantity: 1,
 * }));
 *
 * app.post('/api/paid/generate', (req, res) => {
 *   // Payment already verified - req.drip contains context
 *   console.log(`Charged: ${req.drip.charge.charge.amountUsdc} USDC`);
 *   res.json({ success: true });
 * });
 * ```
 */

import type { Drip } from '../index.js';
import type {
  WithDripConfig,
  DripContext,
  X402ResponseHeaders,
  GenericRequest,
} from './types.js';
import { DripMiddlewareError } from './types.js';
import {
  processRequest,
  hasPaymentProof,
  BILLING_IDENTITY_HEADERS,
} from './core.js';

// ============================================================================
// Express Types
// ============================================================================

/**
 * Express request type.
 * We use a minimal interface to avoid requiring express as a dependency.
 */
export interface ExpressRequest {
  method: string;
  url: string;
  originalUrl: string;
  path: string;
  headers: Record<string, string | string[] | undefined>;
  query: Record<string, string | string[] | undefined>;
  params: Record<string, string>;
  body?: unknown;
}

/**
 * Express response type.
 */
export interface ExpressResponse {
  status(code: number): ExpressResponse;
  set(headers: Record<string, string>): ExpressResponse;
  json(body: unknown): void;
  send(body: unknown): void;
}

/**
 * Express next function.
 */
export type ExpressNextFunction = (error?: unknown) => void;

/**
 * Express middleware type.
 */
export type ExpressMiddleware = (
  req: ExpressRequest,
  res: ExpressResponse,
  next: ExpressNextFunction,
) => void | Promise<void>;

/**
 * Extended Express request with Drip context.
 */
export interface DripExpressRequest extends ExpressRequest {
  drip: DripContext;
}

/**
 * Configuration specific to Express adapter.
 */
export interface ExpressDripConfig extends WithDripConfig<ExpressRequest> {
  /**
   * Custom error handler.
   * Return true to indicate the error was handled.
   */
  errorHandler?: (
    error: DripMiddlewareError,
    req: ExpressRequest,
    res: ExpressResponse,
  ) => boolean | Promise<boolean>;

  /**
   * Whether to attach the Drip context to the request object.
   * @default true
   */
  attachToRequest?: boolean;
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Normalize Express headers to a consistent format.
 */
function normalizeHeaders(
  headers: Record<string, string | string[] | undefined>,
): Record<string, string | undefined> {
  const result: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(headers)) {
    result[key.toLowerCase()] = Array.isArray(value) ? value[0] : value;
  }
  return result;
}

/**
 * Send a 402 Payment Required response.
 */
function sendPaymentRequired(
  res: ExpressResponse,
  headers: X402ResponseHeaders,
  paymentRequest: {
    amount: string;
    recipient: string;
    usageId: string;
    description: string;
    expiresAt: number;
    nonce: string;
    timestamp: number;
  },
): void {
  res.status(402).set(headers as Record<string, string>).json({
    error: 'Payment required',
    code: 'PAYMENT_REQUIRED',
    paymentRequest,
    instructions: {
      step1: 'Sign the payment request with your session key using EIP-712',
      step2: 'Retry the request with X-Payment-* headers',
      documentation: 'https://docs.drippay.dev/x402',
    },
  });
}

/**
 * Send an error response.
 */
function sendError(
  res: ExpressResponse,
  message: string,
  code: string,
  status: number,
  details?: Record<string, unknown>,
): void {
  res.status(status).json({
    error: message,
    code,
    ...(details && { details }),
  });
}

// ============================================================================
// Main Middleware
// ============================================================================

/**
 * Express middleware for Drip billing.
 *
 * This middleware:
 * 1. Resolves the customer ID from headers or query
 * 2. Checks customer balance
 * 3. If insufficient, returns 402 with x402 payment headers
 * 4. If payment proof provided, verifies and processes
 * 5. Charges the customer
 * 6. Attaches Drip context to req.drip
 * 7. Calls next() on success
 *
 * @param config - Configuration for billing
 * @returns Express middleware
 *
 * @example
 * ```typescript
 * // Apply to all routes under /api/paid
 * app.use('/api/paid', dripMiddleware({
 *   meter: 'api_calls',
 *   quantity: 1,
 * }));
 *
 * // Or with dynamic quantity
 * app.use('/api/ai', dripMiddleware({
 *   meter: 'tokens',
 *   quantity: (req) => req.body?.maxTokens ?? 100,
 * }));
 * ```
 */
export function dripMiddleware(config: ExpressDripConfig): ExpressMiddleware {
  const attachToRequest = config.attachToRequest ?? true;

  return async (req, res, next) => {
    // Convert Express request to generic format, stripping billing identity
    // headers to prevent accidental use in customer resolvers.
    const normalized = normalizeHeaders(req.headers);
    for (const header of BILLING_IDENTITY_HEADERS) {
      delete normalized[header];
    }
    const genericRequest = {
      method: req.method,
      url: req.originalUrl || req.url,
      headers: normalized,
      query: req.query as Record<string, string | undefined>,
      body: req.body,
    };

    // Resolve quantity if it's a function (needs access to original request)
    const resolvedQuantity = typeof config.quantity === 'function'
      ? await config.quantity(req)
      : config.quantity;

    // Resolve customer ID if it's a function - wrap to use generic request
    let resolvedCustomerResolver: ((r: GenericRequest) => string | Promise<string>);
    const originalResolver = config.customerResolver;
    resolvedCustomerResolver = async () => originalResolver(req);

    // Resolve idempotencyKey if it's a function
    let resolvedIdempotencyKey: ((r: GenericRequest) => string | Promise<string>) | undefined;
    if (typeof config.idempotencyKey === 'function') {
      const originalIdempotencyKey = config.idempotencyKey;
      resolvedIdempotencyKey = async () => originalIdempotencyKey(req);
    }

    // Resolve metadata if it's a function
    const resolvedMetadata = typeof config.metadata === 'function'
      ? config.metadata(req)
      : config.metadata;

    // Create a generic config for processRequest
    const genericConfig: WithDripConfig<typeof genericRequest> = {
      meter: config.meter,
      quantity: resolvedQuantity,
      apiKey: config.apiKey,
      baseUrl: config.baseUrl,
      customerResolver: resolvedCustomerResolver,
      idempotencyKey: resolvedIdempotencyKey,
      metadata: resolvedMetadata,
      metadataAllowlist: config.metadataAllowlist,
      redactMetadataKeys: config.redactMetadataKeys,
      skipInDevelopment: config.skipInDevelopment,
      // Clear callbacks that need the original request type
      onCharge: undefined,
      onError: undefined,
    };

    // Process the request through Drip billing
    const result = await processRequest(genericRequest, genericConfig);

    if (!result.success) {
      // Handle custom error handler
      if (config.errorHandler) {
        const handled = await config.errorHandler(result.error, req, res);
        if (handled) {
          return;
        }
      }

      // Handle 402 Payment Required
      if (result.paymentRequired) {
        sendPaymentRequired(
          res,
          result.paymentRequired.headers,
          result.paymentRequired.paymentRequest,
        );
        return;
      }

      // Send error response
      sendError(
        res,
        result.error.message,
        result.error.code,
        result.error.statusCode,
        result.error.details,
      );
      return;
    }

    // Call original onCharge callback if provided
    if (config.onCharge) {
      await config.onCharge(result.charge, req);
    }

    // Build context
    const dripContext: DripContext = {
      drip: result.drip,
      customerId: result.state.customerId,
      charge: result.charge,
      isDuplicate: result.isDuplicate,
    };

    // Attach to request if configured
    if (attachToRequest) {
      (req as DripExpressRequest).drip = dripContext;
    }

    // Continue to next middleware/handler
    next();
  };
}

// ============================================================================
// Convenience Exports
// ============================================================================

/**
 * Create a dripMiddleware factory with default configuration.
 * Useful for consistent settings across multiple route groups.
 *
 * @example
 * ```typescript
 * // lib/drip.ts
 * import { createDripMiddleware } from '@drip-sdk/node/express';
 *
 * export const drip = createDripMiddleware({
 *   apiKey: process.env.DRIP_API_KEY,
 *   baseUrl: process.env.DRIP_API_URL,
 * });
 *
 * // routes/api.ts
 * import { drip } from '../lib/drip';
 *
 * router.use('/paid', drip({ meter: 'api_calls', quantity: 1 }));
 * ```
 */
export function createDripMiddleware(
  defaults: Partial<Omit<ExpressDripConfig, 'meter' | 'quantity'>>,
): (
  config: Pick<ExpressDripConfig, 'meter' | 'quantity'> & Partial<Omit<ExpressDripConfig, 'meter' | 'quantity'>>,
) => ExpressMiddleware {
  return (config) => {
    return dripMiddleware({ ...defaults, ...config } as ExpressDripConfig);
  };
}

/**
 * Check if an Express request (or raw Headers object) has x402 payment proof headers.
 * Useful for conditional logic in routes.
 */
export function hasPaymentProofHeaders(reqOrHeaders: ExpressRequest | Headers): boolean {
  if (reqOrHeaders instanceof Headers) {
    const normalized: Record<string, string | undefined> = {};
    reqOrHeaders.forEach((value, key) => {
      normalized[key.toLowerCase()] = value;
    });
    return hasPaymentProof(normalized);
  }
  return hasPaymentProof(normalizeHeaders(reqOrHeaders.headers));
}

/**
 * Type guard to check if request has Drip context attached.
 */
export function hasDripContext(
  req: ExpressRequest,
): req is DripExpressRequest {
  return 'drip' in req && typeof (req as DripExpressRequest).drip === 'object';
}

/**
 * Get Drip context from request, throwing if not present.
 */
export function getDripContext(req: ExpressRequest): DripContext {
  if (!hasDripContext(req)) {
    throw new Error(
      'Drip context not found on request. Ensure dripMiddleware is applied before this route.',
    );
  }
  return req.drip;
}
