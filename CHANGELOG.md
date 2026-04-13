# Changelog

All notable changes to the Drip Node.js SDK are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses semantic versioning.

## [0.2.0]

### Added

- **Payload Mapping Engine.** Server-side translator from your native JSON shape
  into Drip's canonical usage event. New methods:
  - `createPayloadMapping(params)`
  - `listPayloadMappings()`
  - `getPayloadMapping(id)`
  - `updatePayloadMapping(id, patch)`
  - `deletePayloadMapping(id)`
  - `listPayloadMappingVersions(id)`
  - `dryRunPayloadMapping(id, payload)`
  - `ingestViaMapping(sourceName, payload)`

  See the "Payload Mappings — Custom Shapes Without Code Changes" section
  in `FULL_SDK.md` for the full walkthrough.

- **`trackUsage` `mode` parameter.** Replaces the removed `charge()` /
  `chargeAsync()` methods:

  | mode | endpoint | semantics |
  | ---- | -------- | --------- |
  | `'sync'` (default) | `POST /usage` | Billing-aware — creates a charge if a pricing plan matches |
  | `'batch'` | `POST /usage/async` | Queued, returns 202 |
  | `'internal'` | `POST /usage/internal` | Visibility-only, never bills |

- **Auto-promote on the response.** When the backend routes `POST /usage` to
  the internal/visibility path (because the customer has no on-chain address),
  the response now carries:
  - `mode: 'internal'`
  - `autoPromoted: true | false`
  - `reason: string`
  - `charge: null`

  Existing callers that never set an `onchainAddress` no longer get a hard
  400 — `trackUsage()` "just works" for plain tracking.

- TypeScript: `BaseTrackUsageResult` now exposes optional `mode`,
  `autoPromoted`, and `reason` fields so consumers can branch on the
  auto-promote without a type cast.

### Removed

- `charge()` and `chargeAsync()` (breaking). They were thin wrappers around
  `POST /usage` / `POST /usage/async`. Migration:

  ```typescript
  // before
  drip.charge({ customerId, meter, quantity });
  drip.chargeAsync({ customerId, meter, quantity });

  // after
  drip.trackUsage({ customerId, meter, quantity });
  drip.trackUsage({ customerId, meter, quantity, mode: 'batch' });
  ```

  `getCharge()` and `listCharges()` are kept for read-only reconciliation.

### Fixed

- `PricingPlan` parity with the backend: `currency` defaults to `'USDC'`,
  `pricingModel` is optional, unknown fields are ignored. `listPricingPlans()`
  no longer hard-crashes when the backend omits these fields.

- Internal `wrapApiCall()`, `createStreamMeter()`, and the Express/Next.js
  middleware adapters now route through `trackUsage()` instead of the removed
  `charge()` method.

## [0.1.2] — historical

Previous baseline. See git history.
