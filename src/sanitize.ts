/**
 * Shared metadata sanitization utilities.
 *
 * Used by middleware, LangChain integration, and OpenClaw integration
 * to enforce consistent metadata hygiene across all SDK surfaces.
 *
 * @internal
 */

/**
 * Pattern matching sensitive metadata keys that should be redacted.
 */
export const SENSITIVE_METADATA_KEY_PATTERN =
  /(authorization|api[_-]?key|secret|password|token|prompt|completion|output|input|request|response|body|cookie|set-cookie|email|phone|ssn|address|creditcard|card)/i;

/**
 * Check if a value is a metadata-safe primitive.
 */
export function isMetadataPrimitive(value: unknown): value is string | number | boolean | null {
  if (value === null) return true;
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

/**
 * Default maximum string length for metadata values.
 */
export const DEFAULT_METADATA_MAX_STRING_LENGTH = 256;

/**
 * Sanitize a metadata object by filtering to allowed keys, redacting
 * sensitive keys, and removing non-primitive values.
 *
 * @param metadata - Raw metadata to sanitize
 * @param options - Sanitization options
 * @returns Sanitized metadata with only safe primitive values
 */
export function sanitizeMetadata(
  metadata: Record<string, unknown> | undefined,
  options: {
    /** If provided, only these keys are allowed through. */
    allowlist?: ReadonlySet<string> | null;
    /** Keys to force-redact (case-insensitive matching). */
    redactKeys?: ReadonlySet<string>;
    /** Max length for string values before truncation. */
    maxStringLength?: number;
  } = {},
): Record<string, unknown> {
  if (!metadata) return {};

  const allowlist = options.allowlist ?? null;
  const redactKeys = options.redactKeys ?? new Set<string>();
  const maxStringLength = options.maxStringLength ?? DEFAULT_METADATA_MAX_STRING_LENGTH;

  const sanitized: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(metadata)) {
    const normalizedKey = key.toLowerCase();

    if (allowlist && !allowlist.has(key) && !allowlist.has(normalizedKey)) continue;
    if (redactKeys.has(key) || redactKeys.has(normalizedKey)) continue;
    if (SENSITIVE_METADATA_KEY_PATTERN.test(key)) continue;
    if (!isMetadataPrimitive(value)) continue;
    if (typeof value === 'number' && !Number.isFinite(value)) continue;
    if (typeof value === 'string' && value.length > maxStringLength) {
      sanitized[key] = value.slice(0, maxStringLength);
      continue;
    }

    sanitized[key] = value;
  }

  return sanitized;
}
