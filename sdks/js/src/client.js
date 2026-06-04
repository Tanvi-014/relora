/**
 * Relora — JavaScript SDK
 * Compatible with Node.js 18+, browsers, Deno, and Cloudflare Workers.
 * Zero external dependencies — uses the platform fetch API.
 */

export class ReloraError extends Error {
  /** @param {number} statusCode @param {string} detail */
  constructor(statusCode, detail) {
    super(`HTTP ${statusCode}: ${detail}`);
    this.statusCode = statusCode;
    this.detail = detail;
    this.name = 'ReloraError';
  }
}

export class Relora {
  /**
   * @param {string} baseUrl - Relora instance URL, e.g. "http://localhost:8000"
   * @param {{ apiKey?: string, timeout?: number, projectId?: string }} [options]
   */
  constructor(baseUrl, options = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.apiKey = options.apiKey ?? null;
    this.timeout = options.timeout ?? 15_000;
    this.projectId = options.projectId ?? null;
  }

  /** @returns {Record<string, string>} */
  _headers(extra = {}) {
    const h = { 'Content-Type': 'application/json', Accept: 'application/json' };
    if (this.apiKey) h['X-Relora-API-Key'] = this.apiKey;
    if (this.projectId) h['X-Project-Id'] = this.projectId;
    return { ...h, ...extra };
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {{ body?: unknown, params?: Record<string, string>, headers?: Record<string, string> }} [opts]
   */
  async _request(method, path, { body, params, headers } = {}) {
    const url = new URL(`${this.baseUrl}${path}`);
    if (params) {
      Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v));
    }

    const init = { method, headers: this._headers(headers) };
    if (body !== undefined) init.body = JSON.stringify(body);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    init.signal = controller.signal;

