# Hermes — Development Progress Tracker

> Living document. Updated at every milestone.

---

## Phase 9 — Production Architecture Upgrade (v2) ✅

**Status: SHIPPED**
**Date Completed: 2026-05-29**

### What was built:

| Feature | File | Status |
|---------|------|--------|
| Split API/Worker processes | `api_main.py`, `worker_main.py` | ✅ |
| Postgres token-bucket rate limiter (multi-process safe) | `rate_limit.py` | ✅ |
| FIFO ordering_key delivery (strict order per key) | `worker.py`, migration 0006 | ✅ |
| WebSocket hub (real-time dashboard, no polling) | `websocket_hub.py` | ✅ |
| Destinations registry with full CRUD + circuit breaker | `models.py`, `api_main.py` | ✅ |
| Standard Webhooks signing (svix-compatible) | `standard_webhooks.py` | ✅ |
| Adaptive retry (429→Retry-After, 4xx→no-retry, 503→long) | `retry_strategy.py` | ✅ |
| Circuit breaker per destination | `circuit_breaker.py` | ✅ |
| GIN full-text payload search | migration 0006 | ✅ |
| Pull-based polling endpoints for firewall-blocked consumers | `api_main.py` | ✅ |
| Time-window bulk replay with rate control | `api_main.py`, `models.py` | ✅ |
| Claude-powered AI schema intelligence | `ai_intelligence.py` | ✅ |
| Webhook simulator (Stripe/GitHub/Shopify templates) | `simulator.py` | ✅ |
| Delivery SLA tracking (P50/P95/P99) | `api_main.py` | ✅ |
| Event type / schema catalog | `models.py`, `api_main.py` | ✅ |
| httpOnly cookie JWT (XSS-safe, no localStorage) | `auth.py`, `api_main.py` | ✅ |
| DB connection pooling (pool_size=20, max_overflow=10) | `db.py` | ✅ |
| Alembic migration 0006 for all new tables | `alembic/versions/` | ✅ |
| World-class dark UI redesign (sidebar, real-time WS) | `frontend/` | ✅ |
| Separated docker-compose (postgres/api/worker/migrate) | `docker-compose.yml` | ✅ |
| Updated Dockerfile + requirements (websockets, anthropic) | `Dockerfile`, `requirements.txt` | ✅ |
| Comprehensive test suite (retry, signing, circuit breaker, routing, simulator) | `tests/` | ✅ |
| Updated `.env.example` with all v2 variables | `.env.example` | ✅ |
| `main.py` backward-compat shim | `main.py` | ✅ |

### Security fixes vs v1:
- `AUTO_CREATE_TABLES` defaults to `false` (was `true`)
- `ALLOW_PRIVATE_DESTINATIONS` defaults to `false` (was `true`)
- JWT stored in httpOnly cookie, not localStorage (XSS protection)
- Rate limiter is now Postgres-backed — works across multiple API replicas

---

## Architecture Mindmap

```
Sender (Stripe, Twilio, GitHub...)
    │
    ▼
┌─────────────────────────┐
│  1. Ingestion API       │  ✅ COMPLETE
│  Returns 200, saves DB  │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  2. Message Queue + DB  │  ✅ COMPLETE
│  Postgres + status      │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐        ┌──────────┐
│  3. Delivery Worker     │───────▶│ Your App │
│  POSTs to destination   │        │ 200=done │
└───────────┬─────────────┘        └──────────┘
            │ (failure)
            ▼
┌─────────────────────────┐
│  4. Retry Scheduler     │  ✅ COMPLETE
│  Backoff: 30s→2m→1h    │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  5. Dead Letter Queue   │  ✅ COMPLETE
│  Inspect + Replay       │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  6. Dashboard UI        │  ✅ COMPLETE
│  Logs, retry, replay    │
└─────────────────────────┘
```

---

## Phase 1 — Core MVP ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-27**

| Component | Files | Status |
|-----------|-------|--------|
| Ingestion API | `main.py` | ✅ `POST /api/v1/ingest?url=<URL>` with instant 200 OK |
| Message Queue + DB | `models.py`, `db.py` | ✅ PostgreSQL with `webhooks` + `delivery_attempts` |
| Delivery Worker | `worker.py` | ✅ Concurrent async pool with `SELECT FOR UPDATE SKIP LOCKED` |
| Retry Scheduler | `worker.py` | ✅ Exponential backoff: `base × 2^attempt` |
| Dead Letter Queue | `worker.py`, `main.py` | ✅ `failed` status after max retries, manual replay |
| Dashboard UI | `frontend/*` | ✅ Dark-mode console with filters, stats, inspector, replay |
| Docker Compose | `docker-compose.yml` | ✅ One-command local stack |

---

## Phase 2 — Production Readiness ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-27**

