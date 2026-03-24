import { describe, it, expect } from 'vitest';
import { Drip } from '../src/index.js';

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
