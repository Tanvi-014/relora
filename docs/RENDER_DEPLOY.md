# Deploying Relora on Render (Free, No Credit Card)

**What you'll need:**
- [Render account](https://render.com) — free, no credit card
- [Neon account](https://neon.tech) — free Postgres, no credit card
- Your repo pushed to GitHub

> **Heads up:** Render free web services sleep after 15 min of no traffic. The delivery worker never sleeps. For 2-3 users, use [UptimeRobot](https://uptimerobot.com) (free) to ping `/health` every 5 min and keep the API awake.

---

## Step 1 — Set up Postgres on Neon

1. Sign up at [neon.tech](https://neon.tech) and create a project named `relora`
2. Copy the connection string and make two variants:

   **`DATABASE_URL`** (async — swap prefix, drop `?sslmode=require`):
   ```
   postgresql+asyncpg://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb
   ```

   **`SYNC_DATABASE_URL`** (keep as-is, used for migrations):
   ```
   postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```

---

## Step 2 — Deploy via Blueprint (one click)

Render picks up `render.yaml` from your repo automatically.

1. Go to [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect your GitHub account and select your Relora repo
3. Render reads `render.yaml` and shows you two services: `relora-api` and `relora-worker`
4. Click **Apply** — Render starts building both

The build takes a few minutes (Docker build on a cold runner).

---

## Step 3 — Set the database secrets

`DATABASE_URL` and `SYNC_DATABASE_URL` are marked `sync: false` in `render.yaml` — Render won't create them automatically, you set them manually.

For **each service** (`relora-api` and `relora-worker`):

1. Open the service → **Environment** tab
2. Add `DATABASE_URL` → paste your asyncpg URL from Step 1
3. For `relora-api` only: also add `SYNC_DATABASE_URL` → paste your sync URL

Then click **Save Changes** — Render redeploys automatically.

> `JWT_SECRET` is handled automatically — `generateValue: true` in `render.yaml` tells Render to generate a secure random value for you.

---

## Step 4 — Verify

Once both services show **Live**:

1. `https://relora-api.onrender.com/health` → `{"status":"ok"}`
2. `https://relora-api.onrender.com/app.html` → dashboard loads
3. Register and send a demo event — the worker delivers it within seconds

Migrations run automatically via `preDeployCommand` before each deploy.

---

## Step 5 — Keep the API awake (optional but recommended)

1. Sign up at [uptimerobot.com](https://uptimerobot.com) — free, no card
2. **New Monitor** → HTTP(s)
3. URL: `https://relora-api.onrender.com/health`
4. Interval: **5 minutes**

This prevents the API cold-starting when a webhook arrives.

---

## Updating Relora

Push to GitHub → Render auto-deploys both services. Migrations run automatically before the new API version goes live.

---

## Optional: Enable AI features

In `relora-api` → **Environment** tab, add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `ENABLE_AI_FEATURES` | `true` |