| Feature | Files | Status |
|---------|-------|--------|
| Idempotency | `models.py`, `main.py` | ✅ `Idempotency-Key` header + unique constraint on `(tenant, destination, key)` |
| Signature Verification | `signatures.py` | ✅ Stripe, GitHub, and generic HMAC verification |
| API Key Auth | `security.py`, `config.py` | ✅ `X-Hermes-API-Key` header with tenant mapping |
| Multi-tenant Isolation | `main.py`, `models.py` | ✅ `tenant_id` column, tenant-scoped queries |
| Destination URL Validation | `security.py` | ✅ Private IP blocking, host allowlists |
| Structured JSON Logging | `logging_config.py` | ✅ Structured events with tenant/webhook/event IDs |
| Prometheus Metrics | `main.py` | ✅ `/metrics` endpoint with gauges |
| Fan-out Routing | `main.py`, `routing.py` | ✅ Multiple `urls` for broadcasting |
| Event Filtering | `routing.py` | ✅ `filter=event.type=='payment.succeeded'` |
| Payload Transform | `routing.py` | ✅ JSON field mapping via `transform` param |
| Usage/Billing API | `main.py` | ✅ `/api/v1/usage` endpoint |
| CI/CD | `.github/` | ✅ GitHub Actions for backend tests |
| DB Migrations | `alembic/` | ✅ Alembic scaffolding |
| Demo Script | `scripts/demo.py` | ✅ One-command full product walkthrough |
| Fake Downstream | `scripts/` | ✅ Local `/ok`, `/fail`, `/flaky` test endpoints |
| Dashboard API Key | `app.js` | ✅ localStorage persistence + prompt on 401 |

### Tests Verified:
- 16 unit tests passing
- 5 live integration tests passing
- Docker build + compose startup verified
- Demo script proves: health, delivery, idempotency, retry, replay, fan-out, filtering

---

## Phase 3 — Alerts & Notifications ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

When a webhook exhausts all retries and hits the DLQ, nobody gets notified. In production, that can cost real money.

### What to build:
- `[x]` **Alert configuration model** — Store alert destinations per tenant (email, Slack webhook URL, etc.)
- `[x]` **DLQ alert trigger** — When worker moves a webhook to `failed`, fire alerts to configured channels
- `[x]` **Slack integration** — POST to Slack incoming webhook with formatted failure details
- `[x]` **Email integration** — Send email via SMTP with failure summary
- `[x]` **Dashboard alert config UI** — Settings page to add/remove alert destinations

### Files created/modified:
- `backend/app/alerts.py` — Alert dispatcher logic
- `backend/app/models.py` — `AlertConfig` table
- `backend/app/main.py` — CRUD endpoints for alert configs
- `backend/app/worker.py` — Fire alert on DLQ transition
- `frontend/` — Alert settings panel and styling

---

## Phase 4 — SDKs ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

Developers shouldn't need curl. They need standard zero-dependency libraries:

```python
# Python
from hermes import HermesClient
client = HermesClient("http://localhost:8000", api_key="hk_...")
client.send("https://myapp.com/webhook", {"event": "order.created"})
```

```javascript
// JavaScript
import { Hermes } from 'hermes-middleware-sdk';
const hermes = new Hermes('http://localhost:8000', { apiKey: 'hk_...' });
await hermes.send('https://myapp.com/webhook', { event: 'order.created' });
```

### What to build:
- `[x]` **Python SDK** — `sdks/python/` package with zero-dependency `HermesClient` class
- `[x]` **JavaScript SDK** — `sdks/js/` package with ESM/Node compatible `Hermes` class
- `[x]` **SDK docs & setup** — Comprehensive setup configurations and usage README details

---

## Phase 5 — Multi-User SaaS Dashboard ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

Currently auth is API-key based. Real SaaS needs login, projects, teams.

### What to build:
- [x] **User accounts table** — email + hashed password
- [x] **JWT session auth** — Login/register endpoints
- [x] **Project model** — Users can create projects, each project gets API keys
- [x] **Team permissions** — Owner, admin, viewer roles
- [x] **Dashboard login page** — Auth-gated dashboard with session management
- [x] **Project switcher** — Dropdown in dashboard header to switch contexts

### Files created/modified:
- `backend/app/auth.py` — JWT token generation, password hashing, auth dependencies
- `backend/app/config.py` — JWT_SECRET and JWT_ALGORITHM settings
- `backend/app/main.py` — Login/register endpoints, project CRUD, team member management
- `backend/app/models.py` — User, Project, ProjectMember models (already existed)
- `backend/requirements.txt` — python-jose, passlib, python-multipart dependencies
- `backend/alembic/versions/20260529_0005_add_user_project_models.py` — Database migration
- `frontend/login.html` — Login page UI
- `frontend/register.html` — Registration page UI
- `frontend/auth.js` — Auth client-side logic
- `frontend/index.html` — Project switcher, projects settings, team members UI
- `frontend/app.js` — JWT auth integration, project management, team management
- `frontend/style.css` — Login page styles, project switcher styles