    try {
      const res = await fetch(url.toString(), init);
      clearTimeout(timer);
      const text = await res.text();
      const parsed = text ? JSON.parse(text) : {};
      if (!res.ok) throw new ReloraError(res.status, parsed.detail ?? text);
      return parsed;
    } catch (err) {
      clearTimeout(timer);
      if (err instanceof ReloraError) throw err;
      // AbortError means the request timed out via our controller
      if (err.name === 'AbortError') {
        throw new ReloraError(0, `Request timed out after ${this.timeout}ms`);
      }
      throw new ReloraError(0, err.message);
    }
  }

  // ── Ingest ────────────────────────────────────────────────────────────────

  /**
   * Ingest a webhook event for reliable delivery.
   * @param {string} destinationUrl
   * @param {Record<string, unknown>} payload
   * @param {{
   *   idempotencyKey?: string,
   *   filter?: string,
   *   transform?: Record<string, string>,
   *   orderingKey?: string,
   *   signatureProvider?: string,
   *   destinationId?: string,
   *   extraHeaders?: Record<string, string>,
   * }} [options]
   */
  async send(destinationUrl, payload, options = {}) {
    const params = { url: destinationUrl };
    if (options.filter) params.filter = options.filter;
    if (options.transform) params.transform = JSON.stringify(options.transform);
    if (options.orderingKey) params.ordering_key = options.orderingKey;
    if (options.signatureProvider) params.signature_provider = options.signatureProvider;
    if (options.destinationId) params.destination_id = options.destinationId;
    const headers = {};
    if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
    if (options.extraHeaders) Object.assign(headers, options.extraHeaders);
    return this._request('POST', '/api/v1/ingest', { body: payload, params, headers });
  }

  /**
   * Send the same payload to multiple destinations concurrently.
   * All destinations are attempted regardless of individual failures.
   * Each result is either the normal ingest response (success) or
   * ``{ url, id: null, error: "..." }`` (failure).
   * @param {string[]} destinationUrls
   * @param {Record<string, unknown>} payload
   * @param {object} [options]
   * @returns {Promise<Array<Record<string, unknown>>>}
   */
  async fanOut(destinationUrls, payload, options = {}) {
    const settled = await Promise.allSettled(
      destinationUrls.map(url => this.send(url, payload, options))
    );
    return settled.map((result, i) =>
      result.status === 'fulfilled'
        ? result.value
        : { url: destinationUrls[i], id: null, error: result.reason?.message ?? String(result.reason) }
    );
  }

  // ── Webhooks ──────────────────────────────────────────────────────────────

  /** @param {string} webhookId */
  async getWebhook(webhookId) {
    return this._request('GET', `/api/v1/webhooks/${webhookId}`);
  }

  /** @param {{ status?: string, limit?: number, offset?: number }} [opts] */
  async listWebhooks({ status, limit = 50, offset = 0 } = {}) {
    return this._request('GET', '/api/v1/webhooks', {
      params: { ...(status ? { status } : {}), limit: String(limit), offset: String(offset) },
    });
  }

  /** @param {string} webhookId */
  async replayWebhook(webhookId) {
    return this._request('POST', `/api/v1/webhooks/${webhookId}/replay`);
  }

  // ── DLQ ───────────────────────────────────────────────────────────────────

  /** @param {{ limit?: number, offset?: number }} [opts] */
  async listDlq({ limit = 50, offset = 0 } = {}) {
    return this._request('GET', '/api/v1/dlq', {
      params: { limit: String(limit), offset: String(offset) },
    });
  }

  async replayAllDlq() {
    return this._request('POST', '/api/v1/dlq/replay-all');
  }

  async dlqHealth() {
    return this._request('GET', '/api/v1/dlq/health');
  }

  // ── Stats & audit ─────────────────────────────────────────────────────────

  async getStats() {
    return this._request('GET', '/api/v1/stats');
  }

  /**
   * @param {{ resourceType?: string, action?: string, limit?: number, offset?: number }} [opts]
   */
  async getAuditLog({ resourceType, action, limit = 50, offset = 0 } = {}) {
    return this._request('GET', '/api/v1/audit-log', {
      params: {
        limit: String(limit),
        offset: String(offset),
        ...(resourceType ? { resource_type: resourceType } : {}),
        ...(action ? { action } : {}),
      },
    });
  }

  // ── Destinations ──────────────────────────────────────────────────────────

  async listDestinations() {
    return this._request('GET', '/api/v1/destinations');
  }

  /** @param {string} destinationId */
  async getDestination(destinationId) {
    return this._request('GET', `/api/v1/destinations/${destinationId}`);
  }

  /**
   * @param {string} name
   * @param {string} url
   * @param {Record<string, unknown>} [options]
   */
  async createDestination(name, url, options = {}) {
    return this._request('POST', '/api/v1/destinations', { body: { name, url, ...options } });
  }

  /**
   * Update a destination (PUT — supply all fields you want persisted).
   * @param {string} destinationId
   * @param {Record<string, unknown>} body
   */
  async updateDestination(destinationId, body) {
    return this._request('PUT', `/api/v1/destinations/${destinationId}`, { body });
  }

  /** @param {string} destinationId */
  async deleteDestination(destinationId) {
    return this._request('DELETE', `/api/v1/destinations/${destinationId}`);
  }

  // ── Event types ───────────────────────────────────────────────────────────

  async listEventTypes() {
    return this._request('GET', '/api/v1/event-types');
  }

  /**
   * @param {string} name
   * @param {Record<string, unknown>} [options] - description, schema, examplePayload, version
   */
  async createEventType(name, options = {}) {
    return this._request('POST', '/api/v1/event-types', { body: { name, ...options } });
  }

  /** @param {string} eventTypeId */
  async deleteEventType(eventTypeId) {
    return this._request('DELETE', `/api/v1/event-types/${eventTypeId}`);
  }

  // ── Alerts ────────────────────────────────────────────────────────────────

  async listAlerts() {
    return this._request('GET', '/api/v1/alerts');
  }

  /**
   * @param {string} name
   * @param {'slack' | 'email'} channelType
   * @param {Record<string, unknown>} config
   * @param {Record<string, unknown>} [options]
   */
  async createAlert(name, channelType, config, options = {}) {
    return this._request('POST', '/api/v1/alerts', {
      body: { name, channel_type: channelType, config, ...options },
    });
  }

  /** @param {string} alertId */
  async deleteAlert(alertId) {
    return this._request('DELETE', `/api/v1/alerts/${alertId}`);
  }
}
