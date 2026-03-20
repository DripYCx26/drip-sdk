/**
 * Drip SDK - Express Adapter
 *
 * Middleware for Express.js applications.
 *
 * @example
 * ```typescript
 * import express from 'express';
 * import { dripMiddleware } from '@drip-sdk/node/express';
 *
 * const app = express();
 *
 * app.use('/api/paid', dripMiddleware({
 *   meter: 'api_calls',
 *   quantity: 1,
 *   customerResolver: (req) => req.user.dripCustomerId,
 * }));
 *
 * app.post('/api/paid/generate', (req, res) => {
 *   console.log(`Charged: ${req.drip.charge.charge.amountUsdc} USDC`);
 *   res.json({ success: true });
 * });
 * ```
 *
 * @packageDocumentation
 */

// Re-export Express specific
export {
  dripMiddleware,
  createDripMiddleware,
  hasPaymentProofHeaders,
  hasDripContext,
  getDripContext,
} from './middleware/express.js';

export type {
  ExpressRequest,
  ExpressResponse,
  ExpressNextFunction,
  ExpressMiddleware,
  DripExpressRequest,
  ExpressDripConfig,
} from './middleware/express.js';

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