---

## Phase 6 — Public Deployment ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

### What to build:
- [x] **Production Dockerfile** — Multi-stage, non-root, minimal image
- [x] **Railway/Render one-click deploy** — `railway.toml` or `render.yaml`
- [x] **HTTPS enforcement** — Redirect HTTP → HTTPS
- [x] **Rate limiting middleware** — Per-tenant request throttling
- [x] **Health check dashboard** — Uptime monitoring endpoint

### Files created/modified:
- `backend/Dockerfile` — Multi-stage build with non-root user and health checks
- `railway.toml` — Railway one-click deploy configuration with Postgres
- `render.yaml` — Render one-click deploy configuration with Postgres
- `backend/app/config.py` — FORCE_HTTPS and RATE_LIMIT_PER_MINUTE settings
- `backend/app/main.py` — HTTPS middleware, rate limiting on ingest, detailed health check
- `backend/app/rate_limit.py` — In-memory rate limiter per tenant/IP

---

## Phase 7 — Real Webhook Integrations ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

### What to build:
- [x] **Stripe test mode walkthrough** — Connect Stripe CLI, forward events through Hermes
- [x] **GitHub webhook walkthrough** — Configure a repo webhook to point at Hermes
- [x] **Integration documentation** — Comprehensive guides for Stripe and GitHub
- [x] **README examples** — Quick integration examples in main README

### Files created/modified:
- `docs/STRIPE_INTEGRATION.md` — Complete Stripe webhook integration guide
- `docs/GITHUB_INTEGRATION.md` — Complete GitHub webhook integration guide
- `README.md` — Added Integrations section with quick examples

---

## Phase 8 — Bug Fixes & Authentication Stability ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-29**

### What was fixed:
- [x] **Missing require_api_key function** — Added missing function in auth.py for backward compatibility
- [x] **Password hashing limitation** — Switched from bcrypt to argon2 to avoid 72-byte password limit
- [x] **Frontend JSON parsing error** — Improved error handling to check content-type before parsing
- [x] **Docker container issues** — Fixed PYTHONPATH and permission issues in Dockerfile

### Files created/modified:
- `backend/app/auth.py` — Added require_api_key function, switched to argon2
- `backend/requirements.txt` — Changed from passlib[bcrypt] to passlib[argon2]
- `frontend/auth.js` — Added content-type checking before JSON parsing
- `backend/Dockerfile` — Fixed PYTHONPATH and permission issues

---

## Phase 11 — Production Hardening & Dashboard Redesign ✅

**Status: SHIPPED**
**Date Completed: 2026-06-03**

### Critical Bug Fixes
- [x] **Async Anthropic client** — `ai_intelligence.py` was using `anthropic.Anthropic` (sync), blocking the event loop for every AI call. Fixed: `anthropic.AsyncAnthropic` + `await`
- [x] **SSRF vulnerability in `update_destination`** — URL was never re-validated on update, allowing POST-creation redirection to internal IPs (169.254.x.x, localhost). Fixed: `validate_destination_url()` called on every PUT
- [x] **Password validation** — register endpoint accepted empty strings. Fixed: 8-char minimum + email format check
- [x] **Replay job crash recovery** — API process crash mid-replay left jobs stuck in `status='running'` forever. Fixed: startup scan resets jobs stuck >5 min
- [x] **Pydantic destination models** — `create_destination` and `update_destination` used `Dict[str, Any]` with no validation. Fixed: `DestinationCreate` and `DestinationUpdate` Pydantic models with field constraints

### Architecture
- [x] **Router split** — `api_main.py` reduced from ~1900 lines to 103 lines. All endpoint handlers moved to `app/routers/`: `auth`, `projects`, `destinations`, `webhooks`, `alerts`, `event_types`, `ai_tools`, `dlq`, `simulator`, `consumer`, `system`
- [x] **`/api/v1/dashboard` endpoint** — Single API call returns all Overview page data: success rate, P95 latency, DLQ depth, circuit breaker summary, active incidents, recent failures, hourly throughput sparkline

### Dashboard Redesign
- [x] **SVG navigation icons** — All emoji nav icons replaced with clean Heroicons-style SVGs. No more 📊📨🎯 template signals
- [x] **Health banner** — Color-coded system status bar (green/amber/red) above the fold on every load
- [x] **4 KPI cards** — Success Rate (24h), P95 Latency, DLQ Depth, Circuit Breakers — each color-coded by threshold
- [x] **Active incidents section** — Surfaced from DLQ Intelligence directly on Overview; hidden when no incidents
- [x] **Delivery throughput sparkline** — 24-bar stacked chart (delivered vs failed per hour), rendered in pure CSS/JS
- [x] **Recent failures panel** — Last 8 DLQ items with failure category badge, truncated error, relative timestamp, and inline Replay button — one click from the homepage
- [x] **Single dashboard call** — Overview now makes one `GET /api/v1/dashboard` instead of 3+ separate calls

