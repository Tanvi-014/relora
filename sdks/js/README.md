# Relora JavaScript SDK

The official JavaScript/TypeScript SDK for the **Relora**.

Zero external dependencies. Compatible with Node.js 18+, browsers, Deno, Bun,
and Cloudflare Workers — uses the platform `fetch` API.

## Installation

```bash
npm install relora-sdk
```

## Quickstart

```javascript
import { Relora } from 'relora-sdk';

const relora = new Relora('http://localhost:8000', { apiKey: 'hk_live_...' });

const result = await relora.send('https://api.myapp.com/webhooks/receiver', {
  event: 'payment.succeeded',
  data: { id: 'pay_10293', amount: 2999, currency: 'usd' },
}, {
  idempotencyKey: 'pay_10293_succeeded',
});

console.log(`Queued: ${result.webhook_id}`);
```

### CommonJS (Node.js without `"type": "module"`)

```javascript
const { Relora } = await import('relora-sdk');
```

## Advanced features

### Custom delivery headers

```javascript
await relora.send('https://api.myapp.com/webhooks/receiver', payload, {
  extraHeaders: { 'X-Source': 'billing-service' },
});
```

### Filtering & reshaping payloads

```javascript
await relora.send('https://api.myapp.com/webhooks/receiver', {
  event: 'payment.succeeded',
  amount: 2999,
}, {
  filter: "event == 'payment.succeeded'",
  transform: { transaction_id: 'id', cents: 'amount' },
});
```

### Fan-out to multiple destinations

All destinations are attempted regardless of individual failures.

```javascript
const results = await relora.fanOut(
  ['https://app-a.com/hook', 'https://app-b.com/hook'],
  { event: 'order.created' },
);

for (const r of results) {
  if (r.error) {
    console.error(`Failed: ${r.url} — ${r.error}`);
  } else {
    console.log(`Queued: ${r.webhook_id}`);
  }
}
```

### Inspect & replay webhooks

```javascript
const details = await relora.getWebhook('550e8400-e29b-41d4-a716-446655440000');
console.log(`Status: ${details.status}, attempts: ${details.attempts?.length}`);

await relora.replayWebhook('550e8400-e29b-41d4-a716-446655440000');
```

### Manage destinations

```javascript
const dest = await relora.createDestination(
  'Billing service',
  'https://billing.example.com/hooks',
  { max_retries: 10, webhook_secret: 'whsec_...' },
);

await relora.updateDestination(dest.id, { max_retries: 3, is_enabled: false });
await relora.deleteDestination(dest.id);
```

### Event type catalog

```javascript
await relora.createEventType('payment.succeeded', {
  description: 'Fired when a payment is confirmed',
  schema: { type: 'object', required: ['id', 'amount'] },
});

const types = await relora.listEventTypes();
```

### Alert channels

```javascript
await relora.createAlert('Slack DLQ alerts', 'slack', {
  webhook_url: 'https://hooks.slack.com/services/...',
});
```

### DLQ management

```javascript
const health = await relora.dlqHealth();
console.log(`Health score: ${health.health_score}/100`);

await relora.replayAllDlq();
```

## Multi-project support

```javascript
const relora = new Relora('http://localhost:8000', {
  apiKey: 'hk_live_...',
  projectId: '<project-uuid>',
});
```

## Error handling

All errors throw `ReloraError` with `statusCode` and `detail`:

```javascript
import { Relora, ReloraError } from 'relora-sdk';

try {
  await relora.send('https://example.com/hook', {});
} catch (err) {
  if (err instanceof ReloraError) {
    console.error(err.statusCode, err.detail); // e.g. 429 "Rate limit exceeded"
  }
}
```

## TypeScript

Full type definitions are included. No `@types` package needed.

```typescript
import { Relora, ReloraError, type Destination, type SendOptions } from 'relora-sdk';
```
