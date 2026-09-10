# Scheduler flow

The app starts a single APScheduler instance inside the FastAPI process. Run a
single Uvicorn worker and one application instance while using the bundled
SQLite repository.

```text
Market hours
  every configured poll interval -> public OI scan -> enrich/cache/persist

15:31 IST on weekdays
  -> mark unresolved same-day candidate events EXPIRED

17:15 IST on weekdays
  -> fetch/store public F&O participant-wise OI EOD report

18:10 IST on weekdays
  -> fetch/store public equity bhavcopy

00:05 IST
  -> archive prior-day events from visible history; prune past retention

Sunday 07:00 IST
  -> refresh public NSE F&O holiday calendar
```

Each job is single-instance and coalesced. Missing archives on a non-trading
day are normal. The collector records no invented record; callers see missing
data rather than stale data masquerading as a fresh scan.
