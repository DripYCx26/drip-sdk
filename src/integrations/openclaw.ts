import { createHash, randomUUID } from 'node:crypto';

type HttpMethod = 'POST' | 'PATCH';

export type OpenClawRunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';
export type OpenClawEventOutcome = 'SUCCEEDED' | 'FAILED' | 'PENDING' | 'TIMEOUT' | 'CANCELLED';

const DEFAULT_EVENT_TYPE = 'TOOL_CALL';
const DEFAULT_USAGE_ENDPOINT = '/v1/usage';
const DEFAULT_USAGE_TYPE = 'api_calls';

const DEFAULT_ACTION_NAMES: Record<string, string> = {
  brave: 'brave_search',
  google: 'google_search',
};

interface RunResponse {
  id: string;
}

interface EventResponse {
  id: string;
}

interface UsageResponse {
  id?: string;
  usageEventId?: string;
  usageEvent?: {
    id?: string;
  };
}

interface RequestOptions {
  path: string;
  method: HttpMethod;
  body: Record<string, unknown>;
}

class DripApiError extends Error {
  readonly statusCode: number;

  constructor(statusCode: number, message: string) {
    super(message);
    this.name = 'DripApiError';
    this.statusCode = statusCode;
  }
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return 'Unknown error';
}

function hashQuery(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

function sanitizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
}

function isTelemetryPrimitive(value: unknown): value is string | number | boolean | null {
  if (value === null) {
    return true;
  }
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

function sanitizeTelemetryMetadata(
  metadata: Record<string, unknown> | undefined,
): Record<string, unknown> {
  if (!metadata) {
    return {};
  }

  const SENSITIVE_KEY_PATTERN =
    /(authorization|api[_-]?key|secret|password|token|prompt|completion|output|input|request|response|body|cookie|set-cookie)/i;

  const sanitized: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(metadata)) {
    if (SENSITIVE_KEY_PATTERN.test(key)) {
      continue;
    }

    if (!isTelemetryPrimitive(value)) {
      continue;
    }

    if (typeof value === 'number' && !Number.isFinite(value)) {
      continue;
    }

    if (typeof value === 'string' && value.length > 256) {
      sanitized[key] = value.slice(0, 256);
      continue;
    }

    sanitized[key] = value;
  }

  return sanitized;
}

export interface OpenClawBillingOptions {
  apiKey?: string;
  baseUrl?: string;
  customerId?: string;
  workflowId?: string;
  usageEndpoint?: '/v1/usage' | '/v1/usage/async' | '/v1/usage/internal' | '/v1/usage/internal/batch';
  usageType?: string;
  metadata?: Record<string, unknown>;
}

export interface OpenClawRunStartParams {
  customerId?: string;
  workflowId?: string;
  externalRunId?: string;
  correlationId?: string;
  metadata?: Record<string, unknown>;
}

export interface OpenClawRunContext {
  runId: string;
  customerId: string;
  workflowId: string;
}

export interface OpenClawRunEndParams {
  runId: string;
  status: OpenClawRunStatus;
  errorMessage?: string;
  errorCode?: string;
  metadata?: Record<string, unknown>;
}

export interface OpenClawToolMeta {
  statusCode?: number;
  quantity?: number;
  tokens?: number;
  metadata?: Record<string, unknown>;
}

export interface OpenClawToolCallParams<T> {
  runId: string;
  provider: string;
  endpoint: string;
  customerId?: string;
  actionName?: string;
  eventType?: string;
  usageType?: string;
  quantity?: number;
  query?: string;
  idempotencyKey?: string;
  billUsage?: boolean;
  metadata?: Record<string, unknown>;
  onSuccess?: (result: T) => OpenClawToolMeta | void;
  onError?: (error: unknown) => OpenClawToolMeta | void;
}

export interface OpenClawToolExecutionReceipt {
  eventId: string;
  usageEventId?: string;
}

interface EmitToolExecutionParams {
  runId: string;
  provider: string;
  endpoint: string;
  customerId?: string;
  actionName?: string;
  eventType?: string;
  usageType?: string;
  quantity?: number;
  query?: string;
  idempotencyKey?: string;
  billUsage?: boolean;
  metadata?: Record<string, unknown>;
  statusCode?: number;
  latencyMs: number;
  tokens?: number;
  outcome: OpenClawEventOutcome;
}

