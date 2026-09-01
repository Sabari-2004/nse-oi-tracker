# main.py — FastAPI app for NSE F&O OI Scanner
# Fixes: removed deleted imports (scan_by_category, get_futures_signal),
#        HEAD method on /api/health, updated /api/category route

import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
)
from app.nse_fetcher import fetch_buildup_category, fetch_quote_derivative
from app.oi_analyzer import _parse_buildup_row, _f, _symbol

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
    logger.info(f"Scan complete — {len(signals)} high-confidence signals found.")
    return signals


# ── Background poller ─────────────────────────────────────────────────────────

async def background_poller():
    """Re-scan all F&O stocks every 60 s during market hours."""
    while True:
        if is_market_open():
            logger.info("Market open — scanning all F&O stocks…")
            try:
                await asyncio.to_thread(_refresh_signals)
            except Exception as exc:
                logger.error(f"Background scan error: {exc}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NSE OI Tracker starting — Singapore region 🇸🇬")
    task = asyncio.create_task(background_poller())
    yield
    task.cancel()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NSE F&O OI Scanner",
    description="Real-time high-confidence OI signal scanner for all NSE F&O stocks",
    version="3.0.0",
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
    """
    Health check endpoint.
    Accepts GET and HEAD — UptimeRobot sends HEAD requests.
    """
    now = datetime.now(IST)
    return {
        "status": "ok",
        "time_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open": is_market_open(),
        "region": "singapore",
        "version": "3.0.0",
    }


@app.get("/api/oi-signals")
async def oi_signals(
    refresh:      bool  = Query(False, description="Force fresh NSE fetch"),
    signal:       str   = Query("",    description="Filter: LONG_BUILDUP | SHORT_BUILDUP | SHORT_COVERING | LONG_UNWINDING"),
    tier:         str   = Query("",    description="Filter by confidence tier: HIGH | MEDIUM"),
    min_strength: float = Query(0,     description="Min strength score 0–100"),
):
    """
    Dynamically scan ALL NSE F&O stocks.
    Returns only HIGH + MEDIUM confidence signals (no noise).
    """
    if refresh:
        cache.delete("all_signals")

    cached = cache.get("all_signals")
    if cached is None:
        cached = await asyncio.to_thread(_refresh_signals)

    results = list(cached or [])

    # Filters
    if signal:
        results = [r for r in results if r["signal"] == signal.upper()]
    if tier:
        results = [r for r in results if r["confidence_tier"] == tier.upper()]
    if min_strength > 0:
        results = [r for r in results if r["strength"] >= min_strength]

    # Per-signal counts (from full unfiltered list)
    counts = {}
    for r in (cached or []):
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1

    high_count   = sum(1 for r in (cached or []) if r.get("confidence_tier") == "HIGH")
    medium_count = sum(1 for r in (cached or []) if r.get("confidence_tier") == "MEDIUM")

    return {
        "market_open":      is_market_open(),
        "total_fno_active": len(cached or []),
        "high_confidence":  high_count,
        "medium_confidence": medium_count,
        "filtered_count":   len(results),
        "signal_counts":    counts,
        "signal_meta":      SIGNAL_META,
        "signals":          results,
        "timestamp":        datetime.now(IST).strftime("%H:%M:%S"),
        "note": "Only HIGH/MEDIUM confidence signals shown. Powered by NSE pre-computed buildup endpoints.",
    }


@app.get("/api/category/{category}")
async def category_scan(
    category: str,
    refresh: bool = Query(False),
):
    """
    Get stocks from NSE's pre-computed buildup lists.
    category: long_buildup | short_buildup | short_covering | long_unwinding
    """
    valid = list(CATEGORY_TO_SIGNAL.keys())
    if category not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of: {valid}"
        )

    cache_key = f"cat:{category}"
    if refresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if cached is None:
        signal = CATEGORY_TO_SIGNAL[category]
        rows   = await asyncio.to_thread(fetch_buildup_category, category)
        cached = [r for r in
                  [_parse_buildup_row(row, signal) for row in rows]
                  if r is not None]
        cache.set(cache_key, cached, ttl=CACHE_TTL_SECONDS)

    return {
        "category":  category,
        "signal":    CATEGORY_TO_SIGNAL.get(category),
        "count":     len(cached),
        "data":      cached,
        "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
    }


@app.get("/api/option-chain/{symbol}")
async def option_chain(symbol: str):
    """Full option chain for a symbol — ATM ± 10 strikes, PCR, max pain."""
    symbol = symbol.upper().strip()
    cache_key = f"chain:{symbol}"

    cached = cache.get(cache_key)
    if cached:
        return {"source": "cache", **cached}

    result = await asyncio.to_thread(get_option_chain_analysis, symbol)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])

    cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
    return {"source": "live", **result}


@app.get("/api/signal/{symbol}")
async def single_signal(symbol: str):
    """
    On-demand OI signal for any specific F&O symbol.
    Fetches live futures quote from NSE derivative endpoint.
    """
    symbol    = symbol.upper().strip()
    cache_key = f"sig:{symbol}"

    cached = cache.get(cache_key)
    if cached:
        return {"source": "cache", **cached}

    # Fetch from quote-derivative endpoint
    raw = await asyncio.to_thread(fetch_quote_derivative, symbol)
    if not raw:
        raise HTTPException(status_code=404, detail=f"No derivative data for {symbol}")

    try:
        stocks = raw.get("stocks", [])
        fut = next(
            (s for s in stocks
             if "Futures" in s.get("metadata", {}).get("instrumentType", "")
             or "FUT" in s.get("metadata", {}).get("identifier", "")),
            stocks[0] if stocks else None
        )
        if not fut:
            raise HTTPException(status_code=404, detail=f"No futures contract found for {symbol}")

        meta        = fut.get("metadata", {})
        ltp         = _f(meta.get("lastPrice", 0))
        price_chg   = _f(meta.get("change", 0))
        price_chg_p = _f(meta.get("pChange", 0))
        oi          = _f(meta.get("openInterest", 0))
        oi_chg      = _f(meta.get("changeinOpenInterest", 0))
        oi_chg_p    = ((oi_chg / (oi - oi_chg)) * 100) if (oi - oi_chg) > 0 else 0

        from app.oi_analyzer import _build_signal_row, classify_signal
        signal = classify_signal(price_chg_p, oi_chg_p)
        result = _build_signal_row(
            symbol, ltp, price_chg, price_chg_p,
            int(oi), int(oi_chg), oi_chg_p, signal
        )
        cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
        return {"source": "live", **result}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error in /api/signal/{symbol}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
