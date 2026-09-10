# Architecture

## Current service boundary

The application is a FastAPI service with a static Alpine.js dashboard. It is
not a Streamlit application; the current dashboard is retained while API and
persistence responsibilities are moved out of browser storage.

```text
Public NSE endpoints
        |
app.nse_fetcher (session, retry, normalized fetch functions)
        |
app.oi_analyzer (current OI classification and option-chain calculations)
        |
app.main refresh coordinator
        |------------------------------|
        v                              v
in-memory request cache         database.SignalRepository (SQLite)
                                       |
                             /api/history/today
                             /api/analytics/today
                                       |
                           static/index.html dashboard
```

## Durable data model

`scan_snapshots` records an immutable, timestamped scan fingerprint. Identical
payloads during the same IST minute deduplicate. `signal_events` records each
signal event within a snapshot, including server-calculated entry, stop, two
targets, current price, lifecycle status, and original normalized payload.

At 15:31 IST, open events are marked `EXPIRED` if neither a target nor stop
closed them. At 00:05 IST, prior-day events are archived from the visible API
and kept locally for the retention period. No browser clock determines that
lifecycle.

## Scheduler and deployment rule

The scheduler is intentionally in process, so run **exactly one** application
worker against one SQLite file. A multi-worker/multi-instance deployment needs
a separate scheduler plus a shared persistent database before scaling out.

### Render Free caveat

`render.yaml` intentionally deploys a single Free Python web service with
auto-deploy on commits. It is suitable for demonstrations only: a Free Render
web service spins down when idle and has an ephemeral filesystem, so the local
SQLite file cannot retain signal history across a restart, redeploy, or
spin-down. Durable deployment requires a paid disk or moving the repository
layer to a managed persistent database.

## Signal-quality boundary

The existing scanner classifies price/OI relationships only: long buildup,
short buildup, short covering, and long unwinding. Its stored levels are
explicitly tagged `percentage_fallback_pending_atr`. They are not ATR/VWAP,
regime, news, or sector validated. Future signal-engine work must add those
factors before a direction is presented as a complete trade recommendation.

## Public source policy

Only public/free sources may be used. Every source adapter must expose its
timestamp/freshness and fail visibly rather than silently treating stale data
as live. Public NSE endpoints can rate-limit or change without notice.
