# Relora

Self-hosted webhook delivery middleware. Postgres-only. No Redis. No Kafka.

Relora sits between webhook publishers (Stripe, GitHub, Shopify, your internal services) and your application. It accepts events instantly, stores them durably in PostgreSQL, and delivers them reliably with exponential retry, circuit breaking, fan-out routing, and a full DLQ intelligence layer — the stuff every team builds from scratch and never quite finishes.

---

## Why Relora over rolling your own

Most teams write a Celery task or an SQS consumer and call it a webhook handler. Three months later it has no circuit breaker, no DLQ visibility, no replay, and the retry logic resets on every deploy. Relora replaces that entirely:

| Feature | DIY queue | Relora |
|---|---|---|
| Durable storage | depends | PostgreSQL — same DB you already have |
| Concurrent delivery | sometimes | `SELECT FOR UPDATE SKIP LOCKED` |
| Circuit breaker per destination | never | ✅ open/half-open/closed |
| Exponential backoff | usually | ✅ 429/Retry-After aware |
| Dead letter queue | rarely | ✅ with failure classification |
| DLQ intelligence | never | ✅ 0-100 health score, incident lifecycle |
| Fan-out to N destinations | never | ✅ one call, N deliveries |
| Filter / transform | never | ✅ expression-based, per-destination |
| Cloud source adapters | never | ✅ SNS, Pub/Sub, Azure Event Grid |
| Audit log | never | ✅ tamper-evident, queryable |
| Dashboard | never | ✅ built-in |
| Weekly reliability insights | never | ✅ graded report + AI Q&A |
| Multi-tenant | never | ✅ JWT + API key |

## Why Relora over Hookdeck / Svix (hosted services)

- **No per-event pricing.** Flat infra cost. At 10 M events/month the difference is real.
- **Data never leaves your infrastructure.** Required for healthcare, fintech, EU data residency.
- **Postgres only.** No new infrastructure to operate.
- **DLQ Intelligence.** No hosted service has automatic failure classification, incident lifecycle management, or AI-powered root cause analysis.

---

## Quickstart

**Prerequisites:** Docker, Docker Compose

```bash
git clone https://github.com/your-org/relora
cd relora
cp .env.example .env        # review defaults, set JWT_SECRET for production
docker-compose up --build
```

