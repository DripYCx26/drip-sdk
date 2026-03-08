/**
 * StreamMeter - Accumulate usage locally and charge once at the end.
 *
 * Perfect for LLM token streaming and other high-frequency metering scenarios
 * where you want to avoid making an API call for every small increment.
 *
 * @example
 * ```typescript
 * const meter = drip.createStreamMeter({
 *   customerId: 'cust_abc123',
 *   meter: 'tokens',
 * });
 *
 * for await (const chunk of llmStream) {
 *   meter.add(chunk.tokens);
 * }
 *
 * const result = await meter.flush();
 * console.log(`Charged ${result.charge.amountUsdc} for ${result.quantity} tokens`);
 * ```
 */

import type { ChargeResult, ChargeParams } from './index.js';
import { deterministicIdempotencyKey } from './idempotency.js';

/**
 * Options for creating a StreamMeter.
 */
export interface StreamMeterOptions {
  /**
   * The Drip customer ID to charge.
   */
  customerId: string;

  /**
   * The usage meter/type to record against.
   * Must match a meter configured in your pricing plan.
   */
  meter: string;

  /**
   * Unique key to prevent duplicate charges.
   * If not provided, one will be generated.
   */
  idempotencyKey?: string;

  /**
   * Additional metadata to attach to the charge.
   */
  metadata?: Record<string, unknown>;

  /**
   * Auto-flush when accumulated quantity reaches this threshold.
   * Useful for long-running streams where you want periodic charges.
   */
  flushThreshold?: number;

  /**
   * Callback invoked on each add() call.
   * Useful for logging or progress tracking.
   */
  onAdd?: (quantity: number, total: number) => void;

  /**
   * Callback invoked after each successful flush.
   */
  onFlush?: (result: StreamMeterFlushResult) => void;
}

/**
 * Result of flushing a StreamMeter.
 */
export interface StreamMeterFlushResult {
  /** Whether the flush was successful */
  success: boolean;

  /** The quantity that was charged */
  quantity: number;

  /** The charge result from the API (if quantity > 0) */
  charge: ChargeResult['charge'] | null;

  /** Whether this was an idempotent replay */
  isDuplicate: boolean;
}

/**
 * Internal charge function type (injected by Drip class).
 */
export type ChargeFn = (params: ChargeParams) => Promise<ChargeResult>;

/**
 * StreamMeter accumulates usage locally and charges once when flushed.
 *
 * This is ideal for:
 * - LLM token streaming (charge once at end of stream)
 * - High-frequency events (batch small increments)
 * - Partial failure handling (charge for what was delivered)
 */
export class StreamMeter {
  private _total: number = 0;
  private _flushed: boolean = false;
  private _flushCount: number = 0;
  private readonly _chargeFn: ChargeFn;
  private readonly _options: StreamMeterOptions;

  /**
   * Creates a new StreamMeter.
   *
   * @param chargeFn - The charge function from Drip client
   * @param options - StreamMeter configuration
   */
  constructor(chargeFn: ChargeFn, options: StreamMeterOptions) {
    this._chargeFn = chargeFn;
    this._options = options;
  }

  /**
   * Current accumulated quantity (not yet charged).
   */
  get total(): number {
    return this._total;
  }

  /**
   * Whether this meter has been flushed at least once.
   */
  get isFlushed(): boolean {
    return this._flushed;
  }

  /**
   * Number of times this meter has been flushed.
   */
  get flushCount(): number {
    return this._flushCount;
  }

  /**
   * Add quantity to the accumulated total.
   *
   * If a flushThreshold is set and the total exceeds it,
   * this will automatically trigger a flush.
   *
   * @param quantity - Amount to add (must be positive)
   * @returns Promise that resolves when add completes (may trigger auto-flush)
   */
  async add(quantity: number): Promise<StreamMeterFlushResult | null> {
    if (quantity <= 0) {
      return null;
    }

    this._total += quantity;

    // Invoke callback if provided
    this._options.onAdd?.(quantity, this._total);

    // Check for auto-flush threshold
    if (
      this._options.flushThreshold !== undefined &&
      this._total >= this._options.flushThreshold
    ) {
      return this.flush();
    }

    return null;
  }

  /**
   * Synchronously add quantity without auto-flush.
   * Use this for maximum performance when you don't need threshold-based flushing.
   *
   * @param quantity - Amount to add (must be positive)
   */
  addSync(quantity: number): void {
    if (quantity <= 0) {
      return;
    }

    this._total += quantity;

    // Invoke callback if provided
    this._options.onAdd?.(quantity, this._total);
  }

  /**
   * Flush accumulated usage and charge the customer.
   *
   * If total is 0, returns a success result with no charge.
   * After flush, the meter resets to 0 and can be reused.
   *
   * @returns The flush result including charge details
   */
  async flush(): Promise<StreamMeterFlushResult> {
    const quantity = this._total;

    // Reset total before charging to avoid double-counting on retry
    this._total = 0;

    // Nothing to charge
    if (quantity === 0) {
      const result: StreamMeterFlushResult = {
        success: true,
        quantity: 0,
        charge: null,
        isDuplicate: false,
      };
      return result;
    }

    // Generate idempotency key for this flush
    const idempotencyKey = this._options.idempotencyKey
      ? `${this._options.idempotencyKey}_flush_${this._flushCount}`
      : deterministicIdempotencyKey('stream', this._options.customerId, this._options.meter, quantity, this._flushCount);

    // Charge the customer
    try {
      const chargeResult = await this._chargeFn({
        customerId: this._options.customerId,
        meter: this._options.meter,
        quantity,
        idempotencyKey,
        metadata: this._options.metadata,
      });

      this._flushed = true;
      this._flushCount++;

      const result: StreamMeterFlushResult = {
        success: chargeResult.success,
        quantity,
        charge: chargeResult.charge,
        isDuplicate: chargeResult.isDuplicate,
      };

      // Invoke callback if provided
      this._options.onFlush?.(result);

      return result;
    } catch (error) {
      // Restore usage total so it can be retried on the next flush
      this._total += quantity;
      throw error;
    }
  }

  /**
   * Reset the meter without charging.
   * Use this to discard accumulated usage (e.g., on error before delivery).
   */
  reset(): void {
    this._total = 0;
  }
}
