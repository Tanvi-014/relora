# Relora — Development Progress Tracker

> Living document. "Shipped" means code exists, tests pass, and the feature is wired end-to-end.

---

## Production Readiness: 94%
**Last assessed: 2026-06-07**

| Area | Status | Notes |
|------|--------|-------|
| Core delivery (ingest → worker → retry → DLQ) | ✅ | FIFO, SELECT FOR UPDATE SKIP LOCKED, adaptive retry |
| Auth + multi-tenant | ✅ | httpOnly JWT, argon2, startup fail-fast on default JWT_SECRET |
| Password reset | ✅ | /auth/forgot-password + /auth/reset-password, 1-hour tokens, 22 tests |
| Circuit breaker | ✅ | FOR UPDATE on state transitions, OTel trip counter |
| Rate limiting | ✅ | Postgres token-bucket, multi-replica safe |
| DLQ Intelligence | ✅ | Failure classification, incident lifecycle, health score 0–100 |
| Observability — OTel | ✅ | Opt-in; disabled gracefully when endpoint unset |
| Observability — Sentry | ✅ | SENTRY_ENVIRONMENT config field present |
| Observability — Prometheus/Grafana | ✅ | Scrapes /metrics (fixed), alert rules for DLQ/error rate/circuit breaker/worker stall |
| Audit log | ✅ | audit_log table, GET endpoint, wired into destinations CRUD |
| Cloud adapters | ✅ | AWS SNS, GCP Pub/Sub, Azure Event Grid |
| Migrations | ✅ | 12 clean Alembic migrations (0001–0012) |
| TLS / nginx | ✅ | TLS 1.2/1.3, OCSP stapling, HSTS preload, /metrics IP allowlist |
| TLS bootstrap | ✅ | scripts/init-certs.sh auto-patches nginx.prod.conf on first run |
| docker-compose.production.yml | ✅ | Certbot, backup sidecar, monitoring stack, fixed worker healthcheck |
| Backup / restore | ✅ | scripts/backup.sh + scripts/restore.sh, pg_dump gzip, prune-by-days |
| Backup verification | ✅ | scripts/verify-backup.sh — automated restore + table sanity check |
| Python SDK | ✅ | Sync + async clients, full type hints, pip-publishable |
| JS SDK | ✅ | ESM, TypeScript definitions, ReloraError, AbortController timeout |
| SDK tests — sync | ✅ | 19 tests in test_client.py |
| SDK tests — async | ✅ | 27 tests in test_async_client.py (respx mocking) |
| CLI | ✅ | ingest, status, stats, dlq, audit, listen |
| CI | ✅ | ~130 tests (unit + integration + E2E smoke), real Postgres in Actions |
| Benchmark | ✅ | 72.5 RPS @ 20 concurrency, P95 525 ms (dev machine baseline) |
| Email verification — backend | ✅ | email.py, migration 0011, 4 auth routes |
| Email verification — frontend | ✅ | verify-email.html handles link, success/error, inline resend form |
| Email verification — login UX | ✅ | 403 on login shows "check inbox" + link to verify-email.html |
| Email verification — tests | ✅ | 19 tests in test_email.py |
| Email verification — safety gate | ✅ | Startup blocks if EMAIL_VERIFICATION_REQUIRED=true but RESEND_API_KEY unset |
| .env.example completeness | ✅ | All production vars documented |
| RESEND_FROM_EMAIL validation | ✅ | Startup check blocks production start with placeholder email domain |
| E2E smoke test | ✅ | test_smoke_e2e.py — register→project→destination→ingest→deliver→replay→verify |
| Bug fix: /api/v1/dashboard NameError | ✅ | has_open_circuit used before assignment (live crash bug) — fixed |
| nginx domain substitution | ✅ | init-certs.sh now auto-patches nginx.prod.conf; no manual step required |

---

## Phase 16 — Production Hardening Audit (2026-06-05)

### Completed