### Files modified
- `backend/app/ai_intelligence.py` — async client fix
- `backend/app/routers/` (11 new files) — router split
- `backend/app/api_main.py` — 103-line entry point
- `backend/app/schemas.py` — `DestinationCreate`, `DestinationUpdate` models
- `frontend/index.html` — SVG icons, new Overview layout
- `frontend/app.js` — `loadDashboard()`, `_renderHealthBanner()`, `_renderKPIs()`, `_renderIncidents()`, `_renderSparkline()`, `_renderRecentFailures()`
- `frontend/style.css` — KPI cards, health banner, sparkline, incident rows, failure rows

---

## Phase 10 — Observability, Cloud Adapters & Critical Bug Fixes ✅

**Status: SHIPPED**
**Date Completed: 2026-06-03**

### Bug Fixes:
- [x] **Invalid PostgreSQL CLAIM_QUERY syntax** — Removed `WAIT FOR LOCKING row_lock_timeout_in_ms` (not valid SQL); replaced with `SET LOCAL lock_timeout = '5000ms'` executed before claim
- [x] **Missing `response_body_compressed` column** — Added column to `DeliveryAttempt` model + Alembic migration 0008; previously caused worker crashes on any compressed response body
- [x] **Filter operator precedence bug** — `>` was matching `>=` expressions (and `<` matching `<=`) because the regex didn't capture the operator; fixed by adding capture group so the exact matched operator is used directly
- [x] **`_set_state` race condition** — Added `with_for_update()` to circuit breaker state transitions to prevent two workers simultaneously overwriting state
- [x] **Confusing broadcast status** — Extracted `_final_status` / `can_retry` variables before the broadcast block; eliminated fragile inline ternary referencing variables only defined in the failure branch

### New Features:
- [x] **OpenTelemetry integration** (`telemetry.py`) — Opt-in distributed tracing + custom metrics (ingestion counter, delivery latency histogram, DLQ depth, circuit trip counter); gracefully disabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset
- [x] **Cloud event source adapters** (`event_sources.py`) — Three ingest translation endpoints:
  - `POST /api/v1/sources/aws-sns` — AWS SNS notifications + automatic subscription confirmation handshake
  - `POST /api/v1/sources/gcp-pubsub` — Google Cloud Pub/Sub push subscriptions (base64 data decoding)
  - `POST /api/v1/sources/azure-event-grid` — Azure Event Grid (both schemas + validation handshake + optional shared secret)
- [x] **DLQ archival endpoint** — `DELETE /api/v1/dlq/archive?older_than_days=30` with `dry_run` preview mode; cascade-deletes delivery attempts via FK
- [x] **Enhanced Prometheus metrics** — `/metrics` now exports circuit breaker state counts (`closed/open/half_open`) and DLQ health score alongside existing webhook gauges

### Files created/modified:
- `backend/app/worker.py` — Fixed CLAIM_QUERY SQL, SET_LOCK_TIMEOUT, broadcast status, telemetry calls
- `backend/app/circuit_breaker.py` — FOR UPDATE in `_set_state`, circuit trip telemetry
- `backend/app/routing.py` — Regex captures operator group, eliminates re-scan loop
- `backend/app/models.py` — `response_body_compressed` column on DeliveryAttempt
- `backend/alembic/versions/20260603_0008_add_response_body_compressed.py` — Migration
- `backend/app/telemetry.py` — New: OpenTelemetry setup + metric instruments
- `backend/app/event_sources.py` — New: AWS SNS, GCP Pub/Sub, Azure Event Grid adapters
- `backend/app/api_main.py` — DLQ archival endpoint, enhanced /metrics, telemetry startup, source router mount
- `backend/requirements.txt` — Added `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`

---

## Phase 9 — DLQ Intelligence System ✅

**Status: SHIPPED & TESTED**
**Date Completed: 2026-05-31**

### What was built:
- [x] **Failure Classification Engine** — Automatic classification of failures into categories, subcategories, severity, and recoverability
- [x] **Root Cause Aggregation** — Grouping similar failures into incidents with automatic incident lifecycle management
- [x] **DLQ Health Scoring** — 0-100 health score based on DLQ size, growth rate, age, diversity, success rate, and circuit state
- [x] **Growth Analysis** — Tracking DLQ growth over 15m, 1h, 6h, 24h windows with trend classification
- [x] **Destination Health Analysis** — Per-destination health reports with success rate, failure rate, circuit state, and recommendations
- [x] **Actionable Recommendations** — Automatic remediation suggestions for each failure type
- [x] **Incident Management** — Automatic incident creation, updates, and resolution based on failure patterns
- [x] **Auto Incident Detection** — Background scheduler that automatically creates incidents when thresholds are exceeded
- [x] **AI Analysis Endpoint** — Human-readable analysis of DLQ failures with recommendations
- [x] **DLQ Intelligence Dashboard** — Dedicated UI showing health score, incidents, failure breakdown, trends, and root causes

