import { describe, it, expect, vi, afterEach } from 'vitest';
import { Drip } from '../src/index.js';
import { Drip as CoreDrip } from '../src/core.js';

function stubFetch(...responses: Array<{ status: number; body: unknown }>) {
  const fetchMock = vi.fn(async () => {
    const r = responses.length > 1 ? responses.shift()! : responses[0];
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Drip SDK Basic Tests', () => {
  it('should create Drip instance', () => {
    const drip = new Drip({ apiKey: 'sk_test_dummy_key' });
    expect(drip).toBeDefined();
  });

  it('should have keyType property', () => {
    const drip = new Drip({ apiKey: 'sk_test_dummy_key' });
    expect(drip.keyType).toBe('secret');
  });

  it('should detect public key', () => {
    const drip = new Drip({ apiKey: 'pk_test_dummy_key' });
    expect(drip.keyType).toBe('public');
  });
});

describe('Drip extends DripCore', () => {
  it('routes inherited methods through the full SDK request pipeline', async () => {
    const fetchMock = stubFetch({ status: 200, body: { id: 'run_1' } });
    const drip = new Drip({ apiKey: 'sk_test_dummy_key', baseUrl: 'https://x.test', resilience: false });

    await expect(drip.getRun('run_1')).resolves.toEqual({ id: 'run_1' });
    expect(fetchMock).toHaveBeenCalledWith(
      'https://x.test/runs/run_1',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer sk_test_dummy_key' }) }),
    );
  });

  it('uses the full SDK secret-key message for inherited methods', async () => {
    const drip = new Drip({ apiKey: 'pk_test_dummy_key', resilience: false });
    await expect(drip.getCustomerSpendingCaps('cust_1')).rejects.toThrow(
      /pricing, subscription, and webhook operations/,
    );
  });

  it('keeps distinct trackUsage endpoints per entry point', async () => {
    const fetchMock = stubFetch({ status: 200, body: { success: true } });
    const params = { customerId: 'cust_1', meter: 'api_calls', quantity: 1 };

    await new Drip({ apiKey: 'sk_test_dummy_key', baseUrl: 'https://x.test', resilience: false }).trackUsage(params);
    await new CoreDrip({ apiKey: 'sk_test_dummy_key', baseUrl: 'https://x.test' }).trackUsage(params);

    expect(fetchMock.mock.calls.map((c) => (c as unknown[])[0])).toEqual([
      'https://x.test/usage',
      'https://x.test/usage/internal',
    ]);
  });
});

describe('wrapApiCall retry', () => {
  const run = (drip: Drip) =>
    drip.wrapApiCall({
      customerId: 'cust_1',
      meter: 'api_calls',
      call: async () => 'ok',
      extractUsage: () => 1,
      retryOptions: { maxAttempts: 3, baseDelayMs: 1, maxDelayMs: 2 },
    });

  it.each([408, 429, 501])('retries a %i response', async (status) => {
    const fetchMock = stubFetch({ status, body: { message: 'retry me' } }, { status: 200, body: { success: true } });
    const result = await run(new Drip({ apiKey: 'sk_test_dummy_key', resilience: false }));
    expect(result.result).toBe('ok');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retry a 400 response', async () => {
    const fetchMock = stubFetch({ status: 400, body: { message: 'bad' } });
    await expect(run(new Drip({ apiKey: 'sk_test_dummy_key', resilience: false }))).rejects.toThrow('bad');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
