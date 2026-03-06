/**
 * Error thrown by Drip SDK operations.
 */
export class DripError extends Error {
  /**
   * Creates a new DripError.
   * @param message - Human-readable error message
   * @param statusCode - HTTP status code from the API
   * @param code - Machine-readable error code
   * @param data - Full response body from the API (preserved for 402 x402 payment flow, etc.)
   */
  constructor(
    message: string,
    public statusCode: number,
    public code?: string,
    public data?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'DripError';
    Object.setPrototypeOf(this, DripError.prototype);
  }
}
