/**
 * Tests for the Drip class — constructor config, key type detection,
 * webhook signature verification, and mocked HTTP methods.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createHmac } from 'crypto';
import { Drip } from './index.js';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeWebhookSignature(payload: string, secret: string, timestamp?: number): string {
  const ts = timestamp ?? Math.floor(Date.now() / 1000);
  const signaturePayload = `${ts}.${payload}`;
  const sig = createHmac('sha256', secret).update(signaturePayload).digest('hex');
  return `t=${ts},v1=${sig}`;
}

function mockFetchOnce(responseBody: unknown, status = 200): void {
  const mockResponse = {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(responseBody),
  };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(mockResponse));
}

// ─── Constructor & key type ───────────────────────────────────────────────────

describe('Drip constructor', () => {
  it('sets keyType to "secret" for sk_ keys', () => {
    const drip = new Drip({ apiKey: 'sk_test_validkey123', resilience: false });
    expect(drip.keyType).toBe('secret');
  });

  it('sets keyType to "public" for pk_ keys', () => {
    const drip = new Drip({ apiKey: 'pk_test_validkey123', resilience: false });
    expect(drip.keyType).toBe('public');
  });

  it('throws if no apiKey and DRIP_API_KEY env not set', () => {
    const original = process.env.DRIP_API_KEY;
    delete process.env.DRIP_API_KEY;
    expect(() => new Drip()).toThrow(/API key is required/);
    if (original !== undefined) process.env.DRIP_API_KEY = original;
  });

  it('uses DRIP_API_KEY env variable as fallback', () => {
    process.env.DRIP_API_KEY = 'sk_test_fromenv1234';
    const drip = new Drip({ resilience: false });
    expect(drip.keyType).toBe('secret');
    delete process.env.DRIP_API_KEY;
  });

  it('throws for API key with invalid prefix', () => {
    expect(() => new Drip({ apiKey: 'invalid_key_format' })).toThrow(/Invalid API key format/);
  });

  it('throws for API key that is too short', () => {
    expect(() => new Drip({ apiKey: 'sk_abc' })).toThrow(/too short/);
  });
});

// ─── verifyWebhookSignatureSync ───────────────────────────────────────────────

describe('Drip.verifyWebhookSignatureSync', () => {
  it('returns true for a valid HMAC-SHA256 signature', () => {
    const payload = JSON.stringify({ event: 'charge.completed', id: 'chg_1' });
    const secret = 'whsec_test_secret_value';
    const sig = makeWebhookSignature(payload, secret);
    expect(Drip.verifyWebhookSignatureSync(payload, sig, secret)).toBe(true);
  });

  it('returns false for tampered payload', () => {
    const payload = JSON.stringify({ event: 'charge.completed' });
    const secret = 'whsec_test_secret_value';
    const sig = makeWebhookSignature(payload, secret);
    const tamperedPayload = JSON.stringify({ event: 'charge.TAMPERED' });
    expect(Drip.verifyWebhookSignatureSync(tamperedPayload, sig, secret)).toBe(false);
  });

  it('returns false for wrong secret', () => {
    const payload = JSON.stringify({ event: 'charge.completed' });
    const sig = makeWebhookSignature(payload, 'correct_secret');
    expect(Drip.verifyWebhookSignatureSync(payload, sig, 'wrong_secret')).toBe(false);
  });

  it('returns false for expired timestamp (beyond tolerance)', () => {
    const payload = 'test-payload';
    const secret = 'whsec_secret';
    const oldTimestamp = Math.floor(Date.now() / 1000) - 600; // 10 min ago
    const sig = makeWebhookSignature(payload, secret, oldTimestamp);
    // Default tolerance is 300s, so 600s old should fail
    expect(Drip.verifyWebhookSignatureSync(payload, sig, secret)).toBe(false);
  });

  it('returns false for missing signature', () => {
    expect(Drip.verifyWebhookSignatureSync('payload', '', 'secret')).toBe(false);
  });

  it('returns false for missing payload', () => {
    expect(Drip.verifyWebhookSignatureSync('', 't=123,v1=abc', 'secret')).toBe(false);
  });

  it('returns false for malformed signature (no t= part)', () => {
    expect(Drip.verifyWebhookSignatureSync('payload', 'v1=abc123', 'secret')).toBe(false);
  });

  it('returns false for malformed signature (no v1= part)', () => {
    expect(Drip.verifyWebhookSignatureSync('payload', 't=1234567890', 'secret')).toBe(false);
  });
});

// ─── verifyWebhookSignature (async) ──────────────────────────────────────────

describe('Drip.verifyWebhookSignature (async)', () => {
  it('returns true for a valid signature', async () => {
    const payload = JSON.stringify({ event: 'usage.tracked' });
    const secret = 'whsec_async_test';
    const sig = makeWebhookSignature(payload, secret);
    expect(await Drip.verifyWebhookSignature(payload, sig, secret)).toBe(true);
  });

  it('returns false for invalid signature', async () => {
    const payload = 'hello world';
    const sig = makeWebhookSignature(payload, 'correct_secret');
    expect(await Drip.verifyWebhookSignature(payload, sig, 'wrong_secret')).toBe(false);
  });
});

// ─── Mocked HTTP: createCustomer ─────────────────────────────────────────────

describe('drip.createCustomer() — mocked fetch', () => {
  let drip: Drip;

  beforeEach(() => {
    drip = new Drip({ apiKey: 'sk_test_validkey1234', resilience: false });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends POST to /customers with correct body', async () => {
    const fakeCustomer = {
      id: 'cust_abc123',
      externalCustomerId: 'user_42',
      onchainAddress: null,
      isInternal: false,
      status: 'ACTIVE',
      metadata: null,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
    };
    mockFetchOnce(fakeCustomer, 200);

    const result = await drip.createCustomer({ externalCustomerId: 'user_42' });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/customers$/);
    expect(options?.method).toBe('POST');
    expect(JSON.parse(options?.body as string)).toEqual({ externalCustomerId: 'user_42' });
    expect(result.id).toBe('cust_abc123');
  });

  it('throws DripError on 4xx response', async () => {
    mockFetchOnce({ message: 'Customer already exists', code: 'CONFLICT' }, 409);
    await expect(drip.createCustomer({ externalCustomerId: 'user_42' })).rejects.toMatchObject({
      name: 'DripError',
      statusCode: 409,
    });
  });
});

// ─── Mocked HTTP: trackUsage ─────────────────────────────────────────────────

describe('drip.trackUsage() — mocked fetch', () => {
  let drip: Drip;

  beforeEach(() => {
    drip = new Drip({ apiKey: 'sk_test_validkey1234', resilience: false });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends POST to /usage with correct customerId, meter, and quantity', async () => {
    const fakeResult = {
      success: true,
      usageEventId: 'evt_001',
      isDuplicate: false,
      charge: { id: 'chg_001', amountUsdc: '0.001000', amountToken: '0.001000', txHash: null, status: 'PENDING' },
    };
    mockFetchOnce(fakeResult, 200);

    await drip.trackUsage({ customerId: 'cust_abc123', meter: 'tokens', quantity: 500 });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/usage$/);
    expect(options?.method).toBe('POST');
    const body = JSON.parse(options?.body as string);
    expect(body.customerId).toBe('cust_abc123');
    expect(body.usageType).toBe('tokens');
    expect(body.quantity).toBe(500);
  });

  it('sends POST to /usage/async for batch mode', async () => {
    mockFetchOnce({ success: true, usageEventId: 'evt_002', queued: true, isDuplicate: false }, 200);

    await drip.trackUsage({ customerId: 'cust_abc123', meter: 'tokens', quantity: 100, mode: 'batch' });

    const fetchMock = vi.mocked(fetch);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/usage\/async$/);
  });
});

// ─── Mocked HTTP: ping ───────────────────────────────────────────────────────

describe('drip.ping() — mocked fetch', () => {
  let drip: Drip;

  beforeEach(() => {
    drip = new Drip({ apiKey: 'sk_test_validkey1234', resilience: false });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends GET to /health (base URL without /v1)', async () => {
    mockFetchOnce({ status: 'ok', timestamp: Date.now() }, 200);

    await drip.ping();

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/health$/);
    // ping() uses a bare GET (no method override — relies on default)
    expect(options?.method).toBeUndefined();
  });

  it('returns ok: true on successful health response with status=healthy', async () => {
    // ping() returns ok: response.ok && status === 'healthy'
    mockFetchOnce({ status: 'healthy', timestamp: Math.floor(Date.now() / 1000) }, 200);
    const result = await drip.ping();
    expect(result.ok).toBe(true);
    expect(result.status).toBe('healthy');
  });
});
