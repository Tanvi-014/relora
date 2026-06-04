export interface SendOptions {
  idempotencyKey?: string;
  filter?: string;
  transform?: Record<string, string>;
  orderingKey?: string;
  signatureProvider?: string;
  destinationId?: string;
  extraHeaders?: Record<string, string>;
}

export interface FanOutResult {
  /** Present on success; null on failure. */
  id: string | null;
  /** Present on success. */
  status?: string;
  webhook_id?: string;
  /** Present on failure — the destination URL that errored. */
  url?: string;
  /** Present on failure — human-readable error string. */
  error?: string;
  [key: string]: unknown;
}

export interface WebhookRecord {
  id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  destination_url: string;
  retry_count: number;
  created_at: string;
  updated_at: string;
  attempts?: DeliveryAttempt[];
  [key: string]: unknown;
}

export interface DeliveryAttempt {
  id: string;
  webhook_id: string;
  attempt_number: number;
  status_code: number | null;
  error_message: string | null;
  duration_ms: number | null;
  attempted_at: string;
}

export interface Destination {
  id: string;
  project_id: string;
  name: string;
  url: string;
  description: string | null;
  is_enabled: boolean;
  max_retries: number;
  backoff_base_seconds: number;
  filter_expression: string | null;
  transform_type: string;
  custom_headers: Record<string, string>;
  circuit_state: 'closed' | 'open' | 'half_open';
  circuit_failure_count: number;
  slo_target_pct: number | null;
  slo_window_minutes: number;
  created_at: string;
  updated_at: string;
}

export interface DestinationCreate {
  name: string;
  url: string;
  description?: string;
  is_enabled?: boolean;
  max_retries?: number;
  backoff_base_seconds?: number;
  filter_expression?: string;
  transform_type?: string;
  webhook_secret?: string;
  custom_headers?: Record<string, string>;
  slo_target_pct?: number;
  slo_window_minutes?: number;
  [key: string]: unknown;
}

export interface EventType {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  schema: Record<string, unknown> | null;
  example_payload: Record<string, unknown> | null;
  version: string;
  deprecated: boolean;
  created_at: string;
}

export interface AlertConfig {
  id: string;
  tenant_id: string;
  name: string;
  channel_type: 'slack' | 'email';
  config: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: string;
  action: 'CREATE' | 'UPDATE' | 'DELETE' | 'REPLAY';
  resource_type: string;
  resource_id: string | null;
  changes: { before?: Record<string, unknown>; after?: Record<string, unknown> } | null;
  ip_address: string | null;
  created_at: string;
}

export interface AuditLogResponse {
  entries: AuditEntry[];
  limit: number;
  offset: number;
}

export interface DlqHealth {
  health_score: number;
  status: string;
  recommendations?: string[];
  [key: string]: unknown;
}

export interface Stats {
  total_webhooks: number;
  pending_count: number;
  processing_count: number;
  completed_count: number;
  failed_count: number;
  success_rate: number;
}

export class ReloraError extends Error {
  statusCode: number;
  detail: string;
  constructor(statusCode: number, detail: string);
}

export class Relora {
  constructor(baseUrl: string, options?: { apiKey?: string; timeout?: number; projectId?: string });

  send(destinationUrl: string, payload: Record<string, unknown>, options?: SendOptions): Promise<{ webhook_id: string; status: string; [key: string]: unknown }>;
  fanOut(destinationUrls: string[], payload: Record<string, unknown>, options?: SendOptions): Promise<FanOutResult[]>;

  getWebhook(webhookId: string): Promise<WebhookRecord>;
  listWebhooks(opts?: { status?: string; limit?: number; offset?: number }): Promise<{ webhooks: WebhookRecord[]; total: number; page: number; limit: number }>;
  replayWebhook(webhookId: string): Promise<{ success: boolean; message: string }>;

  listDlq(opts?: { limit?: number; offset?: number }): Promise<{ items: WebhookRecord[] }>;
  replayAllDlq(): Promise<{ replayed: number }>;
  dlqHealth(): Promise<DlqHealth>;

  getStats(): Promise<Stats>;
  getAuditLog(opts?: { resourceType?: string; action?: string; limit?: number; offset?: number }): Promise<AuditLogResponse>;

  listDestinations(): Promise<Destination[]>;
  getDestination(destinationId: string): Promise<Destination>;
  createDestination(name: string, url: string, options?: Omit<DestinationCreate, 'name' | 'url'>): Promise<Destination>;
  updateDestination(destinationId: string, body: Partial<DestinationCreate>): Promise<Destination>;
  deleteDestination(destinationId: string): Promise<void>;

  listEventTypes(): Promise<EventType[]>;
  createEventType(name: string, options?: { description?: string; schema?: Record<string, unknown>; example_payload?: Record<string, unknown>; version?: string }): Promise<EventType>;
  deleteEventType(eventTypeId: string): Promise<void>;

  listAlerts(): Promise<AlertConfig[]>;
  createAlert(name: string, channelType: 'slack' | 'email', config: Record<string, unknown>, options?: Record<string, unknown>): Promise<AlertConfig>;
  deleteAlert(alertId: string): Promise<void>;
}