### Files created/modified:
- `backend/app/models.py` — Added failure classification enums, Incident model, updated DeliveryAttempt with classification fields
- `backend/app/failure_classifier.py` — Complete failure classification engine with pattern matching and recommendations
- `backend/app/incident_engine.py` — Incident aggregation, lifecycle management, and root cause analysis
- `backend/app/health_engine.py` — DLQ health scoring with multi-factor analysis
- `backend/app/destination_health.py` — Per-destination health analysis and metrics
- `backend/app/incident_scheduler.py` — Background scheduler for automatic incident detection based on thresholds
- `backend/alembic/versions/20260531_0007_add_dlq_intelligence.py` — Database migration for new columns and incidents table
- `backend/app/worker.py` — Updated to classify failures and create incidents on DLQ transition
- `backend/app/api_main.py` — Added 8 new DLQ intelligence API endpoints and integrated incident scheduler
- `frontend/dlq-intelligence.html` — Dedicated DLQ Intelligence dashboard UI
- `frontend/dlq-intelligence.js` — JavaScript for DLQ Intelligence dashboard with real-time updates
- `frontend/index.html` — Added navigation link to DLQ Intelligence page
- `frontend/app.js` — Updated navigation handler for DLQ Intelligence page

---

## Phase 12 — UI Redesign: Design Audit & Full Rebuild ✅

**Status: SHIPPED**
**Date Completed: 2026-06-03**

Complete visual redesign of the Hermes dashboard. The previous UI had patterns that signalled "AI-generated template" rather than a production-grade engineering tool.

### Design audit findings:
- Purple/indigo `#6366F1` accent on purple-tinted dark surfaces — the canonical "AI dashboard" colour stack
- `linear-gradient` on every interactive element (buttons, logo, avatar, health circles, trend bars)
- `transform: translateY(-2px)` hover lift on cards, buttons, and stat tiles
- Coloured `box-shadow` glows on buttons and avatars
- `backdrop-filter: blur` on modals
- 16px border-radius on cards (consumer-app feel)
- Button shimmer sweep `::before` pseudo-element animation
- 140px gradient health circle with scale-on-hover animation
- Uppercase + letter-spacing on every label (overused, defeats emphasis)
- 2×2 card grid for AI tools (no reading order)
- Destinations as a card grid (forces left-right card reading, not top-down scanning)
- Page title 24px/700 (too large for a dense ops tool)

### Phase 1 — Cosmetic pattern removal
- [x] All `linear-gradient` removed: buttons, logo, avatar, health circles (5 states), health banner fills, trend bars, DLQ score card, refresh button
- [x] Button shimmer sweep `::before` removed entirely
- [x] All `transform: translateY` hover lifts removed: buttons, stat cards, incident stats, dashboard cards, health circle scale
- [x] All coloured `box-shadow` (glow effects) removed
- [x] `backdrop-filter: blur` removed from modal overlays
- [x] `border-radius` reduced to 8px on all cards, modals, table wraps, KPI cards, auth card

### Phase 2 — Typography and spacing
- [x] Header height: 64px → 52px
- [x] Sidebar width: 240px → 216px
- [x] Page title: 24px/700 → 20px/600
- [x] All `text-transform: uppercase; letter-spacing` removed from data labels — reserved for nav section labels only
- [x] KPI value: 32px/700 → 28px/600 mono
- [x] Table `th`: uppercase removed, weight 500
- [x] Dashboard card `h3`: 18px → 13px
- [x] Incident stat value: 36px → 28px mono
- [x] Health banner dot: pulses only on DEGRADED/CRITICAL, not on healthy
- [x] Page padding tightened: 32/36px → 24/28px

### Phase 3 — Layout restructures
- [x] **Destinations**: card grid replaced with a table (`STATUS / NAME / URL / RETRIES / CIRCUIT / actions`). Circuit breaker OPEN banner added at top of page.
- [x] **AI Intelligence**: 2×2 card grid replaced with tabbed layout (Analyze / Suggest Filter / Suggest Transform), 40/60 input/result split panel per tab
- [x] **DLQ Intelligence**: 140px gradient circle removed. Replaced with compact score row (`92 / 100`, 4px vertical bar, status text, 4 inline stat counters). Grid restructured to 3-column analysis + 2-column root cause/incidents.
- [x] **Replay Jobs**: Side-by-side form/results replaced with top-bar form + full-width jobs table
- [x] **Dashboard lower grid**: 50/50 → 3:2 (throughput chart wider than failure list)

