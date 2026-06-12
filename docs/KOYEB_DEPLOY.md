# Deploying Relora on Koyeb (Free, No Credit Card)

**What you'll need:**
- [Koyeb account](https://app.koyeb.com/auth/signup) — free, no credit card
- [Neon account](https://neon.tech) — free Postgres, no credit card
- Your repo pushed to GitHub

---

## Step 1 — Set up Postgres on Neon

1. Sign up at [neon.tech](https://neon.tech) and create a new project (name it `relora`)
2. Neon will give you a connection string like:
   ```
   postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```
3. Copy it — you'll need two variants:
   - **`DATABASE_URL`** (async): replace `postgresql://` with `postgresql+asyncpg://` and remove `?sslmode=require`
     ```
     postgresql+asyncpg://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb
     ```
   - **`SYNC_DATABASE_URL`** (sync, for migrations): keep as-is
     ```
     postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
     ```

---

## Step 2 — Generate a JWT secret

Run this locally (or use any random hex generator):
```bash
openssl rand -hex 32
```
Save the output — this is your `JWT_SECRET`.

---

## Step 3 — Deploy the API service on Koyeb

1. Go to [app.koyeb.com](https://app.koyeb.com) → **Create Service**
2. Choose **GitHub** as the source and select your Relora repo
3. Set **Builder** to **Dockerfile** and set the Dockerfile path to:
   ```
   backend/Dockerfile
   ```
4. Set the **Start command** to:
   ```
   uvicorn app.api_main:app --host 0.0.0.0 --port 8000
   ```
5. Set **Port** to `8000`
6. Set **Instance type** to **Free** (nano)
7. Under **Environment variables**, add all of the following:

   | Variable | Value |
   |---|---|
   | `ENVIRONMENT` | `production` |
   | `DATABASE_URL` | your asyncpg URL from Step 1 |
   | `SYNC_DATABASE_URL` | your sync URL from Step 1 |
   | `JWT_SECRET` | your secret from Step 2 |
   | `AUTO_CREATE_TABLES` | `false` |
   | `ALLOW_PRIVATE_DESTINATIONS` | `false` |
   | `FORCE_HTTPS` | `true` |
   | `COOKIE_SECURE` | `true` |
   | `WORKER_CONCURRENCY` | `3` |
   | `DEFAULT_MAX_RETRIES` | `5` |
   | `BACKOFF_BASE_SECONDS` | `30` |
   | `HTTP_CLIENT_TIMEOUT_SECONDS` | `10` |
   | `RATE_LIMIT_PER_MINUTE` | `60` |
   | `MONTHLY_EVENT_QUOTA` | `0` |
   | `ENABLE_SIMULATOR` | `false` |
   | `ENABLE_AI_FEATURES` | `false` |

8. Set the **Health check** path to `/health`
9. Name the service `relora-api` and deploy

   Once deployed, Koyeb gives you a URL like `https://relora-api-yourname.koyeb.app`. Copy it.

10. Go back to the service's environment variables and add one more:

    | Variable | Value |
    |---|---|
    | `INTERNAL_API_URL` | your Koyeb app URL (e.g. `https://relora-api-yourname.koyeb.app`) |

    Redeploy after adding this.

---

## Step 4 — Run database migrations

Once the API service is deployed and healthy, open the Koyeb service shell (or use the Koyeb CLI):

```bash
# Via Koyeb web shell (Service → Shell tab):
cd /app/backend && alembic upgrade head
```

Or locally if you have the database URL:
```bash
cd backend
SYNC_DATABASE_URL="postgresql://..." alembic upgrade head
```

---

## Step 5 — Deploy the Worker service on Koyeb

1. **Create Service** again → same GitHub repo
2. Same **Dockerfile** path: `backend/Dockerfile`
3. Set the **Start command** to:
   ```
   python -m app.worker_main
   ```
4. Set **Instance type** to **Free** (nano)
5. **No port** needed — this is a background worker, not a web service
6. Under **Environment variables**, add:

   | Variable | Value |
   |---|---|
   | `ENVIRONMENT` | `production` |
   | `DATABASE_URL` | your asyncpg URL from Step 1 |
   | `ALLOW_PRIVATE_DESTINATIONS` | `false` |
   | `WORKER_CONCURRENCY` | `2` |
   | `WORKER_POLL_INTERVAL_SECONDS` | `1` |
   | `DEFAULT_MAX_RETRIES` | `5` |
   | `BACKOFF_BASE_SECONDS` | `30` |
   | `HTTP_CLIENT_TIMEOUT_SECONDS` | `10` |
   | `ENABLE_AI_FEATURES` | `false` |

7. Name it `relora-worker` and deploy

---

## Step 6 — Verify it's working

1. Visit your API URL: `https://relora-api-yourname.koyeb.app/health` — should return `{"status":"ok"}`
2. Visit the dashboard: `https://relora-api-yourname.koyeb.app/app.html`
3. Register an account and send a demo event — if the worker is running, it will deliver within a few seconds

---

## Memory tips for the free tier

Koyeb nano instances have 256MB RAM. To stay within limits:

- Keep `WORKER_CONCURRENCY=2` on the worker (set above)
- Keep `WORKER_CONCURRENCY=3` on the API (set above)
- Don't enable `ENABLE_SIMULATOR=true` — it adds background load
- If the worker crashes with OOM, reduce `WORKER_CONCURRENCY` to `1`

---

## Optional: Enable AI features

If you want the DLQ root cause analysis and weekly insights Q&A:

1. Get an Anthropic API key at [console.anthropic.com](https://console.anthropic.com)
2. Add to both the API and worker services:

   | Variable | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | `sk-ant-...` |
   | `ENABLE_AI_FEATURES` | `true` |

---

## Updating Relora

Push to your GitHub repo — Koyeb auto-deploys on every push if you enabled auto-deploy, or trigger manually from the Koyeb dashboard.

For migrations after a schema-changing update, run `alembic upgrade head` via the shell before the new API version starts serving traffic.
