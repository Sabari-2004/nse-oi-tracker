# main.py — FastAPI production app for NSE F&O OI Scanner
# Version 4.1.0 — RLock fetcher, per-symbol option chain seeding, /api/debug

import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    CACHE_TTL_SECONDS, POLL_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
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
)
from app.nse_fetcher import (
    fetch_all_fno_oi_change,
    fetch_quote_derivative,
    test_nse_connectivity,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(IST)
    return (
        now.weekday() < 5 and
        (now.hour, now.minute) >= (MARKET_OPEN_HOUR, MARKET_OPEN_MIN) and
        (now.hour, now.minute) <= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
    )


def _refresh_signals() -> list[dict]:
    signals = scan_all_fno_realtime()
    cache.set("all_signals", signals, ttl=CACHE_TTL_SECONDS)
    h = sum(1 for s in signals if s.get("confidence_tier") == "HIGH")
    m = sum(1 for s in signals if s.get("confidence_tier") == "MEDIUM")
    logger.info(f"Scan complete — {len(signals)} signals ({h} HIGH, {m} MEDIUM)")
    return signals


# ── Background poller ─────────────────────────────────────────────────────────

async def background_poller():
    """Re-scan all F&O stocks every 60s during market hours."""
    # First scan: wait 15s for session to fully initialise, then scan immediately
    await asyncio.sleep(15)
    if is_market_open():
        logger.info("Market open — initial scan…")
        try:
            await asyncio.to_thread(_refresh_signals)
        except Exception as e:
            logger.error(f"Initial scan error: {e}")

    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        if is_market_open():
            logger.info("Polling — scanning F&O stocks…")
            try:
                await asyncio.to_thread(_refresh_signals)
            except Exception as e:
                logger.error(f"Poll scan error: {e}")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NSE OI Tracker v4.0.0 starting — Singapore 🇸🇬")
    task = asyncio.create_task(background_poller())
    yield
    task.cancel()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NSE F&O OI Scanner",
    description="Production-grade real-time OI signal scanner · Singapore",
    version="4.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    """Health check — GET and HEAD supported (UptimeRobot uses HEAD)."""
    now = datetime.now(IST)
    return {
        "status":      "ok",
        "time_ist":    now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open": is_market_open(),
        "region":      "singapore",
        "version":     "4.0.0",
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
    Signal classification: price direction × OI direction → 4 signal types
    """
    if refresh:
        cache.delete("all_signals")

    cached = cache.get("all_signals")

    # If cache empty AND market open → force fresh scan (don't serve stale empty)
    if (cached is None or len(cached) == 0) and is_market_open():
        logger.info("Cache empty during market hours → fresh scan")
        cached = await asyncio.to_thread(_refresh_signals)

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

    return {
        "market_open":       is_market_open(),
        "total_fno_active":  len(cached),
        "high_confidence":   high,
        "medium_confidence": medium,
        "filtered_count":    len(results),
        "signal_counts":     counts,
        "signal_meta":       SIGNAL_META,
        "signals":           results,
        "timestamp":         datetime.now(IST).strftime("%H:%M:%S"),
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
        all_signals = cache.get("all_signals") or await asyncio.to_thread(_refresh_signals)
        cached = [r for r in all_signals if r["signal"] == target]
        cache.set(cache_key, cached, ttl=CACHE_TTL_SECONDS)

    return {
        "category":  category,
        "signal":    CATEGORY_TO_SIGNAL.get(category),
        "count":     len(cached),
        "data":      cached,
        "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
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

    # Never return 503 — return a structured response with error info
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

    cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
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
        cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
        return {"source": "live", **result}

    except Exception as exc:
        logger.error(f"Signal parse error for {symbol}: {exc}")
        return JSONResponse(status_code=200, content={
            "symbol": symbol,
            "error":  f"Parse error: {exc}",
            "signal": "NEUTRAL",
        })


@app.get("/api/debug")
async def debug():
    """Diagnostic endpoint — NSE connectivity + raw sample data."""
    from app.nse_fetcher import fetch_all_fno_oi_change
    conn   = await asyncio.to_thread(test_nse_connectivity)
    cached = cache.get("all_signals") or []

    # Get raw rows to inspect field names
    raw_rows = await asyncio.to_thread(fetch_all_fno_oi_change)
    sample   = raw_rows[:3] if raw_rows else []

    return {
        "timestamp":      datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open":    is_market_open(),
        "version":        "4.1.0",
        "cached_signals": len(cached),
        "raw_rows_count": len(raw_rows),
        "nse_endpoints":  conn,
        "sample_row":     sample[0] if sample else {},   # ← shows real field names
        "sample_rows":    sample,
    }
