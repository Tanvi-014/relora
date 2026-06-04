/**
 * Relora JS SDK tests — Node.js 18+ built-in test runner (no external deps).
 * Run: node --test src/client.test.js
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Relora, ReloraError } from './client.js';

const BASE = 'http://localhost:8000';

// ── fetch mock helpers ────────────────────────────────────────────────────────

function okFetch(data, status = 200) {
  return async () => ({
    ok: true,
    status,
    text: async () => JSON.stringify(data),
  });
}

function errorFetch(data, status) {
  return async () => ({
    ok: false,
    status,
    text: async () => JSON.stringify(data),
  });
}

function captureFetch(data, status = 200) {
  let captured = null;
  const fn = async (url, init) => {
    captured = { url, init };
    return { ok: status < 400, status, text: async () => JSON.stringify(data) };
  };
  fn.captured = () => captured;
  return fn;
}

// ── ReloraError ───────────────────────────────────────────────────────────────

test('ReloraError has correct statusCode, detail, name', () => {
  const err = new ReloraError(429, 'Rate limit exceeded');
  assert.equal(err.statusCode, 429);
  assert.equal(err.detail, 'Rate limit exceeded');
  assert.equal(err.message, 'HTTP 429: Rate limit exceeded');
  assert.equal(err.name, 'ReloraError');
  assert.ok(err instanceof Error);
});

// ── send() ────────────────────────────────────────────────────────────────────

test('send() posts to /api/v1/ingest with url param', async () => {
  const spy = captureFetch({ webhook_id: 'abc', status: 'pending' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  const result = await relora.send('https://example.com/hook', { event: 'test' });
  assert.equal(result.webhook_id, 'abc');
  const { url, init } = spy.captured();
  assert.ok(url.includes('/api/v1/ingest'));
  assert.ok(url.includes('url='));
  assert.equal(init.method, 'POST');
});

test('send() sets X-Relora-API-Key header', async () => {
  const spy = captureFetch({ webhook_id: 'abc' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_live_key' });
  await relora.send('https://example.com/hook', {});
  assert.equal(spy.captured().init.headers['X-Relora-API-Key'], 'hk_live_key');
});

test('send() sets X-Project-Id header when projectId provided', async () => {
  const spy = captureFetch({ webhook_id: 'abc' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test', projectId: 'proj-uuid' });
  await relora.send('https://example.com/hook', {});
  assert.equal(spy.captured().init.headers['X-Project-Id'], 'proj-uuid');
});

test('send() sets Idempotency-Key header', async () => {
  const spy = captureFetch({ webhook_id: 'abc' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await relora.send('https://example.com/hook', {}, { idempotencyKey: 'idem-1' });
  assert.equal(spy.captured().init.headers['Idempotency-Key'], 'idem-1');
});

test('send() passes extraHeaders to request', async () => {
  const spy = captureFetch({ webhook_id: 'abc' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await relora.send('https://example.com/hook', {}, { extraHeaders: { 'X-Source': 'billing' } });
  assert.equal(spy.captured().init.headers['X-Source'], 'billing');
});

// ── ReloraError on failure ────────────────────────────────────────────────────

test('throws ReloraError on 4xx with detail from body', async () => {
  globalThis.fetch = errorFetch({ detail: 'Webhook not found' }, 404);
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await assert.rejects(
    () => relora.getWebhook('bad-id'),
    (err) => {
      assert.ok(err instanceof ReloraError);
      assert.equal(err.statusCode, 404);
      assert.equal(err.detail, 'Webhook not found');
      return true;
    }
  );
});

test('throws ReloraError on 401', async () => {
  globalThis.fetch = errorFetch({ detail: 'Invalid API key' }, 401);
  const relora = new Relora(BASE, { apiKey: 'wrong' });
  await assert.rejects(
    () => relora.getStats(),
    (err) => {
      assert.ok(err instanceof ReloraError);
      assert.equal(err.statusCode, 401);
      return true;
    }
  );
});

// ── fanOut() ─────────────────────────────────────────────────────────────────

test('fanOut() returns all results even on partial failure', async () => {
  let callCount = 0;
  globalThis.fetch = async (url) => {
    callCount++;
    if (url.includes('url=https%3A%2F%2Fa')) {
      return { ok: true, status: 200, text: async () => JSON.stringify({ webhook_id: 'id-1', status: 'pending' }) };
    }
    return { ok: false, status: 500, text: async () => JSON.stringify({ detail: 'Server error' }) };
  };
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  const results = await relora.fanOut(['https://a.com/h', 'https://b.com/h'], { event: 'x' });
  assert.equal(callCount, 2);
  assert.equal(results.length, 2);
  assert.equal(results[0].webhook_id, 'id-1');
  assert.equal(results[1].id, null);
  assert.ok(results[1].error.includes('HTTP 500'));
});

test('fanOut() with all success returns all results', async () => {
  let call = 0;
  globalThis.fetch = async () => {
    call++;
    return { ok: true, status: 200, text: async () => JSON.stringify({ webhook_id: `id-${call}`, status: 'pending' }) };
  };
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  const results = await relora.fanOut(['https://a.com/h', 'https://b.com/h'], { event: 'x' });
  assert.equal(results.length, 2);
  assert.ok(results.every(r => r.webhook_id));
});

// ── destinations ─────────────────────────────────────────────────────────────

test('createDestination(name, url, opts) sends correct body', async () => {
  const spy = captureFetch({ id: 'dest-1', name: 'My dest' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await relora.createDestination('My dest', 'https://example.com/hook', { max_retries: 3 });
  const body = JSON.parse(spy.captured().init.body);
  assert.equal(body.name, 'My dest');
  assert.equal(body.url, 'https://example.com/hook');
  assert.equal(body.max_retries, 3);
});

test('updateDestination() sends PUT to correct path', async () => {
  const spy = captureFetch({ id: 'dest-1' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await relora.updateDestination('dest-1', { max_retries: 10 });
  const { url, init } = spy.captured();
  assert.ok(url.includes('/api/v1/destinations/dest-1'));
  assert.equal(init.method, 'PUT');
});

// ── event types ───────────────────────────────────────────────────────────────

test('createEventType() sends name and options', async () => {
  const spy = captureFetch({ id: 'et-1', name: 'order.created' });
  globalThis.fetch = spy;
  const relora = new Relora(BASE, { apiKey: 'hk_test' });
  await relora.createEventType('order.created', { description: 'New order' });
  const body = JSON.parse(spy.captured().init.body);
  assert.equal(body.name, 'order.created');
  assert.equal(body.description, 'New order');
});

// ── dlq ───────────────────────────────────────────────────────────────────────

test('dlqHealth() returns health data', async () => {
  globalThis.fetch = okFetch({ health_score: 95, status: 'healthy' });
  const relora = new Relora(BASE);
  const result = await relora.dlqHealth();
  assert.equal(result.health_score, 95);
});

// ── timeout error message ─────────────────────────────────────────────────────

test('timeout produces descriptive ReloraError', async () => {
  globalThis.fetch = async (_url, { signal }) => {
    // Simulate an AbortError as the browser/Node would throw
    await new Promise((_, reject) => {
      signal.addEventListener('abort', () => {
        const err = new Error('The operation was aborted');
        err.name = 'AbortError';
        reject(err);
      });
    });
  };
  const relora = new Relora(BASE, { timeout: 1 });
  await assert.rejects(
    () => relora.getStats(),
    (err) => {
      assert.ok(err instanceof ReloraError);
      assert.ok(err.message.includes('timed out'), `expected timeout message, got: ${err.message}`);
      return true;
    }
  );
});
