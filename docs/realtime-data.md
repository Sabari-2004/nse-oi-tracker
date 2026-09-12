# Realtime data and safety boundaries

The scanner uses the public NSE live OI-spurts endpoint as its realtime source. Each polling cycle preserves NSE's own day-relative price and OI changes whenever those fields are present. A rolling price-delta fallback is used only when NSE omits a native price-change field, and the response is labelled accordingly.

Signal detection keeps the 0.25% inclusive price threshold so the scanner does not miss an early directional move. OI classification requires a 3.00% inclusive change and 100,000 contracts. The live dashboard applies a separate quality gate: confidence must be at least 75/100 and absolute OI must be at least 100,000 contracts. Lower-confidence rows remain diagnostic but are not published as active signals. These are observation filters, not trade instructions.

Risk levels use the latest stored NSE daily bhavcopy ATR14 when at least 15 valid daily bars are available. The plan uses 1 ATR for the stop, 1.5 ATR for the first target, and 3 ATR for the second target. New or insufficiently seeded symbols retain an explicit percentage fallback label so the dashboard cannot mistake fallback levels for volatility-derived levels.

The current public NSE endpoint does not guarantee free, authenticated 5-minute OHLCV candles. The application therefore computes an explicitly labelled session VWAP from live scanner observations only when cumulative volume is present. It does not fabricate candles or claim broker-grade intraday VWAP. Daily EMA/ATR context remains labelled as daily and stale for intraday purposes.

Every persisted signal remains `NO_TRADE` and `actionable: false` until the project has a separately reviewed validation policy. The dashboard's “Paper track” action is local history tracking only. No broker integration, order-placement route, Angel One credentials, or live execution path is included.

The existing in-process scheduler and SQLite repository remain unchanged because a safe PostgreSQL/Redis migration requires a deployment decision, schema migration, and durable hosting configuration. The service continues to support live polling in a single worker and updates open event prices and target/stop lifecycle on every successful scan.

## Verification

Run:

```bash
python3 -m pytest -q
```

The current suite passes all tests after these changes.
