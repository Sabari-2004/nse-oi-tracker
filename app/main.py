# main.py — FastAPI app (dynamic real-time F&O OI scanner)

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
    scan_by_category,
    get_futures_signal,
    get_option_chain_analysis,
    SIGNAL_META,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(IST)
    return (
        now.weekday() < 5 and
        (now.hour, now.minute) >= (MARKET_OPEN_HOUR, MARKET_OPEN_MIN) and
        (now.hour, now.minute) <= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
    )


def _refresh_signals() -> list[dict]:
    """Run full dynamic F&O scan and cache the result."""
    signals = scan_all_fno_realtime()
    cache.set("all_signals", signals, ttl=CACHE_TTL_SECONDS)
    logger.info(f"Scanned all F&O: {len(signals)} active signals found.")
    return signals


def _refresh_category(category: str) -> list[dict]:
    results = scan_by_category(category)
    cache.set(f"cat:{category}", results, ttl=CACHE_TTL_SECONDS)
    return results


# ─── Background poller ────────────────────────────────────────────────────────

async def background_poller():
    """Poll NSE every 60 s during market hours — pre-warms cache."""
    while True:
        if is_market_open():
            logger.info("Market open — running dynamic OI scan...")
            await asyncio.to_thread(_refresh_signals)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ─── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NSE OI Tracker starting up...")
    task = asyncio.create_task(background_poller())
    yield
    task.cancel()


# ─── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NSE F&O OI Scanner",
    description="Dynamic real-time scanner — surfaces ALL F&O stocks with active OI + price signals",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")


@app.get("/api/health")
async def health():
    """Health check + UptimeRobot ping endpoint."""
    now = datetime.now(IST)
    return {
        "status": "ok",
        "time_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open": is_market_open(),
        "cached_scans": len(cache.keys()),
    }


@app.get("/api/oi-signals")
async def oi_signals(
    refresh: bool = Query(False, description="Force fresh NSE fetch"),
    signal: str = Query("", description="Filter by signal type (LONG_BUILDUP, SHORT_BUILDUP, etc.)"),
    min_strength: float = Query(0, description="Minimum signal strength 0–100"),
):
    """
    Dynamic scan of ALL F&O stocks — returns only those with active OI signals.
    No fixed watchlist. Stocks appear here only if they are actually moving.
    """
    if refresh:
        cache.delete("all_signals")

    cached = cache.get("all_signals")
    if not cached:
        cached = await asyncio.to_thread(_refresh_signals)

    results = cached or []

    # Apply filters
    if signal:
        results = [r for r in results if r["signal"] == signal.upper()]
    if min_strength > 0:
        results = [r for r in results if r["strength"] >= min_strength]

    # Summary counts
    counts = {}
    for r in (cached or []):
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1

    return {
        "source": "cache" if cached else "live",
        "total_fno_active": len(cached or []),
        "filtered_count": len(results),
        "market_open": is_market_open(),
        "signal_counts": counts,
        "signal_meta": SIGNAL_META,
        "signals": results,
        "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
        "note": "Dynamic scan of all NSE F&O stocks — showing only stocks with active OI signals",
    }


@app.get("/api/category/{category}")
async def category_scan(
    category: str,
    refresh: bool = Query(False),
):
    """
    Get stocks from NSE's pre-computed buildup lists.

    category: long | short | short_covering | long_unwinding
    """
    valid = ["long", "short", "short_covering", "long_unwinding"]
    if category not in valid:
        raise HTTPException(status_code=400, detail=f"category must be one of {valid}")

    if refresh:
        cache.delete(f"cat:{category}")

    cached = cache.get(f"cat:{category}")
    if not cached:
        cached = await asyncio.to_thread(_refresh_category, category)

    return {
        "category": category,
        "count": len(cached),
        "data": cached,
        "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
    }


@app.get("/api/option-chain/{symbol}")
async def option_chain(symbol: str):
    """Full option chain for a symbol — ATM ± 10 strikes."""
    symbol = symbol.upper()
    cached = cache.get(f"chain:{symbol}")
    if cached:
        return {"source": "cache", **cached}
    result = await asyncio.to_thread(get_option_chain_analysis, symbol)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    cache.set(f"chain:{symbol}", result)
    return {"source": "live", **result}


@app.get("/api/signal/{symbol}")
async def single_signal(symbol: str):
    """Get OI signal for any specific F&O symbol on demand."""
    symbol = symbol.upper()
    cached = cache.get(f"sig:{symbol}")
    if cached:
        return {"source": "cache", **cached}
    result = await asyncio.to_thread(get_futures_signal, symbol)
    if not result:
        raise HTTPException(status_code=404, detail=f"No derivative data for {symbol}")
    cache.set(f"sig:{symbol}", result)
    return {"source": "live", **result}
