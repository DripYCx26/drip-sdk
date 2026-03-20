/**
 * Drip Next.js Adapter
 *
 * Provides the `withDrip` wrapper for Next.js App Router API routes.
 * Handles the complete x402 payment flow automatically.
 *
 * @example
 * ```typescript
 * // app/api/generate/route.ts
 * import { withDrip } from '@drip-sdk/node/next';
 *
 * export const POST = withDrip({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: async (req) => {
 *     const session = await verifySession(req);
 *     return session.dripCustomerId;
 *   },
 * }, async (req, { drip, customerId, charge }) => {
 *   // Your handler - payment already verified
 *   const result = await generateContent(req);
 *   return Response.json({ result });
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
  getHeader,
  hasPaymentProof,
  BILLING_IDENTITY_HEADERS,
  stripBillingIdentityHeaders,
  stripBillingIdentitySearchParams,
  stripBillingIdentityQueryFromUrl,
} from './core.js';

// ============================================================================
// Next.js Types
// ============================================================================

/**
 * Next.js App Router request type.
 * We use a minimal interface to avoid requiring next as a dependency.
 */
export interface NextRequest {
  method: string;
  url: string;
  headers: Headers;
  nextUrl?: { searchParams: URLSearchParams };
  json(): Promise<unknown>;
  text(): Promise<string>;
  clone(): NextRequest;
}

/**
 * Next.js route handler type.
 */
export type NextRouteHandler = (
  request: NextRequest,
  context?: { params?: Record<string, string> },
) => Response | Promise<Response>;

/**
 * Handler with Drip context.
 */
export type DripRouteHandler = (
  request: NextRequest,
  context: DripContext & { params?: Record<string, string> },
) => Response | Promise<Response>;

/**
 * Configuration specific to Next.js adapter.
 */
