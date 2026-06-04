export interface SendOptions {
  idempotencyKey?: string;
  filter?: string;
  transform?: Record<string, string>;
  orderingKey?: string;
  signatureProvider?: string;
  destinationId?: string;
}

export interface WebhookRecord {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  destination_url: string;
  retry_count: number;
  created_at: string;
  updated_at: string;
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

export interface AuditEntry {
  id: string;
  action: "CREATE" | "UPDATE" | "DELETE" | "REPLAY";
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

export class HermesError extends Error {
  statusCode: number;
  detail: string;
  constructor(statusCode: number, detail: string);
}

export class Hermes {
  constructor(baseUrl: string, options?: { apiKey?: string; timeout?: number });

  send(destinationUrl: string, payload: Record<string, unknown>, options?: SendOptions): Promise<{ id: string; status: string }>;
  fanOut(destinationUrls: string[], payload: Record<string, unknown>, options?: SendOptions): Promise<Array<{ id: string; status: string }>>;

  getWebhook(webhookId: string): Promise<WebhookRecord>;
  listWebhooks(opts?: { status?: string; limit?: number; offset?: number }): Promise<{ items: WebhookRecord[]; total: number }>;
  replayWebhook(webhookId: string): Promise<{ queued: boolean }>;

  listDlq(opts?: { limit?: number; offset?: number }): Promise<{ items: WebhookRecord[] }>;
  replayAllDlq(): Promise<{ replayed: number }>;
  dlqHealth(): Promise<DlqHealth>;

  getStats(): Promise<Stats>;
  getAuditLog(opts?: { resourceType?: string; action?: string; limit?: number; offset?: number }): Promise<AuditLogResponse>;

  listDestinations(): Promise<unknown[]>;
  createDestination(body: { name: string; url: string; [key: string]: unknown }): Promise<unknown>;
  deleteDestination(destinationId: string): Promise<void>;
}
