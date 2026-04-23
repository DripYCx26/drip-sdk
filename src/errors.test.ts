import { describe, it, expect } from 'vitest';
import { DripError } from './errors.js';

describe('DripError', () => {
  it('creates with correct message and statusCode', () => {
    const err = new DripError('Not found', 404);
    expect(err.message).toBe('Not found');
    expect(err.statusCode).toBe(404);
  });

  it('is an instanceof Error', () => {
    const err = new DripError('Oops', 500);
    expect(err).toBeInstanceOf(Error);
  });

  it('is an instanceof DripError', () => {
    const err = new DripError('Oops', 500);
    expect(err).toBeInstanceOf(DripError);
  });

  it('has name = "DripError"', () => {
    const err = new DripError('Bad request', 400);
    expect(err.name).toBe('DripError');
  });

  it('carries optional code field', () => {
    const err = new DripError('Payment required', 402, 'PAYMENT_REQUIRED');
    expect(err.code).toBe('PAYMENT_REQUIRED');
  });

  it('code is undefined when not provided', () => {
    const err = new DripError('Server error', 500);
    expect(err.code).toBeUndefined();
  });

  it('carries optional data payload', () => {
    const data = { detail: 'wallet not funded', chain: 'solana' };
    const err = new DripError('Payment required', 402, 'INSUFFICIENT_FUNDS', data);
    expect(err.data).toEqual(data);
  });

  it('data is undefined when not provided', () => {
    const err = new DripError('Unauthorized', 401, 'UNAUTHORIZED');
    expect(err.data).toBeUndefined();
  });

  it('instanceof check works across re-thrown boundaries', () => {
    function throwDripError(): never {
      throw new DripError('Rate limited', 429, 'RATE_LIMITED');
    }

    let caught: unknown;
    try {
      throwDripError();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(DripError);
    expect((caught as DripError).statusCode).toBe(429);
  });

  it('has a stack trace', () => {
    const err = new DripError('Error with stack', 500);
    expect(err.stack).toBeDefined();
    expect(typeof err.stack).toBe('string');
  });
});
