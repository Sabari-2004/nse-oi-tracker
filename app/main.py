# main.py ? FastAPI production app for NSE F&O OI Scanner
# Version 4.2.0 ? holiday-aware market status, native-price-change fix,
#                  MEDIUM-tier signals restored, gated /api/debug

import logging
import asyncio
import time
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import app.oi_analyzer as oi_engine

from app.market_calendar import (
    get_market_status,
    has_holiday_calendar_for_year,
    holiday_calendar_metadata,
    refresh_holiday_calendar,
    MARKET_STATUS_OPEN,
    MARKET_STATUS_LABELS,
)
from app.cache import cache
from app.oi_analyzer import (
    scan_all_fno_realtime,
    get_option_chain_analysis,
    SIGNAL_META,
    CATEGORY_TO_SIGNAL,
    classify_signal,
    _build_signal_row,
    _f,
    sample_field_usage,
)
from app.nse_fetcher import (
    fetch_all_fno_oi_change,
    fetch_quote_derivative,
    test_nse_connectivity,
)
from config.settings import get_settings
from database.repository import SignalRepository
from utils.time import IST, now_ist, ist_trade_date

APP_VERSION = "4.3.0"
settings = get_settings()
repository = SignalRepository(settings.database_path)

# Set this in Render's environment variables to lock down /api/debug in
# production. Left unset, /api/debug stays open (dev convenience) but says
# so loudly in its own response ? "production standard" means the open-by-
# default state is visible, not silently assumed safe.
DEBUG_TOKEN = settings.debug_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
STALE_SIGNAL_GRACE_SECONDS = 180
_last_good_signals: list[dict] = []
_last_good_signals_at = 0.0
_last_refresh_at_ist: str | None = None
_last_refresh_was_stale = False
_last_snapshot_id: int | None = None
_refresh_lock = asyncio.Lock()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"


# ?? Helpers ???????????????????????????????????????????????????????????????????

def is_market_open() -> bool:
    """
    Backward-compatible boolean. Used everywhere that only needs a yes/no.
    This used to ONLY check weekday + clock window, so an NSE trading
    holiday falling on a weekday was reported as "market open" and the
    poller scanned a dead market. It now defers to market_calendar, which
    knows about holidays too.
    """
    return get_market_status() == MARKET_STATUS_OPEN


def _refresh_signals() -> list[dict]:
    """Fetch, persist, and cache one signal scan in a worker thread."""
    global _last_good_signals, _last_good_signals_at
    global _last_refresh_at_ist, _last_refresh_was_stale, _last_snapshot_id
    signals = scan_all_fno_realtime()
    monotonic_now = time.monotonic()
    captured_at = now_ist()
    served_stale = False
    if signals:
        _last_good_signals = signals
        _last_good_signals_at = monotonic_now
    elif _last_good_signals and monotonic_now - _last_good_signals_at <= STALE_SIGNAL_GRACE_SECONDS:
        logger.warning("Empty scan received; serving last valid signals during grace period")
        signals = _last_good_signals
        served_stale = True

    if not served_stale:
        try:
            write = repository.record_scan(signals, captured_at)
            _last_snapshot_id = write.snapshot_id
            repository.update_open_events(signals, captured_at)
        except Exception:
            logger.exception("Could not persist scan history")

    _last_refresh_at_ist = captured_at.isoformat()
    _last_refresh_was_stale = served_stale
    cache.set("all_signals", signals, ttl=settings.cache_ttl_seconds)
    h = sum(1 for s in signals if s.get("confidence_tier") == "HIGH")
    m = sum(1 for s in signals if s.get("confidence_tier") == "MEDIUM")
    logger.info(f"Scan complete ? {len(signals)} signals ({h} HIGH, {m} MEDIUM)")
    return signals


async def refresh_signals() -> list[dict]:
    """Serialize all refresh callers to avoid upstream request stampedes."""
    async with _refresh_lock:
        return await asyncio.to_thread(_refresh_signals)


async def scheduled_refresh() -> None:
    """Refresh only during an exchange-open session."""
    if not is_market_open():
        return
    try:
        await refresh_signals()
    except Exception:
        logger.exception("Scheduled signal refresh failed")


async def scheduled_history_rollover() -> None:
    """Archive prior-day UI history at 00:05 IST and retain a local archive."""
    now = now_ist()
    try:
        archived = await asyncio.to_thread(
            repository.archive_previous_history,
            ist_trade_date(now),
            settings.history_retention_days,
        )
        logger.info("Daily history rollover archived %s event(s)", archived)
    except Exception:
        logger.exception("Daily history rollover failed")