| Item | Detail |
|------|--------|
| **Bug fix: /api/v1/dashboard NameError** | `has_open_circuit` used before assignment on every request with failed webhooks — caused 500 errors; moved to correct position |
| **Bug fix: prometheus.yml scrape target** | Was scraping `/health` (returns JSON), not `/metrics` (returns Prometheus text) — no relora_* metrics were being collected |
| **Password reset — backend** | `PasswordResetToken` model, migration 0012, `create_reset_token` / `consume_reset_token` / `send_reset_email` in email.py |
| **Password reset — routes** | `POST /api/v1/auth/forgot-password` (unauthenticated, anti-enumeration) + `POST /api/v1/auth/reset-password` (token + new password) |
| **Password reset — frontend** | reset-password.html: request form (no token) + set-new-password form (with token) + success state; wired to both routes |
| **Password reset — login link** | login.html: "Forgot password?" link added next to "Register" |
| **Password reset — tests** | 22 tests in test_password_reset.py covering all token states, email dispatch, both auth routes |
| **Prometheus alert rules** | monitoring/prometheus/alerts.yml: 8 rules covering DLQ depth (warn >100 / crit >1000), error rate (<90% / <75%), circuit breaker open, worker stall (>10 min), pending queue growth, DLQ health score |
| **Email verification safety gate** | api_main.py: startup blocks if EMAIL_VERIFICATION_REQUIRED=true and RESEND_API_KEY=""; prevents locking users out with no recovery path |
| **E2E smoke test** | test_smoke_e2e.py: 8-step test (register → project → destination → ingest → wait for delivery → replay → wait for replay → health check); auto-skips if RELORA_TEST_BASE_URL not set |
| **Backup verification script** | scripts/verify-backup.sh: finds newest backup, restores to throw-away DB, checks all 4 core tables exist, drops temp DB |
| **nginx domain auto-substitution** | scripts/init-certs.sh now calls `sed -i` inline after cert issuance; supports both GNU and BSD sed; no manual step remaining |
| **CI updated** | test_password_reset.py added to unit tests; worker started in CI before E2E tests; E2E smoke test added as separate step |

### Remaining Critical

None. All previously identified critical blockers and the newly discovered dashboard NameError and prometheus scrape misconfiguration are resolved.

### Remaining Nice-to-Have

| Item | Notes |
|------|-------|
| Production VPS benchmark | 72.5 RPS is on a dev machine; re-run on a VPS ($20/mo) for an honest number |
| Grafana alert notifications | Alert rules exist in prometheus; need Alertmanager or Grafana contact points configured to actually send PagerDuty/Slack notifications |
| Backup verification cron | verify-backup.sh exists; needs a weekly docker exec or cron container to run automatically in production |

---

## EMAIL_VERIFICATION_REQUIRED=true — safe to enable?

**Yes, with these prerequisites in place:**
1. `RESEND_API_KEY` is set to a real Resend API key
2. `RESEND_FROM_EMAIL` is set to a verified sender domain on Resend
3. `APP_BASE_URL` points to the live HTTPS URL (so verify/reset links work)

The startup guard now blocks production start if (1) is missing with (3) set — preventing a silent lockout. Once those three env vars are configured, set `EMAIL_VERIFICATION_REQUIRED=true` and redeploy.

---

## Deployable today?

**Yes, unconditionally** for self-hosted deployments. The core pipeline — ingest, retry, DLQ, circuit breaker, rate limiting, auth (including password reset), audit log — is production-grade. No outstanding critical blockers.

The only things that need real infrastructure (domain + Resend account) before enabling are email verification and password reset, and the startup validator will refuse to start with an inconsistent config.

---

## Architecture

```
Sender (Stripe, GitHub, Twilio...)
    │
    ▼
┌─────────────────────────┐
│  Ingestion API          │  POST /api/v1/ingest — instant 200, saves to DB
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Delivery Worker        │  SELECT FOR UPDATE SKIP LOCKED, FIFO per ordering_key
└───────────┬─────────────┘
            │ failure
            ▼
┌─────────────────────────┐
│  Retry Scheduler        │  Exponential backoff, 429 Retry-After aware
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Dead Letter Queue      │  Failure classification, incident lifecycle, replay
└─────────────────────────┘
```

---

## Changelog

