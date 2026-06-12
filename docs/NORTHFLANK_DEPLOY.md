# Deploying Relora on Northflank (Free, No Credit Card)

**What you'll need:**
- [Northflank account](https://app.northflank.com/signup) — free, no credit card
- [Neon account](https://neon.tech) — free Postgres, no credit card
- Your repo pushed to GitHub

---

## Step 1 — Set up Postgres on Neon

1. Sign up at [neon.tech](https://neon.tech) and create a project named `relora`
2. Copy the connection string — you need two variants:

   **`DATABASE_URL`** (async — swap the prefix, drop `?sslmode=require`):
   ```
   postgresql+asyncpg://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb
   ```

   **`SYNC_DATABASE_URL`** (keep as-is, used for migrations):
   ```
   postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```

---

## Step 2 — Generate a JWT secret

```bash
openssl rand -hex 32
```

Save the output as your `JWT_SECRET`.

---

## Step 3 — Create a Northflank project

1. Log in to [app.northflank.com](https://app.northflank.com)
2. Click **New Project** → name it `relora`

---

## Step 4 — Deploy the API service

1. Inside the project, click **New Service** → **Combined Service**
2. Connect your GitHub account and select your Relora repo
3. Under **Build settings**:
   - Build type: **Dockerfile**
   - Dockerfile path: `backend/Dockerfile`
   - Build context: `/` (repo root)
4. Under **Deployment**:
   - Start command: `uvicorn app.api_main:app --host 0.0.0.0 --port 8000`
   - Port: `8000` (HTTP)
5. Under **Resources**: select the **Free** plan
6. Under **Environment variables → Secret**, add:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | your asyncpg URL from Step 1 |
   | `SYNC_DATABASE_URL` | your sync URL from Step 1 |
   | `JWT_SECRET` | your secret from Step 2 |

   Under **Environment variables → Plain**, add:

   | Variable | Value |
   |---|---|
   | `ENVIRONMENT` | `production` |
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

7. Name it `relora-api` and click **Create Service**

   Once deployed, Northflank gives you a public URL like `https://relora-api-xxx.northflank.app`. Copy it and add one more env var:

   | Variable | Value |
   |---|---|
   | `INTERNAL_API_URL` | your Northflank app URL |

   Save and redeploy.

---

## Step 5 — Run database migrations

In the Northflank dashboard, go to your `relora-api` service → **Shell** tab and run:

```bash
cd /app/backend && alembic upgrade head
```

Or run it as a one-off **Job** in Northflank (New Job → same Dockerfile, command: `sh -c "cd /app/backend && alembic upgrade head"`).

---

## Step 6 — Deploy the Worker service

1. **New Service** → **Combined Service** (same repo)
2. Same **Dockerfile** path and build context
3. Under **Deployment**:
   - Start command: `python -m app.worker_main`
   - **No port** — this is a background process
4. **Free** plan
5. Under **Environment variables → Secret**:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | your asyncpg URL from Step 1 |

   Under **Environment variables → Plain**:

   | Variable | Value |
   |---|---|
   | `ENVIRONMENT` | `production` |
   | `ALLOW_PRIVATE_DESTINATIONS` | `false` |
   | `WORKER_CONCURRENCY` | `2` |
   | `WORKER_POLL_INTERVAL_SECONDS` | `1` |
   | `DEFAULT_MAX_RETRIES` | `5` |
   | `BACKOFF_BASE_SECONDS` | `30` |
   | `HTTP_CLIENT_TIMEOUT_SECONDS` | `10` |
   | `ENABLE_AI_FEATURES` | `false` |

6. Name it `relora-worker` and deploy

---

## Step 7 — Verify

1. `https://relora-api-xxx.northflank.app/health` → `{"status":"ok"}`
2. `https://relora-api-xxx.northflank.app/app.html` → dashboard loads
3. Register, send a demo event — worker should deliver it within a few seconds

---

## Updating Relora

Push to GitHub — Northflank auto-deploys if you enabled auto-deploy on the service, or trigger manually from the dashboard. Run `alembic upgrade head` via the shell after any schema-changing update before the new version goes live.

---

## Optional: Enable AI features

Add to both services:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `ENABLE_AI_FEATURES` | `true` |