### Phase 4 — Components and JS wiring
- [x] Skeleton shimmer CSS (correct use: on skeleton blocks, not on cards)
- [x] Settings page rebuilt: API key + Ingestion Endpoint (auto-populated) + Danger Zone with delete
- [x] AI tab switcher `switchAiTab()` wired
- [x] Destinations JS rewritten to `renderDestTable()` with status dots, circuit text, action columns
- [x] `updateHealthScore()` drives score bar and border-left signal instead of removed gradient circle
- [x] Inline banner component (`.inline-banner--warn/danger/info`) for contextual alerts
- [x] Pre-existing dead `getElementById` calls in `loadStats` given null guards

### Colour palette swap
- [x] Replaced entire purple-indigo palette with Stripe + Linear language:
  - Background: `#0B0F14` (matte charcoal), surfaces: `#111827` / `#1C2636` / `#243248` (cool blue-slate, not purple-tinted)
  - Text: `#F8FAFC` / `#94A3B8` / `#64748B` (slate scale, no purple cast)
  - Accent: `#3B82F6` blue-500 — used only for: primary buttons, nav active, focus rings, key metrics
  - Status colours unchanged: `#10B981` success, `#F59E0B` warn, `#EF4444` danger
- [x] nginx cache headers changed from `immutable 7d` to `no-cache must-revalidate` for dev iteration

### Known remaining gaps (not production blockers, but tracked)
- ~~`frontend/dlq-intelligence.html` and `frontend/dlq-intelligence.js` are now orphaned~~ — deleted.
- ~~`backend/app/schema_validator.py` and `backend/app/sse_hub.py` are untracked~~ — confirmed wired: `schema_validator` imported by `routers/webhooks.py` and `routers/event_types.py`; `sse_hub` imported by `worker.py` and `routers/system.py`.

---

## Production Readiness Tracker

**Overall: 100% ✅**

| Area | Status | Notes |
|------|--------|-------|
| Core delivery | ✅ | FIFO, SELECT FOR UPDATE SKIP LOCKED, adaptive retry |
| Auth + multi-tenant | ✅ | httpOnly JWT, argon2, `timezone` import fix |
| Circuit breaker | ✅ | FOR UPDATE on state transitions |
| Rate limiting | ✅ | Postgres token-bucket, multi-replica safe |
| Retry logic | ✅ | Standard Webhooks compliant, 429/Retry-After aware |
| DLQ Intelligence | ✅ | Failure classification, incident lifecycle, health scoring |
| Observability | ✅ | Prometheus + OTel, circuit trip counters |
| Cloud adapters | ✅ | SNS, GCP Pub/Sub, Azure Event Grid |
| Migrations | ✅ | Alembic, 8 migrations clean |
| UI | ✅ | Stripe+Linear palette, flat design, table layouts |
| CI — test suite | ✅ | 95/95 passing (fixed `test_alerts.py` after router split, fixed `timezone` import) |
| Graceful shutdown | ✅ | Worker drains in-flight deliveries (35s window) before exit; `loop.add_signal_handler` |
| SSE hub | ✅ | `get_running_loop()` replacing deprecated `get_event_loop()` |
| Security headers | ✅ | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `HSTS` via middleware |
| Startup validation | ✅ | Refuses to start in production if JWT_SECRET is default or COOKIE_SECURE=false |
| Request size limit | ✅ | 1 MB cap on ingest endpoint (HTTP 413 with clear message) |
| TLS / HTTPS | ✅ | `nginx.prod.conf` with TLS, OCSP stapling, HSTS, /metrics IP allowlist |
| Dead file cleanup | ✅ | `dlq-intelligence.html`, `dlq-intelligence.js` deleted; `schema_validator.py`/`sse_hub.py` confirmed wired |
| `.env.example` | ✅ | `ALLOW_PRIVATE_DESTINATIONS=false`, full production checklist with `[PROD]` markers |
| Billing enforcement | ✅ | `MONTHLY_EVENT_QUOTA` config; hard HTTP 429 cutoff at ingest when set; 0=unlimited for self-hosted |
| Container ops | ✅ | Worker `stop_grace_period: 40s`; `FORCE_HTTPS`/`COOKIE_SECURE` env-driven in compose; real worker healthcheck |
| Audit log | ✅ | `audit_log` table (migration 0009); `audit.py` flush-before-commit; `GET /api/v1/audit-log`; wired into destinations CREATE/UPDATE/DELETE |
| CLI | ✅ | `hermes` Click CLI — ingest, status, stats, dlq (list/replay/replay-all/health), audit, listen (local tunnel) |
| Python SDK | ✅ | `pyproject.toml` (pip-publishable); sync `HermesClient` + async `AsyncHermesClient`; full type hints; send/fan_out/dlq/audit/destinations |
| JS SDK | ✅ | ESM+CJS dual export; `client.d.ts` TypeScript definitions; full method parity with Python SDK |
| Load test | ✅ | `benchmark/loadtest.py` — asyncio+httpx, configurable concurrency/duration, P50/P95/P99, JSON result output |
| OpenAPI docs | ✅ | Full description, 12 tagged endpoint groups, `/api/docs` Swagger UI, `/api/redoc` Redoc |
| README | ✅ | Complete overhaul — quickstart, feature table vs DIY, architecture diagram, config reference, production deploy, SDK examples, CLI reference, benchmark instructions |

