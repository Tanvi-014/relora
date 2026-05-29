# Hermes Development Progress

Living project log for turning Hermes from MVP into a deliverable portfolio system.

## Phase 1: Core MVP

Status: Complete  
Completed: 2026-05-27

Built:

- FastAPI ingestion endpoint: `POST /api/v1/ingest?url=<destination>`.
- PostgreSQL-backed webhook store and queue.
- Concurrent async worker pool using `SELECT ... FOR UPDATE SKIP LOCKED`.
- Exponential retry scheduler.
- Dead letter state after max retries.
- Manual replay endpoint.
- Vanilla JS dashboard for stats, filtering, inspection, attempts, payloads, headers, and replay.
- Docker Compose local stack.

Validated manually:

- Health check.
- Webhook ingestion.
- Successful delivery.
- Failed delivery with retry scheduling.
- Stats API.
- Concurrent worker claiming.

## Phase 2: Production Readiness

Status: Started  
Started: 2026-05-27

Completed:

- Added real `.gitignore`.
- Added `.env.example`.
- Reworked settings to use typed `pydantic-settings` defaults.
- Added optional `X-Hermes-API-Key` enforcement.
- Added destination URL validation.
- Added optional private destination blocking for production.
- Added optional destination host allowlist.
- Added queue and attempt indexes.
- Added Alembic migration scaffolding and initial schema migration.
- Added backend unit tests for security validation and retry backoff.
- Added GitHub Actions CI for backend tests.
- Added dashboard API-key prompt with localStorage persistence.
- Added Prometheus-style `/metrics` endpoint.
- Added structured JSON logging for API and worker lifecycle events.
- Added idempotency support with `Idempotency-Key` and `X-Hermes-Idempotency-Key`.
- Added live API integration tests gated by `HERMES_TEST_BASE_URL`.
- Added `backend/scripts/demo.py` for a one-command product walkthrough.
- Added a deterministic fake downstream service for local demos and integration tests.
- Fixed an idempotency race where immediate duplicate ingestion could hit the unique constraint before the first request committed.
- Added optional signature verification for Stripe, GitHub, and generic Hermes HMAC signatures.
- Added fan-out routing with `url` plus repeated/comma-separated `urls`.
- Added simple event filtering with expressions like `event.type == 'payment.succeeded'`.
- Added payload transformation with JSON field mapping.
- Added tenant-aware API key mapping with `HERMES_API_KEYS`.
- Added tenant/event correlation fields and `/api/v1/usage` for billing-style usage counts.
- Enriched worker logs with `tenant_id`, `event_id`, `destination_url`, `attempt_number`, and `response_status`.
- Cleaned README for GitHub and resume presentation.
- Updated Docker Compose configuration for new settings.

Verified:

- Python syntax compilation for `backend/app` and `backend/alembic`.
- Docker image build.
- Docker Compose startup for PostgreSQL and Hermes API.
- `/health` endpoint returns healthy.
- `/metrics` endpoint returns Prometheus-style metrics.
- Backend unit tests pass with `python -m pytest` inside the Hermes container.
- Demo script runs successfully and proves health, successful delivery, duplicate idempotency reuse, failed delivery retry state, replay, and stats.
- Self-contained Docker demo passes using `http://downstream:9000/ok` and `http://downstream:9000/fail`.
- Full container test suite now passes: 16 passed, 5 skipped when live integration env is not set.
- Live API integration tests now pass: 5 passed, including success, failure retry, metrics, usage, fan-out, filtering, and transform.
- Expanded demo script passes and proves fan-out plus filtered non-matching events.
- Successful webhook delivery to `https://httpbin.org/status/200`.
- Failed webhook delivery to `https://httpbin.org/status/500` records an attempt and schedules retry.
- Fake downstream service provides local `/ok`, `/fail`, `/flaky/{key}`, `/events`, and `/reset` endpoints.
- Dashboard loads in the browser with live webhook rows and no console errors.

## Next Tasks

- Add true multi-user auth and project-scoped destinations.
- Add Stripe CLI walkthrough and screenshots/GIFs for the README.