async def scheduled_market_close() -> None:
    """Mark unresolved current-day signal events as expired after close."""
    try:
        expired = await asyncio.to_thread(repository.expire_open_events, now_ist())
        logger.info("Market-close processing expired %s event(s)", expired)
    except Exception:
        logger.exception("Market-close processing failed")


async def scheduled_holiday_calendar_refresh() -> None:
    """Refresh NSE's public F&O calendar; bundled dates stay as fallback."""
    result = await asyncio.to_thread(refresh_holiday_calendar)
    if result["updated"]:
        logger.info("NSE holiday calendar refreshed for %s", result["years"])
    else:
        logger.warning("NSE holiday calendar refresh failed; using bundled dates")


# ?? Background poller ?????????????????????????????????????????????????????????

async def background_poller():
    """Re-scan all F&O stocks every 60s during market hours."""
    # First scan: wait 15s for session to fully initialise, then scan immediately
    await asyncio.sleep(15)
    if is_market_open():
        logger.info("Market open ? initial scan?")
        try:
            await asyncio.to_thread(_refresh_signals)
        except Exception as e:
            logger.error(f"Initial scan error: {e}")

    while True:
        await asyncio.sleep(settings.poll_interval_seconds)
        if is_market_open():
            logger.info("Polling ? scanning F&O stocks?")
            try:
                await asyncio.to_thread(_refresh_signals)
            except Exception as e:
                logger.error(f"Poll scan error: {e}")


# ?? Lifecycle ?????????????????????????????????????????????????????????????????

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"NSE OI Tracker v{APP_VERSION} starting")
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(
        scheduled_refresh,
        IntervalTrigger(seconds=settings.poll_interval_seconds, timezone=IST),
        id="nse-signal-refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_history_rollover,
        CronTrigger(hour=0, minute=5, timezone=IST),
        id="daily-history-rollover",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        scheduled_market_close,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=31, timezone=IST),
        id="market-close-expiry",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        scheduled_holiday_calendar_refresh,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone=IST),
        id="nse-holiday-calendar-refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    # A newly deployed year is unknown until NSE's public calendar loads.
    # Await only in that case: normal startup stays local and fast.
    if not has_holiday_calendar_for_year(now_ist().year):
        await scheduled_holiday_calendar_refresh()
    await scheduled_refresh()
    yield
    scheduler.shutdown(wait=False)


# ?? FastAPI app ???????????????????????????????????????????????????????????????

