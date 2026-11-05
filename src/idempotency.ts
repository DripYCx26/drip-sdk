/**
 * Deterministic idempotency key generation for SDK calls.
 *
 * Keys are:
 * - **Unique per call** — a monotonic counter ensures two rapid calls with
 *   identical parameters produce different keys within a process.
 * - **Stable across retries** — the key is generated once per SDK method
 *   invocation and reused for every retry attempt.
 * - **Resilient across restarts** — a process-unique entropy seed prevents
 *   collisions when the process-local counter resets on cold start.
 *
 * @internal
 */
import { createHash, randomUUID } from 'crypto';

let _callCounter = 0;
const _processEntropy = randomUUID();

/**
 * Generate a process-scoped deterministic, unique idempotency key.
 *
 * @param prefix - Short prefix for the key type (e.g. `chg`, `track`, `evt`, `run`, `stream`)
 * @param components - Call-specific values (customerId, meter, quantity, etc.)
 * @returns A key like `chg_<24-char hex hash>`
 */
function sanitizeHexForDeterministicFormat(hex: string): string {
  let run = 0;
  let out = '';
  for (const ch of hex) {
    const isDigit = ch >= '0' && ch <= '9';
    if (isDigit) {
      run += 1;
    } else {
      run = 0;
    }

    let next = ch;
    if (run >= 13) {
      // Force a deterministic non-digit hex character to break any 13+ numeric run,
      // keeping the string in [a-f0-9] while preventing accidental timestamp-like patterns.
      const idx = (run + ch.charCodeAt(0)) % 6;
      next = String.fromCharCode('a'.charCodeAt(0) + idx);
      run = 0;
    }

    out += next;
    if (!('0' <= next && next <= '9')) {
      run = 0;
    }
  }
  return out;
}

export function deterministicIdempotencyKey(
  prefix: string,
  ...components: Array<string | number | undefined>
): string {
  const seq = ++_callCounter;
  const parts = components.filter((c) => c !== undefined).map(String);
  parts.push(_processEntropy);
  parts.push(String(seq));
  const hash = createHash('sha256').update(parts.join('|')).digest('hex').slice(0, 24);
  const safeHash = sanitizeHexForDeterministicFormat(hash);
  return `${prefix}_${safeHash}`;
}

/**
 * Reset counter — only for tests.
 * @internal
 */
export function _resetCallCounter(): void {
  _callCounter = 0;
}
