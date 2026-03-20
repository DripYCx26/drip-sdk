/**
 * Drip SDK - Next.js Adapter
 *
 * One-liner integration for Next.js App Router API routes.
 *
 * @example
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
 * @packageDocumentation
 */

// Re-export Next.js specific
export {
  withDrip,
  createWithDrip,
  hasPaymentProofHeaders,
  getDripHeader,
} from './middleware/next.js';

export type {
  NextRequest,
  NextRouteHandler,
  DripRouteHandler,
  NextDripConfig,
} from './middleware/next.js';

// Re-export shared types
export type {
  WithDripConfig,
  DripContext,
  X402PaymentProof,
  X402PaymentRequest,
} from './middleware/types.js';

export { DripMiddlewareError } from './middleware/types.js';

// Re-export core SDK for convenience
export { Drip, DripError } from './index.js';

export type {
  DripConfig,
  Customer,
  ChargeParams,
  ChargeResult,
  ChargeStatus,
} from './index.js';