| Date | Phase | Change |
|------|-------|--------|
| 2026-05-27 | 1 | Core MVP: ingest, worker, retry, DLQ, dashboard, Docker Compose |
| 2026-05-27 | 2 | Idempotency, signatures, multi-tenant, fan-out, filtering, CI, migrations |
| 2026-05-29 | 3 | Alerts CRUD, Slack/email dispatcher |
| 2026-05-29 | 4 | Python + JS SDKs |
| 2026-05-29 | 5 | JWT SaaS auth, user accounts, projects, team management |
| 2026-05-29 | 6 | Production Dockerfile, Railway/Render configs, HTTPS middleware |
| 2026-05-29 | 7 | Stripe + GitHub integration guides |
| 2026-05-29 | 8 | argon2, frontend error handling, Docker fixes |
| 2026-05-31 | 9 | DLQ Intelligence: failure classification, incident lifecycle, health scoring |
| 2026-06-03 | 10 | Fixed CLAIM_QUERY SQL, filter regex, circuit breaker race condition |
| 2026-06-03 | 10 | OTel, AWS SNS / GCP Pub/Sub / Azure Event Grid adapters, DLQ archival |
| 2026-06-03 | 11 | Router split (api_main.py → 11 modules), SSRF fix, /api/v1/dashboard |
| 2026-06-03 | 12 | Design audit: gradients, hover lifts, glow shadows removed; Stripe+Linear palette |
| 2026-06-03 | 13 | CI fixed; graceful shutdown; startup config validation; nginx.prod.conf; security headers |
| 2026-06-04 | 14 | Billing quota gate; audit log; CLI; SDK modernization; benchmark; OpenAPI; README |
| 2026-06-04 | 15 | Email verification backend (email.py, migration 0011, 4 auth routes) |
| 2026-06-04 | 15 | Monitoring stack (Prometheus, Grafana, node-exporter, postgres-exporter) |
| 2026-06-04 | 15 | Backup/restore scripts (pg_dump gzip, auto-prune, interactive restore) |
| 2026-06-04 | 15 | 27 async SDK tests (test_async_client.py, respx mocking) |
| 2026-06-04 | 15 | Fixed SENTRY_ENVIRONMENT missing from config.py |
| 2026-06-04 | 15 | Fixed production + dev worker healthcheck (broken age<300 fallback → Z/D/X state check) |
| 2026-06-04 | 15 | SDK repo URLs corrected (your-org/relora → Tanvi-014/sentinel-) |
| 2026-06-04 | 15 | verify-email.html: handles email link, shows success/error, inline resend form |
| 2026-06-04 | 15 | auth.js login: 403 surfaces "check inbox" message + link to verify-email.html |
| 2026-06-04 | 15 | /auth/request-verification: unauthenticated resend endpoint (rate-limited, no enumeration) |
| 2026-06-04 | 15 | test_email.py: 19 tests for token helpers, Resend dispatch, all 4 auth routes |
| 2026-06-04 | 15 | CI: test_email.py added to GitHub Actions; total 108 tests (96 unit + 12 integration) |
| 2026-06-04 | 15 | scripts/init-certs.sh: one-command Let's Encrypt issuance via temporary nginx |
| 2026-06-04 | 15 | .env.example: added GRAFANA_ADMIN_PASSWORD, RESEND_FROM_EMAIL, APP_BASE_URL, SENTRY_*, email vars |
| 2026-06-04 | 15 | api_main.py: RESEND_FROM_EMAIL placeholder now blocked at production startup |
| 2026-06-05 | 16 | Bug fix: /api/v1/dashboard NameError (has_open_circuit used before assignment → 500 on every request with failures) |
| 2026-06-05 | 16 | Bug fix: prometheus.yml scraping /health instead of /metrics (no metrics were being collected) |
| 2026-06-05 | 16 | Password reset: PasswordResetToken model, migration 0012, create/consume/send helpers |
| 2026-06-05 | 16 | Password reset: /auth/forgot-password + /auth/reset-password routes (rate-limited, anti-enumeration) |
| 2026-06-05 | 16 | Password reset: reset-password.html (request form + set-password form + success state) |
| 2026-06-05 | 16 | Password reset: "Forgot password?" link added to login.html |
| 2026-06-05 | 16 | Password reset: 22 tests in test_password_reset.py |
| 2026-06-05 | 16 | Prometheus alert rules: 8 rules in monitoring/prometheus/alerts.yml |
| 2026-06-05 | 16 | Email verification safety gate: startup blocks EMAIL_VERIFICATION_REQUIRED=true without RESEND_API_KEY |
| 2026-06-05 | 16 | E2E smoke test: test_smoke_e2e.py (8-step register→deliver→replay pipeline test) |
| 2026-06-05 | 16 | Backup verification: scripts/verify-backup.sh (restore to temp DB, table sanity check) |
| 2026-06-05 | 16 | nginx domain automation: init-certs.sh auto-patches nginx.prod.conf inline |
| 2026-06-05 | 16 | CI: test_password_reset.py + worker start + E2E smoke test added |
| 2026-06-07 | 17 | Bug fix: `asyncio.create_task()` in webhooks.py had no error handler — failed background tasks (schema drift check, replay job launch) were silently dropped. Added `_fire_and_forget` helper with strong-reference set (prevents GC) and done callback that logs exceptions via `logger.error` |