Marketing site: [http://localhost:8080](http://localhost:8080)  
Dashboard: [http://localhost:8080/app.html](http://localhost:8080/app.html)  
API docs: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)

**Register and send your first webhook:**

```bash
# 1. Create an account
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpassword"}' | jq .

# 2. Log in (sets a session cookie)
curl -s -c cookies.txt -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpassword"}' | jq .

# 3. Send a webhook (fan-out to two destinations)
curl -s -b cookies.txt \
  -X POST "http://localhost:8000/api/v1/ingest?url=http://localhost:9000/ok&url=http://localhost:9000/slow" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-123" \
  -d '{"event":"order.created","amount":4999}' | jq .
```

**Or use the Python SDK:**

```bash
pip install relora-sdk        # coming to PyPI — install from ./sdks/python for now
```

```python
from relora import ReloraClient

client = ReloraClient("http://localhost:8000", api_key="hk_...")

# Send a webhook
result = client.send(
    destination_url="https://myapp.com/hooks",
    payload={"event": "order.created", "amount": 4999},
    idempotency_key="order-123",
)
print(result["id"])  # webhook UUID

# Fan-out
client.fan_out(
    ["https://primary.example.com/hook", "https://analytics.example.com/hook"],
    payload={"event": "payment.succeeded"},
)

# DLQ
health = client.dlq_health()
print(f"DLQ health: {health['health_score']}/100")
```

**Or the CLI:**

```bash
pip install ./cli            # install from repo
relora config set --url http://localhost:8000 --api-key hk_...

relora ingest --to http://localhost:9000/ok '{"event":"test"}'
relora stats
relora dlq list
relora dlq replay-all --confirm
relora listen --port 4040 --forward http://localhost:3000/hook
```

---

## Architecture

```
Webhook Source (Stripe, GitHub, SNS, Pub/Sub…)
        │  POST
        ▼
┌─────────────────────────────┐
│   Relora Ingestion API      │  FastAPI — returns 200 immediately
│   /api/v1/ingest            │  Writes to Postgres, runs filter/transform
└────────────┬────────────────┘
             │ SELECT FOR UPDATE SKIP LOCKED
             ▼
┌─────────────────────────────┐
│   Worker Pool               │  Concurrent async workers
│   Circuit Breaker           │  Per-destination open/half-open/closed
│   Retry Scheduler           │  Exponential backoff, 429-aware
└────────────┬────────────────┘
             │ failure
             ▼
┌─────────────────────────────┐
│   Dead Letter Queue         │  Failure classification
│   DLQ Intelligence Engine   │  Health score, incident lifecycle, AI analysis
└─────────────────────────────┘
```

All state lives in **PostgreSQL**. No Redis. No Kafka. No external queue service.

---

## Features

### Delivery reliability
- **At-least-once delivery** with `SELECT FOR UPDATE SKIP LOCKED` — no double-delivery from the queue
- **Exponential backoff** — `base × 2^attempt`, respects `Retry-After` headers from destinations
- **Circuit breaker per destination** — open/half-open/closed state machine, prevents hammering broken endpoints
- **Idempotency** — `Idempotency-Key` header deduplicates at ingest; unique constraint enforced in Postgres
- **Ordering keys** — `ordering_key` field guarantees FIFO delivery per key within a destination

### Routing
- **Fan-out** — one inbound event → N independent deliveries (`?url=...&url=...`)
- **Registered destinations** — store URL, circuit state, custom headers, retry policy, transform, filter
- **Filter expressions** — `event.type == 'payment.succeeded'` — filter at ingest, not after delivery
- **Payload transforms** — JSON field mapping to reshape payloads before delivery

### DLQ Intelligence (unique — no other tool has this)
- Automatic failure classification into 14 categories (DNS, SSL, timeout, auth, rate-limit, etc.)
- Incident lifecycle management — open → investigating → resolved, with affected webhook count
- 0-100 health score based on DLQ size, growth rate, failure diversity, circuit state
- AI-powered root cause analysis via Claude (`ENABLE_AI_FEATURES=true`)

### Weekly Insights
- Executive-style reliability briefing generated weekly per tenant
- Letter grade (A+ → F) based on delivery success rate
- AI-written narrative summary with trend analysis
- Interactive Q&A — ask questions about your reliability data in plain English
- Full archive of past weekly reports

### Dashboard
- **Events** — full webhook history with delivery status, payload inspection, and event pipeline view
- **Destinations** — manage endpoints with a type-picker wizard (My API / Localhost / Test Endpoint / Other)
- **Failed Events** — unified view with tabs for Failures, Incidents, DLQ, Replays, and Alert Settings
- **Analytics** — Weekly Report, Trends, and Archive tabs
- **Audit Log** — tamper-evident record of all configuration changes
- Demo-first onboarding — a sandbox destination is auto-created on signup so you can see a working delivery before configuring anything

### Observability
- Prometheus metrics at `/metrics` — webhook counts, delivery latency, circuit breaker states, DLQ depth
- OpenTelemetry tracing — opt-in with `OTEL_EXPORTER_OTLP_ENDPOINT`
- Structured JSON logs with `tenant_id`, `webhook_id`, `event_id` on every line
- Audit log — every CREATE/UPDATE/DELETE to destinations, projects, and alert configs is recorded

### Cloud integrations
- **AWS SNS** — `POST /api/v1/sources/aws-sns` with automatic subscription confirmation handshake
- **GCP Pub/Sub** — push subscription endpoint with base64 data decoding
- **Azure Event Grid** — both schemas, validation handshake, optional shared secret
- **Stripe, GitHub, Shopify** — signature verification via `?signature_provider=stripe|github|relora`

### Operations
- **Graceful shutdown** — SIGTERM drains in-flight deliveries (35 s window) before exit
- **Multi-replica safe** — Postgres token-bucket rate limiter works across API replicas
- **Production nginx** — `nginx.prod.conf` with TLS 1.2/1.3, OCSP stapling, HSTS preload, `/metrics` IP allowlist
- **Startup validation** — refuses to start in `ENVIRONMENT=production` with default `JWT_SECRET`

---

## Configuration

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Set to `production` to enable all safety checks |
| `JWT_SECRET` | placeholder | **Must** be changed in production. Generate: `openssl rand -hex 32` |
| `DATABASE_URL` | local Postgres | Async SQLAlchemy URL |
| `WORKER_CONCURRENCY` | `10` | Concurrent delivery workers per process |
| `DEFAULT_MAX_RETRIES` | `5` | Delivery attempts before DLQ |
| `BACKOFF_BASE_SECONDS` | `30` | Retry backoff base (30 → 60 → 120 → 240 → 480 s) |
| `MONTHLY_EVENT_QUOTA` | `0` | Per-tenant event quota. 0 = unlimited |
| `RATE_LIMIT_PER_MINUTE` | `60` | Ingest rate limit per tenant |
| `ALLOW_PRIVATE_DESTINATIONS` | `false` | **Must** be false in production (SSRF risk) |
| `FORCE_HTTPS` | `false` | Redirects HTTP → HTTPS and sets HSTS |
| `COOKIE_SECURE` | `false` | Set to `true` when behind HTTPS |
| `ANTHROPIC_API_KEY` | — | Enable AI features (`ENABLE_AI_FEATURES=true`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry collector endpoint |

---

## Production deployment

### Docker Compose (single server)

```bash
cp .env.example .env
# Edit .env: set JWT_SECRET, POSTGRES_PASSWORD, ENVIRONMENT=production,
#            COOKIE_SECURE=true, FORCE_HTTPS=true
docker-compose up -d
```

For TLS, use `nginx.prod.conf` instead of `nginx.conf`:

```yaml
# In docker-compose.yml nginx volumes:
- ./nginx.prod.conf:/etc/nginx/conf.d/default.conf:ro
- /etc/letsencrypt:/etc/letsencrypt:ro
```

### Horizontal scaling

Scale workers independently from the API:

```bash
docker-compose up -d --scale relora-worker=4
```

All workers share the same Postgres queue via `SKIP LOCKED` — no coordination needed.

### Database migrations

```bash
docker-compose run --rm relora-migrate   # runs alembic upgrade head
```

Never use `AUTO_CREATE_TABLES=true` in production.

### Free hosting (Fly.io + Supabase)

Fly.io has a free tier (3 shared VMs). Supabase offers free managed Postgres. Together they run Relora at zero cost.

The API serves the frontend directly (FastAPI StaticFiles), so no separate nginx app is needed on Fly.io.

**1. Create a Supabase project** at [supabase.com](https://supabase.com) and copy the connection strings (both pooler and direct).

**2. Install the Fly CLI** and log in:

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
```

**3. Create both apps:**

```bash
fly apps create relora-api
fly apps create relora-worker
```

**4. Set secrets on each app:**

```bash
# API
fly secrets set --app relora-api \
  JWT_SECRET="$(openssl rand -hex 32)" \
  DATABASE_URL="postgresql+asyncpg://postgres:[password]@[host]:5432/postgres" \
  SYNC_DATABASE_URL="postgresql://postgres:[password]@[host]:5432/postgres"

# Worker (same DATABASE_URL, no JWT_SECRET needed)
fly secrets set --app relora-worker \
  DATABASE_URL="postgresql+asyncpg://postgres:[password]@[host]:5432/postgres"
```

**5. Run migrations once:**

```bash
fly ssh console --app relora-api -C "cd /app/backend && alembic upgrade head"
```

**6. Deploy:**

```bash
fly deploy --config fly.api.toml
fly deploy --config fly.worker.toml
```

Your app will be live at `https://relora-api.fly.dev`.

Note: Vercel and other serverless platforms are **not compatible** — Relora's background worker requires a persistent process.

---

## SDKs

**Python** (sync + async):
```bash
pip install ./sdks/python          # local install
# pip install relora-sdk  # coming to PyPI
```

```python
from relora import ReloraClient
from relora.async_client import AsyncReloraClient

# Sync
client = ReloraClient("http://localhost:8000", api_key="hk_...")
client.send("https://myapp.com/hook", {"event": "order.created"})

# Async
async with AsyncReloraClient("http://localhost:8000", api_key="hk_...") as client:
    await client.fan_out(["https://a.com/hook", "https://b.com/hook"], {"event": "test"})
```

**JavaScript / TypeScript** (ESM + CJS, zero dependencies):
```bash
npm install ./sdks/js              # local install
# npm install relora-sdk  # coming to npm
```

```js
import { Relora } from 'relora-sdk';

const relora = new Relora('http://localhost:8000', { apiKey: 'hk_...' });
await relora.send('https://myapp.com/hook', { event: 'order.created' });
await relora.fanOut(['https://a.com/hook', 'https://b.com/hook'], { event: 'test' });
const health = await relora.dlqHealth();
```

---

## CLI

```bash
pip install ./cli

relora config set --url http://localhost:8000 --api-key hk_...

relora ingest --to https://myapp.com/hook '{"event":"test"}'
relora status <webhook-id>
relora stats
relora dlq list
relora dlq replay <webhook-id>
relora dlq replay-all --confirm
relora dlq health
relora audit --resource-type destination --action UPDATE
relora listen --port 4040 --forward http://localhost:3000/hook
```

---

## Benchmarking

Run the load test against a local stack:

```bash
docker-compose up -d
pip install httpx
python benchmark/loadtest.py \
  --url http://localhost:8000 \
  --destination http://localhost:9000/ok \
  --concurrency 20 \
  --duration 30
```

Results are saved to `benchmark/last_result.json`.

---

## Development

```bash
cd backend
pip install -r requirements.txt
python -m pytest                          # unit tests
python -m pytest tests/test_api_integration.py  # integration (needs running stack)
```

Run locally:
```bash
uvicorn app.api_main:app --reload --port 8000 &
python -m app.worker_main &
```

API docs available at [http://localhost:8000/api/docs](http://localhost:8000/api/docs).

---

## Integrations

- [Stripe Integration Guide](docs/STRIPE_INTEGRATION.md)
- [GitHub Integration Guide](docs/GITHUB_INTEGRATION.md)

---

## License

MIT