/**
 * OpenClaw billing helper for Drip.
 *
 * Pattern:
 * 1) Start run (`POST /v1/runs`)
 * 2) Wrap each external tool/API call and emit events (`POST /v1/events`)
 * 3) Optionally emit billable usage (`POST /v1/usage` or async/internal)
 * 4) End run (`PATCH /v1/runs/:id`)
 */
export class OpenClawBilling {
  private readonly _apiKey: string;
  private readonly _baseUrl: string;
  private readonly _defaultCustomerId?: string;
  private readonly _defaultWorkflowId?: string;
  private readonly _usageEndpoint: '/v1/usage' | '/v1/usage/async' | '/v1/usage/internal' | '/v1/usage/internal/batch';
  private readonly _defaultUsageType: string;
  private readonly _baseMetadata: Record<string, unknown>;

  constructor(options: OpenClawBillingOptions = {}) {
    this._apiKey = options.apiKey ?? process.env.DRIP_API_KEY ?? '';
    if (this._apiKey.length === 0) {
      throw new Error('OpenClawBilling requires options.apiKey or DRIP_API_KEY');
    }

    const configuredBaseUrl = options.baseUrl ?? process.env.DRIP_BASE_URL;
    if (!configuredBaseUrl) {
      throw new Error('OpenClawBilling requires options.baseUrl or DRIP_BASE_URL');
    }

    this._baseUrl = sanitizeBaseUrl(configuredBaseUrl);
    this._defaultCustomerId = options.customerId;
    this._defaultWorkflowId = options.workflowId;
    this._usageEndpoint = options.usageEndpoint ?? DEFAULT_USAGE_ENDPOINT;
    this._defaultUsageType = options.usageType ?? DEFAULT_USAGE_TYPE;
    this._baseMetadata = sanitizeTelemetryMetadata(options.metadata);
  }

  async startRun(params: OpenClawRunStartParams = {}): Promise<OpenClawRunContext> {
    const customerId = params.customerId ?? this._defaultCustomerId;
    const workflowId = params.workflowId ?? this._defaultWorkflowId;

    if (!customerId) {
      throw new Error('OpenClaw run start requires customerId');
    }

    if (!workflowId) {
      throw new Error('OpenClaw run start requires workflowId');
    }

    const response = await this.request<RunResponse>({
      path: '/v1/runs',
      method: 'POST',
      body: {
        customerId,
        workflowId,
        externalRunId: params.externalRunId,
        correlationId: params.correlationId,
        metadata: sanitizeTelemetryMetadata({
          ...this._baseMetadata,
          ...(params.metadata ?? {}),
          integration: 'openclaw',
        }),
      },
    });

    return {
      runId: response.id,
      customerId,
      workflowId,
    };
  }

  async endRun(params: OpenClawRunEndParams): Promise<void> {
    await this.request<Record<string, unknown>>({
      path: `/v1/runs/${params.runId}`,
      method: 'PATCH',
      body: {
        status: params.status,
        errorMessage: params.errorMessage,
        errorCode: params.errorCode,
        metadata: sanitizeTelemetryMetadata({
          ...this._baseMetadata,
          ...(params.metadata ?? {}),
          integration: 'openclaw',
        }),
      },
    });
  }

  async withRun<T>(
    params: OpenClawRunStartParams,
    execute: (context: OpenClawRunContext) => Promise<T>,
  ): Promise<T> {
    const run = await this.startRun(params);

    try {
      const result = await execute(run);
      await this.endRun({ runId: run.runId, status: 'COMPLETED' });
      return result;
    } catch (error) {
      try {
        await this.endRun({
          runId: run.runId,
          status: 'FAILED',
          errorMessage: getErrorMessage(error),
        });
      } catch {
        // Preserve the original execution error.
      }
      throw error;
    }
  }

