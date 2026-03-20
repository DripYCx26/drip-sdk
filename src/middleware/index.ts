/**
 * Drip Middleware
 *
 * Framework adapters for integrating Drip billing into your application.
 *
 * @example Next.js App Router
 * ```typescript
 * import { withDrip } from '@drip-sdk/node/next';
 *
 * export const POST = withDrip({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: async (req) => {
 *     const session = await verifySession(req);
 *     return session.dripCustomerId;
 *   },
 * }, async (req, { charge }) => {
 *   return Response.json({ success: true });
 * });
 * ```
 *
 * @example Express
 * ```typescript
 * import { dripMiddleware } from '@drip-sdk/node/express';
 *
 * app.use('/api/paid', dripMiddleware({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: (req) => req.user.dripCustomerId,
 * }));
 * ```
 */

// Core types and utilities
export type {
  WithDripConfig,
  DripContext,
  X402PaymentProof,
  X402PaymentRequest,
  X402ResponseHeaders,
  DripMiddlewareErrorCode,
  GenericRequest,
  ResponseBuilder,
} from './types.js';

export { DripMiddlewareError } from './types.js';

// Core processing (for custom adapters)
export {
  processRequest,
  hasPaymentProof,
  parsePaymentProof,
  generatePaymentRequest,
  resolveCustomerId,
  resolveQuantity,
  generateIdempotencyKey,
  createDripClient,
  getHeader,
  BILLING_IDENTITY_HEADERS,
  BILLING_IDENTITY_QUERY_PARAMS,
  stripBillingIdentityHeaders,
  stripBillingIdentityQueryParams,
  stripBillingIdentitySearchParams,
  stripBillingIdentityQueryFromUrl,
} from './core.js';

// Next.js adapter
export {
  withDrip,
  createWithDrip,
  hasPaymentProofHeaders as hasNextPaymentProof,
  getDripHeader,
} from './next.js';

export type {
  NextRequest,
  NextRouteHandler,
  DripRouteHandler,
  NextDripConfig,
} from './next.js';

// Express adapter
export {
  dripMiddleware,
  createDripMiddleware,
  hasPaymentProofHeaders as hasExpressPaymentProof,
  hasDripContext,
  getDripContext,
} from './express.js';

export type {
  ExpressRequest,
  ExpressResponse,
  ExpressNextFunction,
  ExpressMiddleware,
  DripExpressRequest,
  ExpressDripConfig,
} from './express.js';