app = FastAPI(
    title="NSE F&O OI Scanner",
    description="Real-time OI signal scanner",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # The shipped UI is same-origin. An external dashboard must be opted in
    # through NSE_OI_CORS_ORIGINS rather than using a browser-wide wildcard.
    allow_origins=list(settings.cors_origins),
    allow_methods=["GET", "HEAD"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ?? Routes ????????????????????????????????????????????????????????????????????

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    """Health check ? GET and HEAD supported (UptimeRobot uses HEAD)."""
    now = now_ist()
    status = get_market_status(now)
    return {
        "status":        "ok",
        "time_ist":      now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open":   status == MARKET_STATUS_OPEN,
        "market_status": status,
        "market_status_label": MARKET_STATUS_LABELS[status],
        "version":       APP_VERSION,
        "database":       "ready",
        "last_refresh_at_ist": _last_refresh_at_ist,
        "last_refresh_was_stale": _last_refresh_was_stale,
        "last_snapshot_id": _last_snapshot_id,
        "holiday_calendar": holiday_calendar_metadata(),
    }


@app.get("/api/oi-signals")
async def oi_signals(
    refresh:      bool  = Query(False, description="Force fresh NSE fetch"),
    signal:       str   = Query("",    description="Filter by signal type"),
    tier:         str   = Query("",    description="Filter by tier: HIGH | MEDIUM"),
    min_strength: float = Query(0,     description="Min strength score"),
):
    """
    Scan ALL NSE F&O stocks. Returns HIGH + MEDIUM confidence signals only.

    Data source: /api/live-analysis-oi-spurts-underlyings (confirmed working)
    Signal classification: price direction ? OI direction ? 4 signal types
    """
    if refresh:
        cache.delete("all_signals")

    cached = cache.get("all_signals")

    # If cache empty AND market open ? force fresh scan (don't serve stale empty)
    if (cached is None or len(cached) == 0) and is_market_open():
        logger.info("Cache empty during market hours ? fresh scan")
        cached = await refresh_signals()

    if cached is None:
        cached = []

    results = list(cached)

    # Optional filters
    if signal:
        results = [r for r in results if r["signal"] == signal.upper()]
    if tier:
        results = [r for r in results if r["confidence_tier"] == tier.upper()]
    if min_strength > 0:
        results = [r for r in results if r["strength"] >= min_strength]

    # Count per signal type
    counts = {}
    for r in cached:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1

    high   = sum(1 for r in cached if r.get("confidence_tier") == "HIGH")
    medium = sum(1 for r in cached if r.get("confidence_tier") == "MEDIUM")
    status = get_market_status()

    return {
        "market_open":       status == MARKET_STATUS_OPEN,
        "market_status":     status,
        "market_status_label": MARKET_STATUS_LABELS[status],
        "total_fno_active":  len(cached),
        "high_confidence":   high,
        "medium_confidence": medium,
        "filtered_count":    len(results),
        "signal_counts":     counts,
        "signal_meta":       SIGNAL_META,
        "signals":           results,
        "timestamp":         now_ist().strftime("%H:%M:%S"),
        "last_refresh_at_ist": _last_refresh_at_ist,
        "is_stale":          _last_refresh_was_stale,
        "snapshot_id":       _last_snapshot_id,
    }


@app.get("/api/history/today")
async def today_history(
    limit: int = Query(1000, ge=1, le=5000, description="Maximum number of current-day events"),
):
    """Return server-owned signal events visible for the current IST date."""
    trade_date = ist_trade_date()
    events, total = await asyncio.to_thread(repository.history_for_date, trade_date, limit=limit)
    performance = await asyncio.to_thread(repository.performance_for_date, trade_date)
    return {
        "trade_date": trade_date,
        "visible_history_scope": "today_ist",
        "reset_policy": "Previous-day events are archived at 00:05 IST.",
        "total_events": total,
        "events": events,
        "performance": performance,
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
    }


@app.get("/api/analytics/today")
async def today_analytics():
    """Return server-calculated daily performance for durable signal events."""
    trade_date = ist_trade_date()
    return {
        "trade_date": trade_date,
        "metrics": await asyncio.to_thread(repository.performance_for_date, trade_date),
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
    }


@app.get("/api/category/{category}")
async def category_scan(category: str, refresh: bool = Query(False)):
    """Signals filtered by category: long_buildup | short_buildup | short_covering | long_unwinding"""
    valid = list(CATEGORY_TO_SIGNAL.keys())
    if category not in valid:
        raise HTTPException(status_code=400, detail=f"category must be one of: {valid}")

    cache_key = f"cat:{category}"
    if refresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if cached is None:
        target = CATEGORY_TO_SIGNAL[category]
        all_signals = cache.get("all_signals") or await refresh_signals()
        cached = [r for r in all_signals if r["signal"] == target]
        cache.set(cache_key, cached, ttl=settings.cache_ttl_seconds)

    return {
        "category":  category,
        "signal":    CATEGORY_TO_SIGNAL.get(category),
        "count":     len(cached),
        "data":      cached,
        "timestamp": now_ist().strftime("%H:%M:%S"),
    }


@app.get("/api/option-chain/{symbol}")
async def option_chain(symbol: str, refresh: bool = Query(False)):
    """
    Option chain for any F&O symbol or index.
    Returns: ATM strike, PCR, max pain, CE/PE OI per strike.

    Supports: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, and all F&O stocks.
    Returns graceful error (not 503) if NSE is temporarily unavailable.
    """
    symbol    = symbol.upper().strip()
    cache_key = f"chain:{symbol}"

    if refresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if cached:
        return {"source": "cache", **cached}

    result = await asyncio.to_thread(get_option_chain_analysis, symbol)

    # Never return 503 ? return a structured response with error info
    # so the frontend can display a friendly message
    if "error" in result:
        logger.warning(f"Option chain error for {symbol}: {result['error']}")
        return JSONResponse(
            status_code=200,
            content={
                "source":      "live",
                "symbol":      symbol,
                "error":       result["error"],
                "strikes":     [],
                "atm_strike":  0,
                "expiry":      None,
                "pcr":         0,
                "total_ce_oi": 0,
                "total_pe_oi": 0,
                "max_pain":    0,
            }
        )

    cache.set(cache_key, result, ttl=settings.cache_ttl_seconds)
    return {"source": "live", **result}


@app.get("/api/signal/{symbol}")
async def single_signal(symbol: str):
    """
    On-demand signal for any specific F&O symbol.
    Uses quote-derivative endpoint for live futures data.
    Returns graceful response if symbol not found or NSE unavailable.
    """
    symbol    = symbol.upper().strip()
    cache_key = f"sig:{symbol}"

    cached = cache.get(cache_key)
    if cached:
        return {"source": "cache", **cached}

    raw = await asyncio.to_thread(fetch_quote_derivative, symbol)

    if not raw:
        # Fallback: check if symbol is in the current scan cache
        all_signals = cache.get("all_signals") or []
        match = next((s for s in all_signals if s["symbol"] == symbol), None)
        if match:
            return {"source": "scan_cache", **match}
        return JSONResponse(
            status_code=200,
            content={
                "symbol": symbol,
                "error":  f"NSE data unavailable for {symbol}. Try during market hours (09:15-15:30 IST).",
                "signal": "NEUTRAL",
                "confidence_tier": "LOW",
            }
        )

    try:
        stocks = raw.get("stocks", [])
        # Find the nearest futures contract
        fut = next(
            (s for s in stocks
             if "Futures" in s.get("metadata", {}).get("instrumentType", "")
             or "FUT" in s.get("metadata", {}).get("identifier", "")),
            stocks[0] if stocks else None
        )
        if not fut:
            return JSONResponse(status_code=200, content={
                "symbol": symbol, "error": "No futures contract found",
                "signal": "NEUTRAL",
            })

        meta        = fut.get("metadata", {})
        ltp         = _f(meta.get("lastPrice", 0))
        price_chg   = _f(meta.get("change", 0))
        price_chg_p = _f(meta.get("pChange", 0))
        oi          = _f(meta.get("openInterest", 0))
        oi_chg      = _f(meta.get("changeinOpenInterest", 0))
        prev_oi     = oi - oi_chg
        oi_chg_p    = (oi_chg / prev_oi * 100) if prev_oi > 0 else 0

        signal = classify_signal(price_chg_p, oi_chg_p)
        result = _build_signal_row(
            symbol, ltp, price_chg, price_chg_p,
            int(oi), int(oi_chg), oi_chg_p, signal
        )
        cache.set(cache_key, result, ttl=settings.cache_ttl_seconds)
        return {"source": "live", **result}

    except Exception as exc:
        logger.error(f"Signal parse error for {symbol}: {exc}")
        return JSONResponse(status_code=200, content={
            "symbol": symbol,
            "error":  f"Parse error: {exc}",
            "signal": "NEUTRAL",
        })


@app.get("/api/debug")
async def debug(x_debug_token: str = Header(default="")):
    """
    Diagnostic endpoint ? NSE connectivity + raw sample data.

    Gated by DEBUG_TOKEN env var. This endpoint exposes raw upstream
    payloads and internal field-mapping state; leaving it wide open on a
    public deployment is fine for personal debugging but not something to
    call "production standard". Set DEBUG_TOKEN in Render's env vars to
    lock it down ? until you do, it stays open and says so explicitly.
    """
    if not DEBUG_TOKEN:
        raise HTTPException(status_code=404, detail="Diagnostic endpoint is disabled")
    if x_debug_token != DEBUG_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Debug-Token header")

    conn   = await asyncio.to_thread(test_nse_connectivity)
    cached = cache.get("all_signals") or []

    # Get raw rows to inspect field names
    raw_rows = await asyncio.to_thread(fetch_all_fno_oi_change)
    sample   = raw_rows[:3] if raw_rows else []

    return {
        "timestamp":        now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_status":    get_market_status(),
        "version":          APP_VERSION,
        "auth_protected":   bool(DEBUG_TOKEN),
        "cached_signals":   len(cached),
        "raw_rows_count":   len(raw_rows),
        "nse_endpoints":    conn,
        "sample_row":       sample[0] if sample else {},   # ? shows real field names
        "sample_rows":      sample,
        "price_sources":    {r.get("symbol"): r.get("price_source") for r in sample},
        "oi_field_usage":   sample_field_usage(),
        "cas_time_ist":     oi_engine._last_cas_time_ist,
    }
