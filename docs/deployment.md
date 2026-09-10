# Deployment guide

## Local Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pytest
Copy-Item .env.example .env
.\.venv\Scripts\uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The in-process scheduler and SQLite repository
require one application worker against one database file.

## Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The compose file mounts `./data` at `/app/data`; that mount is the persistence
boundary for SQLite. Do not run multiple replicas against this file.

## Render Blueprint

1. Create a Render Blueprint from this repository.
2. Keep `render.yaml` on the deployment branch and use its single worker.
3. Set optional alert secrets in Render's environment settings, never in Git.
4. Verify `/api/health` after each automatic deployment.

The Blueprint uses Render Free only as a demonstration environment. Its local
filesystem is ephemeral and an idle service can spin down, so SQLite signal
history is lost when the instance restarts or redeploys. The scanner remains
usable but historic analytics are not durable there. A durable deployment
needs a persistent volume or an external database adapter before relying on
history for operational decisions.

## Production guardrails

- Use exactly one worker/instance until persistence and scheduling are moved
  to shared infrastructure.
- Keep `DEBUG_TOKEN` set in any internet-facing deployment. `/api/debug` is
  intentionally unavailable when the token is absent.
- Keep CORS blank for the bundled same-origin dashboard. Add only exact HTTPS
  origins if a separate dashboard is deployed.
- Alert URLs and Telegram credentials are optional. They send candidate
  observations labelled `NO_TRADE`; they never place orders.
