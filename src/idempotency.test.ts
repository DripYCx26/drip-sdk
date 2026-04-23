import { describe, it, expect, beforeEach } from 'vitest';
import { deterministicIdempotencyKey, _resetCallCounter } from './idempotency.js';

beforeEach(() => {
  _resetCallCounter();
});

describe('deterministicIdempotencyKey', () => {
  it('produces a string with the given prefix', () => {
    const key = deterministicIdempotencyKey('chg', 'cust_123', 'tokens', 100);
    expect(key).toMatch(/^chg_/);
  });

  it('same inputs produce different keys due to monotonic counter', () => {
    // Two calls with identical args must differ (counter bumps each call)
    const k1 = deterministicIdempotencyKey('chg', 'cust_123', 'tokens', 100);
    const k2 = deterministicIdempotencyKey('chg', 'cust_123', 'tokens', 100);
    expect(k1).not.toBe(k2);
  });

  it('different prefixes produce different keys', () => {
    _resetCallCounter();
    const k1 = deterministicIdempotencyKey('chg', 'cust_123');
    _resetCallCounter();
    const k2 = deterministicIdempotencyKey('track', 'cust_123');
    expect(k1).not.toBe(k2);
    expect(k1).toMatch(/^chg_/);
    expect(k2).toMatch(/^track_/);
  });

  it('different components produce different keys (same counter reset)', () => {
    _resetCallCounter();
    const k1 = deterministicIdempotencyKey('chg', 'cust_aaa', 'tokens', 10);
    _resetCallCounter();
    const k2 = deterministicIdempotencyKey('chg', 'cust_bbb', 'tokens', 10);
    expect(k1).not.toBe(k2);
  });

  it('output is a valid hex hash segment (only [a-f0-9_] chars)', () => {
    const key = deterministicIdempotencyKey('stream', 'cust_xyz', 'calls', 5);
    // format: prefix_<24-char hex-like string>
    const parts = key.split('_');
    // prefix may itself contain underscores but last segment is the hash
    const hash = parts[parts.length - 1];
    expect(hash).toMatch(/^[a-f0-9]{24}$/);
  });

  it('counter increments across calls producing unique keys', () => {
    const keys = Array.from({ length: 10 }, () =>
      deterministicIdempotencyKey('evt', 'cust_1', 'meter', 1)
    );
    const unique = new Set(keys);
    expect(unique.size).toBe(10);
  });

  it('handles undefined components gracefully', () => {
    const key = deterministicIdempotencyKey('run', undefined, 'meter', undefined);
    expect(key).toMatch(/^run_[a-f0-9]{24}$/);
  });
});