export interface NextDripConfig extends WithDripConfig<NextRequest> {
  /**
   * Custom error response generator.
   * Return a Response to override default error handling.
   */
  errorResponse?: (
    error: DripMiddlewareError,
    request: NextRequest,
  ) => Response | Promise<Response> | null;
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Convert Next.js Headers to a plain object.
 */
function headersToObject(headers: Headers): Record<string, string> {
  const result: Record<string, string> = {};
  headers.forEach((value, key) => {
    result[key.toLowerCase()] = value;
  });
  return result;
}

/**
 * Convert URL search params to a plain object.
 */
function searchParamsToObject(
  params: URLSearchParams,
): Record<string, string> {
  const result: Record<string, string> = {};
  params.forEach((value, key) => {
    result[key] = value;
  });
  return result;
}

function getSanitizedRequestUrl(request: NextRequest): string {
  return stripBillingIdentityQueryFromUrl(request.url);
}

function stripBillingIdentityHeadersFromRequestHeaders(headers: Headers): Headers {
  const sanitized = new Headers(headers);
  for (const header of BILLING_IDENTITY_HEADERS) {
    sanitized.delete(header);
  }
  return sanitized;
}

function createSanitizedNextUrl(
  nextUrl: NonNullable<NextRequest['nextUrl']>,
  sanitizedUrl: URL,
): NonNullable<NextRequest['nextUrl']> {
  const sanitizedSearchParams = stripBillingIdentitySearchParams(nextUrl.searchParams);

  return new Proxy(nextUrl, {
    get(target, prop) {
      if (prop === 'searchParams') {
        return sanitizedSearchParams;
      }
      if (prop === 'search') {
        return sanitizedUrl.search;
      }
      if (prop === 'href') {
        return sanitizedUrl.toString();
      }
      if (prop === 'toString' || prop === 'toJSON') {
        return () => sanitizedUrl.toString();
      }

      const value = Reflect.get(target, prop, target);
      return typeof value === 'function'
        ? value.bind(target)
        : value;
    },
  });
}

/**
 * Present a resolver-safe request view with spoofable billing identity removed
 * from headers and query-bearing URL surfaces.
 */
function createSanitizedResolverRequest(
  request: NextRequest,
): NextRequest {
  const sanitizedHeaders = stripBillingIdentityHeadersFromRequestHeaders(request.headers);
  const sanitizedUrl = getSanitizedRequestUrl(request);
  const sanitizedParsedUrl = new URL(sanitizedUrl);
  const sanitizedNextUrl = request.nextUrl
    ? createSanitizedNextUrl(request.nextUrl, sanitizedParsedUrl)
    : undefined;

  return new Proxy(request, {
    get(target, prop) {
      if (prop === 'headers') {
        return sanitizedHeaders;
      }
      if (prop === 'url') {
        return sanitizedUrl;
      }
      if (prop === 'nextUrl') {
        return sanitizedNextUrl;
      }
      if (prop === 'clone') {
        return () => createSanitizedResolverRequest(target.clone());
      }

      const value = Reflect.get(target, prop, target);
      return typeof value === 'function'
        ? value.bind(target)
        : value;
    },
  });
}

/**
 * Create a JSON error response.
 */
function errorResponse(
  message: string,
  code: string,
  status: number,
  details?: Record<string, unknown>,
): Response {
  return Response.json(
    {
      error: message,
      code,
      ...(details && { details }),
    },
    { status },
  );
}

/**
 * Create a 402 Payment Required response with x402 headers.
 */
function paymentRequiredResponse(
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
): Response {
  const responseHeaders = new Headers();

  // Add all x402 headers
  Object.entries(headers).forEach(([key, value]) => {
    responseHeaders.set(key, value);
  });

  // Add standard headers
  responseHeaders.set('Content-Type', 'application/json');

  return new Response(
    JSON.stringify({
      error: 'Payment required',
      code: 'PAYMENT_REQUIRED',
      paymentRequest,
      instructions: {
        step1: 'Sign the payment request with your session key using EIP-712',
        step2: 'Retry the request with X-Payment-* headers',
        documentation: 'https://docs.drippay.dev/x402',
      },
    }),
    {
      status: 402,
      headers: responseHeaders,
    },
  );
}

// ============================================================================
// Main Wrapper
// ============================================================================

/**
 * Wrap a Next.js App Router handler with Drip billing.
 *
 * This wrapper:
 * 1. Resolves the customer ID with your explicit customerResolver
 * 2. Checks customer balance
 * 3. If insufficient, returns 402 with x402 payment headers
 * 4. If payment proof provided, verifies and processes
 * 5. Charges the customer
 * 6. Calls your handler with the Drip context
 *
 * @param config - Configuration for billing
 * @param handler - Your route handler
 * @returns A Next.js route handler
 *
 * @example
 * ```typescript
 * export const POST = withDrip({
 *   meter: 'tokens',
 *   quantity: (req) => req.headers.get('x-token-count') ?? 1,
 *   customerResolver: async (req) => {
 *     const session = await verifySession(req);
 *     return session.dripCustomerId;
 *   },
 * }, async (req, { charge }) => {
 *   console.log(`Charged ${charge.charge.amountUsdc} USDC`);
 *   return Response.json({ success: true });
 * });
 * ```
 */
export function withDrip(
  config: NextDripConfig,
  handler: DripRouteHandler,
): NextRouteHandler {
  return async (request, routeContext) => {
    // Read the request body for idempotency key generation.
    // Clone first since NextRequest body streams can only be read once.
    let requestBody: unknown;
    try {
      const cloned = request.clone();
      const bodyText = await cloned.text();
      if (bodyText) {
        try {
          requestBody = JSON.parse(bodyText);
        } catch {
          requestBody = bodyText;
        }
      }
    } catch {
      // Body may not be available (e.g., GET requests) — leave undefined
    }

    // Convert Next.js request to generic format, stripping billing identity
    // headers and query params to prevent accidental use in customer resolvers.
    const sanitizedSearchParams = request.nextUrl
      ? stripBillingIdentitySearchParams(request.nextUrl.searchParams)
      : undefined;
    const headers = stripBillingIdentityHeaders(headersToObject(request.headers));
    const genericRequest = {
      method: request.method,
      url: getSanitizedRequestUrl(request),
      headers,
      query: sanitizedSearchParams
        ? searchParamsToObject(sanitizedSearchParams)
        : {},
      body: requestBody,
    };
    const resolverRequest = createSanitizedResolverRequest(request);

    // Resolve quantity if it's a function (needs access to original request)
    const resolvedQuantity = typeof config.quantity === 'function'
      ? await config.quantity(request)
      : config.quantity;

    // Wrap the customer resolver to use the original Next.js request
    let resolvedCustomerResolver: ((req: GenericRequest) => string | Promise<string>);
    const originalResolver = config.customerResolver;
    if (typeof originalResolver === 'function') {
      resolvedCustomerResolver = async () => originalResolver(resolverRequest);
    } else {
      resolvedCustomerResolver = originalResolver as unknown as (req: GenericRequest) => string | Promise<string>;
    }

    // Resolve idempotencyKey if it's a function
    let resolvedIdempotencyKey: ((req: GenericRequest) => string | Promise<string>) | undefined;
    if (typeof config.idempotencyKey === 'function') {
      const originalIdempotencyKey = config.idempotencyKey;
      resolvedIdempotencyKey = async () => originalIdempotencyKey(request);
    }

    // Resolve metadata if it's a function
    const resolvedMetadata = typeof config.metadata === 'function'
      ? config.metadata(request)
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
      // Handle custom error response
      if (config.errorResponse) {
        const customResponse = await config.errorResponse(result.error, request);
        if (customResponse) {
          return customResponse;
        }
      }

      // Handle 402 Payment Required
      if (result.paymentRequired) {
        return paymentRequiredResponse(
          result.paymentRequired.headers,
          result.paymentRequired.paymentRequest,
        );
      }

      // Return error response
      return errorResponse(
        result.error.message,
        result.error.code,
        result.error.statusCode,
        result.error.details,
      );
    }

    // Call original onCharge callback if provided
    if (config.onCharge) {
      await config.onCharge(result.charge, request);
    }

    // Build context for the handler
    const dripContext: DripContext & { params?: Record<string, string> } = {
      drip: result.drip,
      customerId: result.state.customerId,
      charge: result.charge,
      isDuplicate: result.isDuplicate,
      params: routeContext?.params,
    };

    // Call the wrapped handler
    try {
      return await handler(request, dripContext);
    } catch (error) {
      // Let errors propagate (Next.js will handle them)
      throw error;
    }
  };
}