  async withToolCall<T>(
    params: OpenClawToolCallParams<T>,
    execute: () => Promise<T>,
  ): Promise<T> {
    const startTime = Date.now();

    try {
      const result = await execute();
      const successMeta = params.onSuccess?.(result);

      await this.emitToolExecution({
        runId: params.runId,
        provider: params.provider,
        endpoint: params.endpoint,
        customerId: params.customerId,
        actionName: params.actionName,
        eventType: params.eventType,
        usageType: params.usageType,
        quantity: successMeta?.quantity ?? params.quantity,
        query: params.query,
        idempotencyKey: params.idempotencyKey,
        billUsage: params.billUsage,
        metadata: {
          ...(params.metadata ?? {}),
          ...(successMeta?.metadata ?? {}),
        },
        statusCode: successMeta?.statusCode,
        latencyMs: Date.now() - startTime,
        tokens: successMeta?.tokens,
        outcome: 'SUCCEEDED',
      });

      return result;
    } catch (error) {
      const errorMeta = params.onError?.(error);

      await this.emitToolExecution({
        runId: params.runId,
        provider: params.provider,
        endpoint: params.endpoint,
        customerId: params.customerId,
        actionName: params.actionName,
        eventType: params.eventType,
        usageType: params.usageType,
        quantity: errorMeta?.quantity ?? params.quantity,
        query: params.query,
        idempotencyKey: params.idempotencyKey,
        billUsage: params.billUsage,
        metadata: {
          ...(params.metadata ?? {}),
          ...(errorMeta?.metadata ?? {}),
          error: getErrorMessage(error),
        },
        statusCode: errorMeta?.statusCode,
        latencyMs: Date.now() - startTime,
        tokens: errorMeta?.tokens,
        outcome: 'FAILED',
      });

      throw error;
    }
  }

  private async emitToolExecution(
    params: EmitToolExecutionParams,
  ): Promise<OpenClawToolExecutionReceipt> {
    const customerId = params.customerId ?? this._defaultCustomerId;
    if (!customerId) {
      throw new Error('OpenClaw tool execution requires customerId');
    }

    const provider = params.provider.toLowerCase();
    const actionName =
      params.actionName ??
      DEFAULT_ACTION_NAMES[provider] ??
      `${provider}_call`;

    const quantity = params.quantity ?? 1;
    const idempotencyBase =
      params.idempotencyKey ??
      `openclaw_${params.runId}_${actionName}_${randomUUID()}`;

    const queryHash = params.query ? hashQuery(params.query) : undefined;

    const metadata: Record<string, unknown> = {
      ...this._baseMetadata,
      ...(params.metadata ?? {}),
      integration: 'openclaw',
      provider,
      endpoint: params.endpoint.split('?')[0],
      statusCode: params.statusCode ?? null,
      latencyMs: params.latencyMs,
      queryHash,
    };

    const sanitizedMetadata = sanitizeTelemetryMetadata(metadata);

    const event = await this.request<EventResponse>({
      path: '/v1/events',
      method: 'POST',
      body: {
        customerId,
        runId: params.runId,
        actionName,
        eventType: params.eventType ?? DEFAULT_EVENT_TYPE,
        outcome: params.outcome,
        quantity,
        metadata: sanitizedMetadata,
        idempotencyKey: `${idempotencyBase}:event`,
      },
    });

    if (params.billUsage === false) {
      return { eventId: event.id };
    }

    const usageType =
      params.usageType ??
      (provider.length > 0 ? `${provider}_api_calls` : this._defaultUsageType);

    const usage = await this.request<UsageResponse>({
      path: this._usageEndpoint,
      method: 'POST',
      body: {
        customerId,
        usageType,
        quantity,
        idempotencyKey: `${idempotencyBase}:usage`,
        metadata: sanitizedMetadata,
      },
    });

    const usageEventId = usage.usageEventId ?? usage.usageEvent?.id ?? usage.id;

    return {
      eventId: event.id,
      usageEventId,
    };
  }

  private async request<T>(options: RequestOptions): Promise<T> {
    const response = await fetch(`${this._baseUrl}${options.path}`, {
      method: options.method,
      headers: {
        Authorization: `Bearer ${this._apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(options.body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new DripApiError(
        response.status,
        `Drip API request failed: ${response.status} ${errorText}`,
      );
    }

    if (response.status === 204) {
      return {} as T;
    }

    const text = await response.text();
    if (text.length === 0) {
      return {} as T;
    }

    return JSON.parse(text) as T;
  }
}
