# Hermes: Guaranteed Webhook Delivery Middleware

Hermes is an open-source, lightweight, self-hostable middleware proxy designed to guarantee webhook delivery. It sits between event publishers (e.g., Stripe, Shopify, Twilio, GitHub) and your application, catching webhook requests instantly, persisting them durably in a PostgreSQL database, and delivering them reliably with concurrent workers and exponential backoff.

If your downstream server is down for maintenance, deploys, or heavy load, Hermes buffers the webhooks and retries until delivery is confirmed. It features a developer console dashboard to monitor, inspect, and manually replay failed/dead-lettered webhooks.

---

## Architecture Highlight: Postgres Queueing (`SKIP LOCKED`)

Instead of requiring heavy message brokers like RabbitMQ or Redis, Hermes uses a single Postgres database instance. Concurrent workers claim pending jobs atomically using `SELECT ... FOR UPDATE SKIP LOCKED`, preventing multiple workers from claiming or delivering the same webhook, with zero performance locking bottlenecks.

```
                  ┌──────────────────────┐
                  │ Webhook Event Source │ (e.g. Stripe)
                  └──────────┬───────────┘
                             │ POST
                             ▼
                    ┌─────────────────┐
                    │  Ingestion API  │  (FastAPI - Instant 200 OK)
                    └────────┬────────┘
                             │ DB Write
                             ▼
                    ┌─────────────────┐
                    │   PostgreSQL    │  (State Store & Queue)
                    └────────┬────────┘
                             │
     ┌───────────────────────┼───────────────────────┐
     │ SKIP LOCKED           │ SKIP LOCKED           │ SKIP LOCKED
     ▼                       ▼                       ▼
┌──────────┐            ┌──────────┐            ┌──────────┐
│ Worker 1 │            │ Worker 2 │            │ Worker N │ (Concurrent Pool)
└────┬─────┘            └────┬─────┘            └────┬─────┘
     │ POST                  │ POST                  │ POST
     ▼                       ▼                       ▼
┌──────────┐            ┌──────────┐            ┌──────────┐
│ Target A │            │ Target B │            │ Target C │ (Your Downstream Apps)
└──────────┘            └──────────┘            └──────────┘
```

---

## Features

- **Ultra-Fast Ingestion:** Durably stores payload and headers in PostgreSQL under 15ms and returns a `200 OK` to the sender.
- **Concurrent Worker Pool:** High-throughput async delivery agents with safe database-level job-claiming.
- **Exponential Backoff Retries:** Automatically schedules retries with growing intervals on downstream failures (e.g., 30s, 60s, 120s...).
- **Header Preserving:** Forwards exact security signatures (e.g. `Stripe-Signature`), adding tracing headers `X-Hermes-Delivery-Id` and `X-Hermes-Attempt`.
- **Dead Letter Queue (DLQ):** Webhooks exceeding maximum retries are kept durably so you can inspect payloads and trace logs.
- **Developer Console:** Premium dark-mode dashboard (inspired by Linear and Railway) built with vanilla JS and custom CSS for monitoring, inspect, and manual replay commands.

---

## Quickstart

Ensure you have [Docker and Docker Compose](https://docs.docker.com/compose/install/) installed.

### 1. Start Hermes

Clone the project and run:

```bash
docker-compose up --build
```

This starts:
- **PostgreSQL Database** on port `5432`
- **FastAPI API Server & Web Workers** on port `8000`
- **Dashboard Web Console** mounted and served at `http://localhost:8000/`

---

## Ingesting Your First Webhook

To route a webhook through Hermes, send it to the Hermes host with the downstream destination passed in the `url` query parameter.

For example, if your application expects a webhook at `http://host.docker.internal:3000/webhooks`, configure your sender to hit:

```http
POST http://localhost:8000/api/v1/ingest?url=http://host.docker.internal:3000/webhooks
```

### Example Curl:

```bash
curl -X POST "http://localhost:8000/api/v1/ingest?url=https://httpbin.org/status/200" \
     -H "Content-Type: application/json" \
     -H "X-Custom-Auth-Signature: stripe_xyz123" \
     -d '{"event": "payment.succeeded", "amount": 2999}'
```

Hermes instantly saves the payload and custom signature header, queueing it for delivery, and returns:

```json
{
  "success": true,
  "webhook_id": "8a7fb783-a2be-4972-bc32-15e76a6cf34e",
  "message": "Webhook ingested and queued for delivery"
}
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/ingest?url=<URL>` | `POST` | Generic ingestion. Captures headers + JSON body. |
| `/api/v1/webhooks` | `GET` | List all webhooks (supports pagination and `?status=` filtering). |
| `/api/v1/webhooks/{id}` | `GET` | Detailed webhook inspection with nested execution attempts. |
| `/api/v1/webhooks/{id}/replay` | `POST` | Manually reschedule/replay a failed or dead webhook. |
| `/api/v1/stats` | `GET` | Aggregated dashboard telemetry card statistics. |
| `/health` | `GET` | API health indicator. |
