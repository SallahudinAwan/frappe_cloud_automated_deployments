# Frappe Cloud Automated Deployments (Webhook Middleware)

This repository contains a small Flask service that:

- Receives **Frappe Cloud** webhooks (Bench/Site/Deploy Candidate Build events)
- Receives **GitHub** webhooks (pull_request/workflow_run events)
- Posts updates to **Google Chat** (cards + text)
- Can trigger an automated deployment run via the **Press API**

## Entry Points

- `app.py`: Flask HTTP server (webhooks + helper endpoints)
- `auto_deploy.py`: one-off deployment runner (invoked by `/trigger-workflow/<env>`)

## Code Layout

The implementation is now modularized under:

- `frappe_cloud_deploy_middleware/`
  - `config.py`: env var config + environment mappings
  - `db.py`: SQLAlchemy engine + DB helpers for deployment lock and GitHub thread mapping
  - `utils.py`: time + HTML→text helpers
  - `env.py`: optional `.env` loader (local/dev)
  - `home.py`: health check route
  - `github/`: GitHub webhooks + card payloads
  - `frappe_cloud/`: Frappe Cloud webhooks + deploy runner + Press API helpers

Legacy monolithic versions are kept for reference:

- `legacy/app_monolith.py`
- `legacy/auto_deploy_monolith.py`

## Configuration (Environment Variables)

Required for most flows:

- `DATABASE_URL`
- `FC_API_KEY`
- `FC_API_SECRET`
- `GOOGLE_CHAT_WEBHOOK`

Frappe Cloud environment mapping (required for correct routing of webhooks):

This repo ships with **dummy placeholder** site/bench values in `frappe_cloud_deploy_middleware/config.py`.
Before running, map your real Frappe Cloud **Site names** and **Bench/Release Group names** to the environment labels:
`Staging`, `Preview`, `Production`, `Version16`.

Recommended (keep sensitive names out of git): set these env vars:

- `SITE_ENV_MAP_JSON` (JSON object: `{ "<site-name>": "<env-label>" }`)
- `BENCH_ENV_MAP_JSON` (JSON object: `{ "<bench-name>": "<env-label>" }`)

Example:

```bash
SITE_ENV_MAP_JSON='{"my-staging-site.example.com":"Staging","my-prod-site.example.com":"Production"}'
BENCH_ENV_MAP_JSON='{"bench-12345":"Staging","bench-67890":"Production"}'
```

Alternative: edit `SITE_ENV_MAP` and `BENCH_ENV_MAP` directly in `frappe_cloud_deploy_middleware/config.py`.

GitHub integration:

- `GITHUB_TOKEN`
- `GITHUB_WEBHOOK_SECRET` (recommended; validates `X-Hub-Signature-256`)
- `GITHUB_WEBHOOK_TOKEN` (fallback token if signature validation is not used)
- `GOOGLE_CHAT_WEBHOOK_TESTING` (used by `/github-webhook-v2`)
- `GOOGLE_CHAT_WEBHOOK_GITHUB` (used by `/github-webhook`)

Inbound webhook/API security:

- `INBOUND_SHARED_TOKEN` (single shared secret for all protected inbound endpoints)
- `FRAPPE_CLOUD_WEBHOOK_TOKEN` (optional override for `/frappe-cloud-webhook`)
- `DEPLOY_WORKFLOW_TOKEN` (optional override for `/trigger-workflow/<env>`)
- `DEPLOY_STATUS_TOKEN` (optional override for `/status/<env>` and `/check-deploy-failure/<env>`)

Auto deploy runner:

- `DEPLOY_ENV` (`staging` | `preview` | `production` | `version16`)
- `STAGING_BENCH_NAME`, `PREVIEW_BENCH_NAME`, `PROD_BENCH_NAME`, `VERSION16_BENCH_NAME` (optional overrides)
- `STAGING_ALLOWED_APPS`, `PREVIEW_ALLOWED_APPS`, `PROD_ALLOWED_APPS`, `VERSION16_ALLOWED_APPS` (comma-separated)
- `ALLOWED_APPS_FROM_WORKFLOW` (comma-separated; overrides allowed apps at runtime)

