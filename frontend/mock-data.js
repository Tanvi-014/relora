'use strict';

window.ReloraMock = {
  metrics: [
    { label: 'Reliability score', value: 98.7, suffix: '%', tone: 'good', detail: '+1.2% from baseline' },
    { label: 'Events accepted', value: 284921, suffix: '', tone: 'neutral', detail: 'last 24 hours' },
    { label: 'P95 delivery', value: 418, suffix: ' ms', tone: 'neutral', detail: '-84 ms after reroute' },
    { label: 'DLQ depth', value: 17, suffix: '', tone: 'warn', detail: '12 ready to replay' },
    { label: 'Open incidents', value: 2, suffix: '', tone: 'danger', detail: '1 provider-side spike' },
  ],
  journey: [
    { title: 'Accepted', body: 'Stripe posted invoice.paid and Relora responded 200 in 23 ms.', status: 'done', time: '10:04:12' },
    { title: 'Persisted', body: 'Payload, headers, idempotency key, and ordering key saved to Postgres.', status: 'done', time: '10:04:12' },
    { title: 'Routed', body: 'Matched production billing, analytics mirror, and customer success fan-out.', status: 'done', time: '10:04:13' },
    { title: 'Retried', body: 'Billing endpoint returned 503. Retry scheduled with jittered backoff.', status: 'active', time: '10:04:43' },
    { title: 'Recovered', body: 'Second attempt delivered. Incident counter updated automatically.', status: 'queued', time: '10:05:17' },
  ],
  feed: [
    { type: 'success', title: 'Recovered api.billing.internal', meta: '2nd attempt, 412 ms' },
    { type: 'warn', title: 'Retry-After honored for Shopify', meta: 'next attempt in 90 s' },
    { type: 'info', title: 'Schema drift acknowledged', meta: 'customer.subscription.updated' },
    { type: 'danger', title: 'Auth failures grouped', meta: '7 events, same destination' },
    { type: 'success', title: 'Replay batch completed', meta: '1,204 events restored' },
  ],
  anomalies: [
    { label: 'Failure rate', before: '0.2%', after: '3.8%', note: 'Spike isolated to GitHub source and one destination.' },
    { label: 'Payload shape', before: '22 keys', after: '26 keys', note: 'New nested billing fields detected and fingerprinted.' },
    { label: 'Latency', before: '391 ms', after: '918 ms', note: 'P95 increase began after downstream deploy marker.' },
  ],
  bars: [52, 70, 64, 79, 91, 77, 85, 72, 96, 88, 69, 81, 93, 74, 58, 83, 90, 86],
  features: [
    ['Accept ledger', 'Record the provider request, headers, idempotency key, and accepted timestamp before delivery begins.'],
    ['Route policy', 'Keep filtering, signing, ordering, retry, and circuit behavior visible per destination.'],
    ['Attempt timeline', 'Show every response code, duration, retry wait, and final state for each delivery.'],
    ['DLQ workbench', 'Classify permanent failures, group related incidents, and identify replay candidates.'],
    ['Replay governor', 'Recover a single event or time window with rate limits that protect downstream services.'],
    ['Audit trail', 'Track route edits, key rotation, replay jobs, schema acknowledgements, and membership changes.'],
  ],
};
