import { describe, it, expect } from 'vitest';
import {
  calculateBackoff,
  isRetryableError,
  CircuitBreaker,
  DEFAULT_RETRY_CONFIG,
  type RetryConfig,
} from './resilience.js';
import { DripError } from './errors.js';

// ─── calculateBackoff ───────────────────────────────────────────────────────

describe('calculateBackoff', () => {
  const deterministicConfig: RetryConfig = {
    ...DEFAULT_RETRY_CONFIG,
    jitter: 0, // eliminate randomness
  };

  it('returns baseDelayMs * exponentialBase^0 = baseDelayMs for attempt 0', () => {
    const delay = calculateBackoff(0, deterministicConfig);
    expect(delay).toBe(deterministicConfig.baseDelayMs); // 100 * 2^0 = 100
  });

  it('doubles for attempt 1 (100 * 2^1 = 200)', () => {
    const delay = calculateBackoff(1, deterministicConfig);
    expect(delay).toBe(200);
  });

  it('quadruples for attempt 2 (100 * 2^2 = 400)', () => {
    const delay = calculateBackoff(2, deterministicConfig);
    expect(delay).toBe(400);
  });

  it('caps at maxDelayMs', () => {
    const delay = calculateBackoff(99, deterministicConfig);
    expect(delay).toBe(deterministicConfig.maxDelayMs);
  });

  it('never returns a negative value', () => {
    const delay = calculateBackoff(0, { ...deterministicConfig, baseDelayMs: 0 });
    expect(delay).toBeGreaterThanOrEqual(0);
  });

  it('adds jitter within expected range when jitter > 0', () => {
    const config: RetryConfig = { ...DEFAULT_RETRY_CONFIG, jitter: 0.1 };
    const base = config.baseDelayMs * Math.pow(config.exponentialBase, 1); // 200
    const jitterRange = base * config.jitter; // 20
    // Run 20 samples — all must be within [base - jitterRange, base + jitterRange]
    for (let i = 0; i < 20; i++) {
      const delay = calculateBackoff(1, config);
      expect(delay).toBeGreaterThanOrEqual(base - jitterRange);
      expect(delay).toBeLessThanOrEqual(base + jitterRange);
    }
  });
});

// ─── isRetryableError ────────────────────────────────────────────────────────

describe('isRetryableError', () => {
  it('returns true for DripError with 500 status', () => {
    const err = new DripError('Server error', 500);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(true);
  });

  it('returns true for DripError with 429 (rate limit)', () => {
    const err = new DripError('Too Many Requests', 429);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(true);
  });

  it('returns true for DripError with 503', () => {
    const err = new DripError('Service Unavailable', 503);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(true);
  });

  it('returns false for DripError with 404', () => {
    const err = new DripError('Not found', 404);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(false);
  });

  it('returns false for DripError with 400', () => {
    const err = new DripError('Bad request', 400);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(false);
  });

  it('returns false for DripError with 401', () => {
    const err = new DripError('Unauthorized', 401);
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(false);
  });

  it('returns true for generic fetch/network errors', () => {
    const err = new Error('fetch failed: ECONNREFUSED');
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(true);
  });

  it('returns true for ETIMEDOUT errors', () => {
    const err = new Error('ETIMEDOUT connection');
    expect(isRetryableError(err, DEFAULT_RETRY_CONFIG)).toBe(true);
  });

  it('returns false for non-Error values', () => {
    expect(isRetryableError('string error', DEFAULT_RETRY_CONFIG)).toBe(false);
    expect(isRetryableError(null, DEFAULT_RETRY_CONFIG)).toBe(false);
    expect(isRetryableError(42, DEFAULT_RETRY_CONFIG)).toBe(false);
  });
});

// ─── CircuitBreaker ──────────────────────────────────────────────────────────

describe('CircuitBreaker', () => {
  it('starts in closed state', () => {
    const cb = new CircuitBreaker('test');
    expect(cb.getState()).toBe('closed');
  });

  it('allows requests when closed', () => {
    const cb = new CircuitBreaker('test');
    expect(cb.allowRequest()).toBe(true);
  });

  it('opens after failureThreshold failures', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 3, timeoutMs: 30000, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.getState()).toBe('closed');
    cb.recordFailure(); // 3rd failure → open
    expect(cb.getState()).toBe('open');
  });

  it('blocks requests when open', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 30000, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    expect(cb.getState()).toBe('open');
    expect(cb.allowRequest()).toBe(false);
  });

  it('transitions to half_open after timeoutMs', () => {
    // With timeoutMs=0, the very first getState() call after recordFailure()
    // already triggers the open→half_open transition (elapsed >= 0).
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 0, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    // elapsed is already >= 0ms when we check → immediately half_open
    expect(cb.getState()).toBe('half_open');
  });

  it('allows requests in half_open state', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 0, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    expect(cb.getState()).toBe('half_open');
    expect(cb.allowRequest()).toBe(true);
  });

  it('closes after successThreshold successes in half_open', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 0, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    // timeoutMs=0 → already half_open on first getState()
    expect(cb.getState()).toBe('half_open');
    cb.recordSuccess();
    expect(cb.getState()).toBe('half_open'); // still half_open after 1
    cb.recordSuccess();
    expect(cb.getState()).toBe('closed'); // closed after 2
  });

  it('stays open and blocks requests when timeout has not elapsed', () => {
    // Use a long timeout so the circuit stays open and doesn't transition to half_open.
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 60000, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    expect(cb.getState()).toBe('open');
    expect(cb.allowRequest()).toBe(false);
  });

  it('in half_open, failure resets successCount so more successes are needed', () => {
    // Verify: after a half_open→open→half_open cycle (timeoutMs=0),
    // previously accumulated successes are cleared.
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 0, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    cb.getState(); // → half_open
    cb.recordSuccess(); // successCount=1 (need 2 to close)
    cb.recordFailure(); // → open; successCount reset
    // getState() → half_open again (timeoutMs=0)
    expect(cb.getState()).toBe('half_open');
    // Now need full successThreshold again — 1 success not enough
    cb.recordSuccess(); // successCount=1 again
    expect(cb.getState()).toBe('half_open'); // not yet closed
    cb.recordSuccess(); // successCount=2 → closed
    expect(cb.getState()).toBe('closed');
  });

  it('reset() returns circuit to closed state', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 30000, enabled: true, successThreshold: 2 });
    cb.recordFailure();
    expect(cb.getState()).toBe('open');
    cb.reset();
    expect(cb.getState()).toBe('closed');
    expect(cb.allowRequest()).toBe(true);
  });

  it('when disabled, always allows requests regardless of failures', () => {
    const cb = new CircuitBreaker('test', { failureThreshold: 1, timeoutMs: 30000, enabled: false, successThreshold: 2 });
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.allowRequest()).toBe(true);
  });
});