// ============================================================================
// Convenience Exports
// ============================================================================

/**
 * Create a withDrip wrapper with default configuration.
 * Useful for consistent settings across multiple routes.
 *
 * @example
 * ```typescript
 * // lib/drip.ts
 * import { createWithDrip } from '@drip-sdk/node/next';
 *
 * export const withDrip = createWithDrip({
 *   apiKey: process.env.DRIP_API_KEY,
 *   baseUrl: process.env.DRIP_API_URL,
 *   customerResolver: async (req) => {
 *     const session = await verifySession(req);
 *     return session.dripCustomerId;
 *   },
 * });
 *
 * // app/api/generate/route.ts
 * import { withDrip } from '@/lib/drip';
 *
 * export const POST = withDrip({ meter: 'api_calls', quantity: 1 }, handler);
 * ```
 */
export function createWithDrip(
  defaults: Partial<Omit<NextDripConfig, 'meter' | 'quantity'>>,
): (
  config: Pick<NextDripConfig, 'meter' | 'quantity'> & Partial<Omit<NextDripConfig, 'meter' | 'quantity'>>,
  handler: DripRouteHandler,
) => NextRouteHandler {
  return (config, handler) => {
    return withDrip({ ...defaults, ...config } as NextDripConfig, handler);
  };
}

/**
 * Check if a Next.js request has x402 payment proof headers.
 * Useful for conditional logic in handlers.
 */
export function hasPaymentProofHeaders(request: NextRequest): boolean {
  return hasPaymentProof(headersToObject(request.headers));
}

/**
 * Get a header value from a Next.js request.
 */
export function getDripHeader(
  request: NextRequest,
  name: string,
): string | undefined {
  return getHeader(headersToObject(request.headers), name);
}