---

## Changelog

| Date | Phase | Change |
|------|-------|--------|
| 2026-05-27 | 1 | Core MVP shipped — all 6 mindmap components built and tested |
| 2026-05-27 | 1 | Bug fix: Enum → String for Postgres status column |
| 2026-05-27 | 2 | Idempotency, signatures, API keys, tenant isolation, metrics, logging |
| 2026-05-27 | 2 | Fan-out routing, event filtering, payload transforms |
| 2026-05-27 | 2 | CI, Alembic migrations, demo script, fake downstream service |
| 2026-05-27 | 2 | Dashboard API key prompt, 16 unit tests, 5 integration tests |
| 2026-05-29 | — | Full roadmap documented (Phases 3–7) |
| 2026-05-29 | 3 | Added alert configs CRUD endpoints, credentials mask protection, test trigger |
| 2026-05-29 | 3 | High-fidelity dark-mode Alerts settings UI layout with Slack/SMTP tabs |
| 2026-05-29 | 3 | Added comprehensive AsyncMock unit tests for alerts API |
| 2026-05-29 | 4 | Built zero-dependency Python and JavaScript SDK clients with full docs |
| 2026-05-29 | 5 | Complete SaaS auth system with JWT, user accounts, projects, and team management |
| 2026-05-29 | 5 | Login/register pages with dark-mode UI and form validation |
| 2026-05-29 | 5 | Project switcher in dashboard header with project-scoped API calls |
| 2026-05-29 | 5 | Project management UI for creating/listing projects with API key display |
| 2026-05-29 | 5 | Team member management UI for inviting users with role-based permissions |
| 2026-05-29 | 5 | Database migration for User, Project, and ProjectMember tables |
| 2026-05-29 | 5 | Dual auth support: JWT for SaaS users, API key for legacy compatibility |
| 2026-05-29 | 6 | Production-ready multi-stage Dockerfile with non-root user and health checks |
| 2026-05-29 | 6 | Railway and Render one-click deploy configurations with Postgres integration |
| 2026-05-29 | 6 | HTTPS enforcement middleware with configurable FORCE_HTTPS setting |
| 2026-05-29 | 6 | Per-tenant rate limiting middleware (60 req/min default) on ingest endpoint |
| 2026-05-29 | 6 | Enhanced health check endpoints with database connectivity monitoring |
| 2026-05-29 | 7 | Complete Stripe webhook integration guide with signature verification |
| 2026-05-29 | 7 | Complete GitHub webhook integration guide with HMAC verification |
| 2026-05-29 | 7 | Added Integrations section to README with quick examples |
| 2026-05-29 | 8 | Fixed missing require_api_key function in auth.py for backward compatibility |
| 2026-05-29 | 8 | Switched from bcrypt to argon2 for password hashing to avoid 72-byte limit |
| 2026-05-29 | 8 | Improved frontend error handling with content-type checking before JSON parsing |
| 2026-05-29 | 8 | Fixed Docker container PYTHONPATH and permission issues |
| 2026-05-31 | 9 | Added failure classification enums and Incident model to database schema |
| 2026-05-31 | 9 | Created failure classifier engine with pattern matching and recommendations |
| 2026-05-31 | 9 | Implemented incident aggregation and lifecycle management engine |
| 2026-05-31 | 9 | Created DLQ health scoring engine with multi-factor analysis (0-100 score) |
| 2026-05-31 | 9 | Implemented growth analysis with trend classification (15m, 1h, 6h, 24h windows) |
| 2026-05-31 | 9 | Created destination health analysis module with per-destination metrics |
| 2026-05-31 | 9 | Updated worker to automatically classify failures and create incidents |
| 2026-05-31 | 9 | Added 8 new DLQ intelligence API endpoints for health, incidents, classifications, trends, and root causes |
| 2026-05-31 | 9 | Built dedicated DLQ Intelligence dashboard with health score, incidents, and root cause analysis |
| 2026-05-31 | 9 | Created incident scheduler for automatic incident detection based on health, growth, and failure rate thresholds |
| 2026-05-31 | 9 | Added AI analysis endpoint for human-readable DLQ failure analysis with recommendations |
| 2026-06-03 | 10 | Fixed PostgreSQL CLAIM_QUERY syntax, response_body_compressed migration, filter regex, circuit breaker race condition |
| 2026-06-03 | 10 | Added OpenTelemetry integration, AWS SNS / GCP Pub/Sub / Azure Event Grid adapters, DLQ archival endpoint |
| 2026-06-03 | 11 | Router split: api_main.py reduced to 103 lines, 11 router modules created |
| 2026-06-03 | 11 | Fixed async Anthropic client, SSRF in update_destination, password validation, replay crash recovery, Pydantic destination models |
| 2026-06-03 | 11 | Built /api/v1/dashboard single-call endpoint for overview page |
| 2026-06-03 | 12 | Full design audit: removed all gradients, hover lifts, coloured shadows, shimmer sweep, glassmorphism |
| 2026-06-03 | 12 | Phase 2: typography scale, header 52px, sidebar 216px, page titles 20px/600, table headers cleaned |
| 2026-06-03 | 12 | Phase 3: destinations table layout, AI Intelligence tabbed layout, DLQ Intelligence score row, Replay Jobs form bar |
| 2026-06-03 | 12 | Phase 4: skeleton CSS, Settings page rebuilt (API key + ingestion URL + danger zone), AI tab switcher wired |
| 2026-06-03 | 12 | Palette swap: indigo #6366F1 → electric blue #3B82F6, purple-tinted blacks → cool blue-slate (Stripe + Linear language) |
| 2026-06-03 | 12 | nginx cache headers: immutable 7d → no-cache must-revalidate for dev iteration |
| 2026-06-03 | 13 | Fixed CI: test_alerts.py rewritten to import from app.routers.alerts after router split |
| 2026-06-03 | 13 | Fixed auth.py: missing `timezone` import caused login endpoint to 500 in production |
| 2026-06-03 | 13 | Fixed worker graceful shutdown: stop() now drains in-flight deliveries (35s) before cancel |
| 2026-06-03 | 13 | Fixed worker_main.py: loop.add_signal_handler replaces signal.signal for asyncio correctness |
| 2026-06-03 | 13 | Fixed sse_hub.py: asyncio.get_running_loop() replaces deprecated get_event_loop() |
| 2026-06-03 | 13 | Added startup config validation: API refuses to start in production with default JWT_SECRET |
| 2026-06-03 | 13 | Added SecurityHeadersMiddleware: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS |
| 2026-06-03 | 13 | Added 1 MB body size limit on ingest endpoint (HTTP 413 on oversized payloads) |
| 2026-06-03 | 13 | Created nginx.prod.conf: TLS 1.2/1.3, OCSP stapling, HSTS preload, /metrics IP allowlist |
| 2026-06-04 | 14 | Added MONTHLY_EVENT_QUOTA config + hard 429 quota gate at ingest (0=unlimited, uses existing tenant/created_at index) |
| 2026-06-04 | 14 | Fixed docker-compose: FORCE_HTTPS/COOKIE_SECURE were hardcoded false, now env-driven; MONTHLY_EVENT_QUOTA wired |
| 2026-06-04 | 14 | Added stop_grace_period: 40s to worker service (covers the 35s drain window) |
| 2026-06-04 | 14 | Fixed worker healthcheck: was always-pass no-op; now checks /proc/1/stat for zombie state |
| 2026-06-04 | 14 | Confirmed schema_validator.py and sse_hub.py fully wired (routers/webhooks, routers/event_types, worker, routers/system) |
| 2026-06-04 | 14 | Added audit_log table (migration 0009), AuditLog model, audit.py module; wired into destinations CREATE/UPDATE/DELETE |
| 2026-06-04 | 14 | Added GET /api/v1/audit-log endpoint with resource_type, action, pagination filters |
| 2026-06-04 | 14 | Built hermes CLI: ingest, status, stats, dlq (list/replay/replay-all/health), audit, listen (local tunnel); installable via pip install ./cli |
| 2026-06-04 | 14 | Modernized Python SDK: pyproject.toml, HermesError, AsyncHermesClient, fan_out, dlq_health, get_audit_log, list_destinations |
| 2026-06-04 | 14 | Modernized JS SDK: ESM+CJS dual export, TypeScript client.d.ts, full method parity, HermesError class, AbortController timeout |
| 2026-06-04 | 14 | Added benchmark/loadtest.py: asyncio+httpx, configurable concurrency/duration, P50/P95/P99 output, last_result.json |
| 2026-06-04 | 14 | Enhanced FastAPI OpenAPI metadata: full description, auth docs, 12 tagged endpoint groups, /api/docs + /api/redoc |
| 2026-06-04 | 14 | Overhauled README: feature table vs DIY/hosted, quickstart, architecture, config reference, production deploy, SDK+CLI examples |
| 2026-06-03 | 13 | Deleted orphaned dlq-intelligence.html and dlq-intelligence.js |
| 2026-06-03 | 13 | Fixed .env.example: ALLOW_PRIVATE_DESTINATIONS=false, full [PROD] checklist |
| 2026-06-03 | 13 | Test suite: 95/95 passing, 0 errors |
