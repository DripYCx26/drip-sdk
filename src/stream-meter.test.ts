import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StreamMeter } from './stream-meter.js';
import type { ChargeFn } from './stream-meter.js';
import type { ChargeResult } from './index.js';

function makeChargeResult(quantity: number, overrides?: Partial<ChargeResult>): ChargeResult {
  return {
    success: true,
    usageEventId: `evt_${quantity}`,
    isDuplicate: false,
    charge: {
      id: `chg_${quantity}`,
      amountUsdc: (quantity * 0.001).toFixed(6),
      amountToken: (quantity * 0.001).toFixed(6),
      txHash: null,
      status: 'PENDING',
    },
    ...overrides,
  };
}

describe('StreamMeter', () => {
  let chargeFn: ReturnType<typeof vi.fn<ChargeFn>>;

  beforeEach(() => {
    chargeFn = vi.fn<ChargeFn>().mockImplementation(async (params) =>
      makeChargeResult(params.quantity)
    );
  });

  it('add() accumulates quantity on total', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(100);
    await meter.add(50);
    expect(meter.total).toBe(150);
  });

  it('addSync() accumulates without returning a promise', () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    meter.addSync(200);
    meter.addSync(300);
    expect(meter.total).toBe(500);
  });

  it('add() ignores zero or negative quantities', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(0);
    await meter.add(-5);
    expect(meter.total).toBe(0);
  });

  it('flush() calls chargeFn with accumulated quantity', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(300);
    const result = await meter.flush();

    expect(chargeFn).toHaveBeenCalledOnce();
    const call = chargeFn.mock.calls[0][0];
    expect(call.customerId).toBe('cust_1');
    expect(call.meter).toBe('tokens');
    expect(call.quantity).toBe(300);
    expect(result.quantity).toBe(300);
    expect(result.success).toBe(true);
  });

  it('flush() resets internal counter to 0 after flush', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(100);
    await meter.flush();
    expect(meter.total).toBe(0);
  });

  it('flush() with quantity=0 skips the charge call', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    const result = await meter.flush();

    expect(chargeFn).not.toHaveBeenCalled();
    expect(result.quantity).toBe(0);
    expect(result.charge).toBeNull();
    expect(result.success).toBe(true);
  });

  it('isFlushed becomes true after first successful flush', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    expect(meter.isFlushed).toBe(false);
    await meter.add(10);
    await meter.flush();
    expect(meter.isFlushed).toBe(true);
  });

  it('flushCount increments after each flush', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(10);
    await meter.flush();
    await meter.add(20);
    await meter.flush();
    expect(meter.flushCount).toBe(2);
  });

  it('flushThreshold triggers auto-flush when threshold reached', async () => {
    const meter = new StreamMeter(chargeFn, {
      customerId: 'cust_1',
      meter: 'tokens',
      flushThreshold: 100,
    });
    await meter.add(60);
    expect(chargeFn).not.toHaveBeenCalled();
    await meter.add(50); // total = 110, exceeds threshold
    expect(chargeFn).toHaveBeenCalledOnce();
  });

  it('onAdd callback is invoked with quantity and running total', async () => {
    const onAdd = vi.fn();
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens', onAdd });
    await meter.add(50);
    await meter.add(30);
    expect(onAdd).toHaveBeenCalledTimes(2);
    expect(onAdd).toHaveBeenNthCalledWith(1, 50, 50);
    expect(onAdd).toHaveBeenNthCalledWith(2, 30, 80);
  });

  it('onFlush callback is invoked after successful flush', async () => {
    const onFlush = vi.fn();
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens', onFlush });
    await meter.add(75);
    await meter.flush();
    expect(onFlush).toHaveBeenCalledOnce();
    const result = onFlush.mock.calls[0][0];
    expect(result.quantity).toBe(75);
    expect(result.success).toBe(true);
  });

  it('reset() discards accumulated total without charging', () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    meter.addSync(500);
    meter.reset();
    expect(meter.total).toBe(0);
    expect(chargeFn).not.toHaveBeenCalled();
  });

  it('concurrent flush() calls deduplicate to same promise', async () => {
    const meter = new StreamMeter(chargeFn, { customerId: 'cust_1', meter: 'tokens' });
    await meter.add(100);
    const [r1, r2] = await Promise.all([meter.flush(), meter.flush()]);
    // Only one charge call despite two concurrent flush() invocations
    expect(chargeFn).toHaveBeenCalledOnce();
    expect(r1.quantity).toBe(r2.quantity);
  });
});
