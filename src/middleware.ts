/**
 * Drip SDK - Middleware
 *
 * Complete middleware package including all framework adapters
 * and core utilities for building custom integrations.
 *
 * @example Next.js
 * ```typescript
 * import { withDrip } from '@drip-sdk/node/middleware';
 *
 * export const POST = withDrip({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: async (req) => {
 *     const session = await verifySession(req);
 *     return session.dripCustomerId;
 *   },
 * }, handler);
 * ```
 *
 * @example Express
 * ```typescript
 * import { dripMiddleware } from '@drip-sdk/node/middleware';
 *
 * app.use('/api', dripMiddleware({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: (req) => req.user.dripCustomerId,
 * }));
 * ```
 *
 * @example Custom Adapter
 * ```typescript
 * import { processRequest, createDripClient } from '@drip-sdk/node/middleware';
 *
 * // Use processRequest() to build your own framework adapter
 * ```
 *
 * @packageDocumentation
 */

// Re-export everything from middleware index
export * from './middleware/index.js';

// Re-export core SDK for convenience
export { Drip, DripError } from './index.js';

export type {
  DripConfig,
  Customer,
  ChargeParams,
  ChargeResult,
  ChargeStatus,
} from './index.js';
