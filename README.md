# NSE OI Tracker PRO ? foundations

NSE OI Tracker is a FastAPI dashboard for public NSE F&O OI-spurts data and
option-chain analytics. This branch adds the production foundation for
server-owned signal history, SQLite event storage, scheduled lifecycle jobs,
and a dashboard migration path from browser-local history.

It is analytical decision support, not investment advice. Current OI labels
are not a complete multi-factor trading signal.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pytest
Copy-Item .env.example .env
.\.venv\Scripts\uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The SQLite database is created in `data/` by
default. Keep one Uvicorn worker because the app runs an in-process scheduler.

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The compose volume keeps SQLite history in `./data`. Do not use an ephemeral
PaaS filesystem if durable history is required.

## Render free-tier deployment

This repository includes a `render.yaml` Blueprint. After connecting the
repository in Render, it deploys the FastAPI web service and redeploys on each
commit to `main`. The health check is `/api/health` and Render supplies `PORT`.

Render's Free web service is appropriate for a live demo, not durable history:
it spins down after idle time and its local filesystem (including SQLite) is
lost on restart, redeploy, or spin-down. Use a paid persistent disk or a
durable external database before treating history as production data.

## Core API

- `GET /api/health` ? market status, version, persistence status, and last
  refresh metadata.
- `GET /api/oi-signals` ? current scanner output with freshness metadata.
- `GET /api/option-chain/{symbol}` ? PCR, max pain, and strike ladder.
- `GET /api/history/today` ? visible, server-owned IST-day signal events and
  lifecycle status.
- `GET /api/analytics/today` ? server-calculated same-day outcomes.

`/api/debug` is disabled unless `DEBUG_TOKEN` is configured; pass it through
the `X-Debug-Token` request header in a trusted environment.

The bundled dashboard is served from the API's own origin. If deploying a
separate dashboard, set `NSE_OI_CORS_ORIGINS` to its exact comma-separated
HTTPS origins; it is deliberately blank by default.

## Verification

```powershell
.\.venv\Scripts\python verify_history_audit.py
.\.venv\Scripts\python -m pytest -q
```

See [architecture](docs/architecture.md) for data ownership, scheduler rules,
and the current signal-quality boundary.