Optional runtime:

- `PORT` (default `8080`)
- `APP_DEBUG` / `FLASK_DEBUG` (`1`/`true` to enable Flask debug)
- `LOG_LEVEL` (default `INFO`)
- `PRESS_API_TIMEOUT_SECONDS` (default `30`)
- `GITHUB_API_TIMEOUT_SECONDS` (default `15`)
- `GOOGLE_CHAT_TIMEOUT_SECONDS` (default `10`)

## Running Locally

Install deps (inside your venv):

- `pip install -r requirements.txt`

Create your `.env`:

- `cp .env.example .env`
- Fill in the values in `.env`

Run the web server:

- `python3 app.py`

Trigger a deployment run manually:

- `python3 auto_deploy.py`

## Production

Use a real WSGI server (example with gunicorn):

- `gunicorn -w 2 -b 0.0.0.0:8080 wsgi:app`

## Free Deployment (Render + Neon)

This app works well on the free tiers of:

- Render (hosting)
- Neon Postgres (database): https://console.neon.tech/

### 1) Neon (Postgres) setup

1. Create a Neon project and a database/user.
2. Copy the **connection string** from Neon (prefer the pooled connection string if Neon offers it).
3. Make sure the connection string includes SSL (commonly `sslmode=require`).
4. `deployment_lock` is created automatically on app startup.
5. If you use GitHub webhook v2 (`/github-webhook-v2`), create the `github_db` table once (Neon SQL Editor):

```sql
CREATE TABLE IF NOT EXISTS github_db (
  id BIGSERIAL PRIMARY KEY,
  pr_id VARCHAR(50) NOT NULL,
  google_thread_id TEXT,
  repo_name TEXT,
  branch_name TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS github_db_repo_name_id_idx
  ON github_db (repo_name, id DESC);
```

### 2) Render (Web Service) setup

1. Create a **New → Web Service** in Render and connect this GitHub repo.
2. Pick a Python runtime (recommend **Python 3.10+**).
3. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app`
4. Add environment variables in Render (**Environment → Environment Variables**):
   - `DATABASE_URL` = your Neon connection string
   - `FC_API_KEY`, `FC_API_SECRET`
   - `GOOGLE_CHAT_WEBHOOK`
   - `SITE_ENV_MAP_JSON`, `BENCH_ENV_MAP_JSON`
   - Security: `INBOUND_SHARED_TOKEN` (or endpoint-specific tokens), and `GITHUB_WEBHOOK_SECRET`
   - Optional: `GITHUB_TOKEN`, `GOOGLE_CHAT_WEBHOOK_TESTING`, `GOOGLE_CHAT_WEBHOOK_GITHUB`
   - Optional runtime: `LOG_LEVEL`, `PRESS_API_TIMEOUT_SECONDS`, `GITHUB_API_TIMEOUT_SECONDS`, `GOOGLE_CHAT_TIMEOUT_SECONDS`
5. Deploy. Your base URL will commonly look like `https://<service-name>.onrender.com/`.

### 3) Configure webhooks (after deploy)

Use your Render base URL:

- **Health check:** `GET /`
- **Frappe Cloud webhook:** `POST /frappe-cloud-webhook` (must send `Authorization: Bearer <token>` or `X-Webhook-Token: <token>`)
- **GitHub webhooks:** `POST /github-webhook` and/or `POST /github-webhook-v2` (must use `GITHUB_WEBHOOK_SECRET` signature verification)
- **Manual workflow trigger:** `POST /trigger-workflow/<env>` (must send token)

Example manual trigger call:

```bash
curl -X POST "https://<service-name>.onrender.com/trigger-workflow/staging" \
  -H "Authorization: Bearer $DEPLOY_WORKFLOW_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"allowed_apps":"frappe,erpnext"}'
```

### Free tier notes

- Render free web services may sleep/spin down when idle; webhook deliveries can be delayed during cold starts.
