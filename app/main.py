# main.py ? FastAPI production app for NSE F&O OI Scanner
# Version 4.2.0 ? holiday-aware market status, native-price-change fix,
#                  MEDIUM-tier signals restored, gated /api/debug

import logging
import asyncio
import time
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
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
    fetch_fii_dii_activity,
    fetch_corporate_announcements,
    fetch_market_indices,
    fetch_quote_derivative,
    test_nse_connectivity,
)
from analytics.technical import technical_context
from analytics.intraday import observe as observe_intraday
from analytics.intraday import candle_vwap
from analytics.technical import ema as calculate_ema
from collector.bhavcopy import collect_equity_bhavcopy
from collector.participant_oi import collect_participant_oi
from signal_engine.quality import apply_daily_technical_context, apply_intraday_observation_context
from analytics.market_overview import normalize_market_overview
from analytics.option_chain import summarize_pcr_trend
from analytics.backtest import summarize_candidate_backtest
from alerts.dispatcher import dispatch_candidate_alert
from analytics.news import latest_event_risk, normalize_nse_announcements
from analytics.sectors import attach_sector, known_sectors
from analytics.cas import confidence_analysis
from analytics.regime import classify_market_regime
from analytics.oi_heatmap import option_oi_heatmap
from analytics.traps import trap_risk
from analytics.intelligence import build_market_intelligence
from analytics.sources import public_source_inventory
from integrations.angel_one_market_data import AngelOneMarketData
from config.settings import get_settings
from database.repository import SignalRepository
from utils.time import IST, now_ist, ist_trade_date

APP_VERSION = "4.4.0"
settings = get_settings()
repository = SignalRepository(settings.database_path)
angel_market_data = AngelOneMarketData.from_environment()

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
    if signals and angel_market_data is not None:
        try:
            quotes = angel_market_data.full_quotes(
                [str(signal.get("symbol") or "") for signal in signals]
            )
            enriched = []
            for signal in signals:
                symbol = str(signal.get("symbol") or "").upper()
                quote = quotes.get(symbol)
                if quote and quote.get("ltp", 0) > 0:
                    enriched.append({
                        **signal,
                        "nse_ltp": signal.get("ltp"),
                        "ltp": round(float(quote["ltp"]), 2),
                        "volume": quote.get("volume", signal.get("volume", 0)),
                        "angel_quote": quote,
                        "realtime_source": "angel_one_read_only",
                    })
                else:
                    enriched.append(signal)
            signals = enriched
        except Exception:
            logger.exception("Read-only Angel One quote overlay failed; retaining NSE data")
    signals = [attach_sector(signal) for signal in signals]
    # The public NSE live endpoint is polled every minute. Where it supplies
    # cumulative volume, retain a session VWAP from those real observations.
    # This is deliberately labelled observation-based; it is not fabricated
    # 5-minute OHLCV and never turns a candidate into an order recommendation.
    session_date = now_ist().date()
    signals = [
        {
            **signal,
            "intraday_context": observe_intraday(
                str(signal.get("symbol") or ""),
                float(signal.get("ltp") or 0),
                float(signal.get("volume") or 0),
                session_date,
            ),
        }
        for signal in signals
    ]
    if signals:
        try:
            bars_by_symbol = repository.daily_equity_bars_for_symbols(
                (str(signal.get("symbol") or "") for signal in signals)
            )
            signals = [
                apply_daily_technical_context(
                    signal,
                    technical_context(bars_by_symbol.get(str(signal.get("symbol") or ""), [])),
                )
                for signal in signals
            ]
            signals = [
                apply_intraday_observation_context(signal, signal.get("intraday_context"))
                for signal in signals
            ]
            signals = [{
                **signal,
                "trap_context": trap_risk(signal, signal.get("technical_context")),
            } for signal in signals]
        except Exception:
            logger.exception("Could not attach daily technical context to scanner results")
        try:
            announcements = normalize_nse_announcements(fetch_corporate_announcements())
            repository.upsert_corporate_announcements(announcements)
            signals = [{**signal, "news_context": latest_event_risk(announcements, str(signal.get("symbol") or ""))} for signal in signals]
        except Exception:
            logger.exception("Could not attach NSE disclosure context to scanner results")
        try:
            market_context = normalize_market_overview(
                fetch_market_indices(), fetch_fii_dii_activity(),
            )
            cache.set("market-overview", market_context, ttl=settings.cache_ttl_seconds)
            signals = [
                {
                    **signal,
                    "cas_context": confidence_analysis(
                        signal.get("technical_context"), signal.get("news_context"), market_context,
                    ),
                }
                for signal in signals
            ]
        except Exception:
            logger.exception("Could not attach public VIX/regime/CAS context to scanner results")
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

        # Alert delivery is opt-in. Per-symbol/day/channel dedup prevents a
        # frequent scanner refresh from becoming a notification storm.
        if settings.alert_webhook_url or settings.ntfy_topic_url or settings.telegram_bot_token:
            for signal in signals:
                if int(signal.get("confidence") or 0) < settings.alert_min_confidence:
                    continue
                for channel, endpoint in (
                    ("webhook", settings.alert_webhook_url),
                    ("ntfy", settings.ntfy_topic_url),
                    ("telegram", settings.telegram_bot_token),
                ):
                    if not endpoint:
                        continue
                    key = f"candidate:{ist_trade_date(captured_at)}:{signal.get('symbol')}:{channel}"
                    if not repository.reserve_alert(key, channel, captured_at):
                        continue
                    deliveries = dispatch_candidate_alert(
                        signal,
                        webhook_url=endpoint if channel == "webhook" else None,
                        ntfy_topic_url=endpoint if channel == "ntfy" else None,
                        telegram_bot_token=endpoint if channel == "telegram" else None,
                        telegram_chat_id=settings.telegram_chat_id if channel == "telegram" else None,
                    )
                    for delivery in deliveries:
                        repository.complete_alert(
                            key, delivered=delivery.delivered, detail=delivery.detail,
                            observed_at=captured_at,
                        )

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


async def ingest_daily_bhavcopy(trade_date: str | None = None) -> int:
    """Collect one public NSE daily equity file and persist normalized bars."""
    target_date = datetime.fromisoformat(trade_date).date() if trade_date else now_ist().date()
    try:
        bars = await asyncio.to_thread(collect_equity_bhavcopy, target_date)
        stored = await asyncio.to_thread(repository.upsert_daily_equity_bars, bars)
        logger.info("Stored %s daily NSE equity bars for %s", stored, target_date.isoformat())
        return stored
    except Exception:
        logger.exception("Daily NSE bhavcopy ingestion failed for %s", target_date.isoformat())
        return 0


async def ingest_participant_oi(report_date: str | None = None) -> int:
    """Store the public EOD participant OI report, trying recent calendar days.

    A missed holiday report is normal.  The date remains attached to the rows,
    so the dashboard cannot label the prior close's positions as intraday.
    """
    target = datetime.fromisoformat(report_date).date() if report_date else now_ist().date()
    dates = [target - timedelta(days=offset) for offset in range(0, 5)]
    for candidate_date in dates:
        try:
            rows = await asyncio.to_thread(collect_participant_oi, candidate_date)
            if rows:
                stored = await asyncio.to_thread(repository.upsert_participant_oi, rows)
                logger.info("Stored %s participant OI rows for %s", stored, candidate_date.isoformat())
                return stored
        except Exception:
            logger.exception("Could not ingest participant OI report for %s", candidate_date.isoformat())
    return 0


async def scheduled_bhavcopy_ingestion() -> None:
    """Try NSE's completed daily bhavcopy after the regular market session."""
    await ingest_daily_bhavcopy()


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
    scheduler.add_job(
        scheduled_bhavcopy_ingestion,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=10, timezone=IST),
        id="nse-daily-bhavcopy-ingestion",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        ingest_participant_oi,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=15, timezone=IST),
        id="nse-participant-oi-ingestion",
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
        "daily_equity_data": repository.daily_equity_bar_summary(),
    }


@app.get("/api/sources")
async def sources():
    """Declared public-data coverage; unavailable sources are never implied live."""
    return {
        "sources": public_source_inventory(),
        "angel_one_market_data": {
            "configured": angel_market_data is not None,
            "mode": "read_only_quotes_and_candles" if angel_market_data else "disabled",
            "order_execution": False,
        },
        "policy": "Only public/free sources are used. A NOT_CONFIGURED source is not silently substituted or inferred.",
    }


@app.get("/api/intraday/{symbol}")
async def intraday_candles(symbol: str, interval: str = Query("FIVE_MINUTE")):
    """Return read-only Angel One intraday candles when configured."""
    if angel_market_data is None:
        return {"symbol": symbol.upper(), "configured": False, "candles": [],
                "message": "Angel One read-only market data is not configured"}
    allowed = {"ONE_MINUTE", "THREE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE",
               "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY"}
    if interval not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported candle interval")
    try:
        candles = await asyncio.to_thread(
            angel_market_data.intraday_candles, symbol.upper(), interval=interval,
        )
        closes = [float(c["close"]) for c in candles if float(c.get("close") or 0) > 0]
        fast = calculate_ema(closes, 5)
        slow = calculate_ema(closes, 13)
        return {"symbol": symbol.upper(), "configured": True,
                "source": "angel_one_read_only", "candles": candles,
                "vwap": candle_vwap(candles),
                "ema_fast": fast, "ema_slow": slow,
                "trend": ("UPTREND" if fast is not None and slow is not None and fast > slow
                           else "DOWNTREND" if fast is not None and slow is not None and fast < slow
                           else "INSUFFICIENT_DATA"),
                "order_execution": False}
    except Exception as exc:
        logger.exception("Angel One intraday candle request failed for %s", symbol)
        return {"symbol": symbol.upper(), "configured": True, "candles": [],
                "source": "angel_one_read_only", "error": str(exc),
                "order_execution": False}


@app.get("/api/oi-signals")
async def oi_signals(
    refresh:      bool  = Query(False, description="Force fresh NSE fetch"),
    signal:       str   = Query("",    description="Filter by signal type"),
    tier:         str   = Query("",    description="Filter by tier: HIGH | MEDIUM"),
    sector:       str   = Query("",    description="Curated display sector; unknown symbols are Unclassified"),
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
    if sector:
        results = [r for r in results if str(r.get("sector") or "Unclassified").casefold() == sector.strip().casefold()]
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
        "available_sectors": known_sectors(),
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
    events = await asyncio.to_thread(repository.backtest_events, trade_date, trade_date)
    return {
        "trade_date": trade_date,
        "metrics": await asyncio.to_thread(repository.performance_for_date, trade_date),
        "breakdowns": summarize_candidate_backtest(events),
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
    }


def _validated_backtest_dates(start_date: str, end_date: str) -> tuple[str, str]:
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Dates must use YYYY-MM-DD") from exc
    if start > end:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date")
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Backtest range is limited to 366 days")
    return start.isoformat(), end.isoformat()


@app.get("/api/backtest")
async def backtest(
    from_date: str = Query(..., description="IST start date, YYYY-MM-DD"),
    to_date: str = Query(..., description="IST end date, YYYY-MM-DD"),
):
    """Analyze stored candidate events; does not claim strategy performance."""
    start, end = _validated_backtest_dates(from_date, to_date)
    events = await asyncio.to_thread(repository.backtest_events, start, end)
    return {
        "from_date": start,
        "to_date": end,
        "metrics": summarize_candidate_backtest(events),
    }


@app.get("/api/backtest/export.csv")
async def backtest_export(
    from_date: str = Query(..., description="IST start date, YYYY-MM-DD"),
    to_date: str = Query(..., description="IST end date, YYYY-MM-DD"),
):
    """Export the underlying stored candidate-event rows for audit/recalculation."""
    start, end = _validated_backtest_dates(from_date, to_date)
    events = await asyncio.to_thread(repository.backtest_events, start, end)
    fields = [
        "captured_at_ist", "trade_date", "symbol", "signal", "direction", "confidence",
        "entry", "stop_loss", "target_1", "target_2", "risk_reward", "risk_source",
        "current_price", "exit_price", "max_target_hit", "status", "result", "sector",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(events)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="nse-oi-candidates-{start}-to-{end}.csv"'},
    )


@app.get("/api/market-overview")
async def market_overview(refresh: bool = Query(False)):
    """Cached public NSE indices, VIX, breadth, and FII/DII cash activity."""
    cache_key = "market-overview"
    if refresh:
        cache.delete(cache_key)
    cached = cache.get(cache_key)
    if cached is not None:
        return {"cached": True, **cached}
    indices, activity = await asyncio.gather(
        asyncio.to_thread(fetch_market_indices),
        asyncio.to_thread(fetch_fii_dii_activity),
    )
    result = normalize_market_overview(indices, activity)
    cache.set(cache_key, result, ttl=settings.cache_ttl_seconds)
    return {"cached": False, **result}


@app.get("/api/market-regime")
async def market_regime(refresh: bool = Query(False)):
    """Public-VIX regime context, deliberately separate from a trade call."""
    cache_key = "market-overview"
    if refresh:
        cache.delete(cache_key)
    overview = cache.get(cache_key)
    if overview is None:
        indices, activity = await asyncio.gather(
            asyncio.to_thread(fetch_market_indices),
            asyncio.to_thread(fetch_fii_dii_activity),
        )
        overview = normalize_market_overview(indices, activity)
        cache.set(cache_key, overview, ttl=settings.cache_ttl_seconds)
    return {
        "source": "public NSE index context",
        **classify_market_regime(None, overview),
    }


@app.get("/api/market-intelligence")
async def market_intelligence():
    """Compact daily briefing from the app's existing public-source cache."""
    overview = cache.get("market-overview")
    if overview is None:
        indices, activity = await asyncio.gather(
            asyncio.to_thread(fetch_market_indices),
            asyncio.to_thread(fetch_fii_dii_activity),
        )
        overview = normalize_market_overview(indices, activity)
        cache.set("market-overview", overview, ttl=settings.cache_ttl_seconds)
    announcements = await asyncio.to_thread(repository.recent_corporate_announcements, limit=200)
    return {
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        **build_market_intelligence(
            overview,
            classify_market_regime(None, overview),
            announcements,
            cache.get("all_signals") or [],
        ),
    }


@app.get("/api/cas/{symbol}")
async def candidate_confidence_analysis(symbol: str):
    """Return stored CAS context for a currently cached OI/price candidate."""
    symbol = symbol.upper().strip()
    candidate = next(
        (row for row in (cache.get("all_signals") or []) if row.get("symbol") == symbol),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="No current candidate for symbol; refresh the scanner first")
    return {
        "symbol": symbol,
        "classification": candidate.get("classification"),
        "trade_recommendation": candidate.get("trade_recommendation", "NO_TRADE"),
        "cas": candidate.get("cas_context") or confidence_analysis(
            candidate.get("technical_context"), candidate.get("news_context"), cache.get("market-overview"),
        ),
    }


@app.get("/api/news")
async def corporate_news(symbol: str = Query(""), limit: int = Query(100, ge=1, le=500), refresh: bool = Query(False)):
    """Latest public NSE corporate disclosures, labeled for event risk not sentiment."""
    if refresh:
        announcements = normalize_nse_announcements(await asyncio.to_thread(fetch_corporate_announcements))
        await asyncio.to_thread(repository.upsert_corporate_announcements, announcements)
    rows = await asyncio.to_thread(repository.recent_corporate_announcements, symbol=symbol or None, limit=limit)
    return {
        "source": "NSE corporate announcements",
        "symbol": symbol.upper().strip() or None,
        "announcements": rows,
        "methodology_caveat": "Labels indicate potential event volatility only; they do not infer news sentiment or a trade direction.",
    }


@app.get("/api/participant-oi")
async def participant_oi(refresh: bool = Query(False)):
    """Latest stored public NSE EOD participant-wise OI report."""
    if refresh:
        await ingest_participant_oi()
    result = await asyncio.to_thread(repository.latest_participant_oi)
    return {
        "source": "NSE F&O participant-wise OI end-of-day report",
        "data_frequency": "end_of_day",
        "is_intraday": False,
        "methodology_caveat": "Participant OI is published as an end-of-day report and is context only, not a live participant-position or trade signal.",
        **result,
    }


@app.get("/api/technical/{symbol}")
async def technical_analysis(symbol: str):
    """Daily NSE-bar technical context, not an intraday trade instruction."""
    symbol = symbol.upper().strip()
    if not symbol or len(symbol) > 32 or not symbol.replace("&", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid NSE symbol")
    bars = await asyncio.to_thread(repository.daily_equity_bars_for_symbol, symbol)
    context = technical_context(bars)
    return {
        "symbol": symbol,
        "source": "NSE daily equity bhavcopy",
        "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        **context,
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

    history: list[dict] = []
    try:
        await asyncio.to_thread(repository.record_option_chain_snapshot, result, now_ist())
        history = await asyncio.to_thread(repository.option_chain_history, symbol)
    except Exception:
        logger.exception("Could not persist option-chain history for %s", symbol)
    result["pcr_history"] = history
    result["pcr_trend"] = summarize_pcr_trend(history)
    cache.set(cache_key, result, ttl=settings.cache_ttl_seconds)
    return {"source": "live", **result}


@app.get("/api/option-chain/{symbol}/history")
async def option_chain_history(symbol: str, limit: int = Query(200, ge=1, le=1000)):
    """Stored PCR/max-pain timeline from prior public NSE chain observations."""
    symbol = symbol.upper().strip()
    history = await asyncio.to_thread(repository.option_chain_history, symbol, limit=limit)
    return {
        "symbol": symbol,
        "source": "server-owned option-chain snapshots",
        "history": history,
        "trend": summarize_pcr_trend(history),
    }


@app.get("/api/option-chain/{symbol}/heatmap")
async def option_chain_heatmap(symbol: str, refresh: bool = Query(False)):
    """OI/?OI intensity ladder based on the current public chain window."""
    symbol = symbol.upper().strip()
    cache_key = f"chain:{symbol}"
    if refresh:
        cache.delete(cache_key)
    chain = cache.get(cache_key)
    if chain is None:
        chain = await asyncio.to_thread(get_option_chain_analysis, symbol)
        if "error" not in chain:
            cache.set(cache_key, chain, ttl=settings.cache_ttl_seconds)
    if "error" in chain:
        return JSONResponse(status_code=200, content={
            "symbol": symbol, "error": chain["error"], "strikes": [],
            "methodology_caveat": "No heatmap is available until a public NSE option chain is returned.",
        })
    return {"symbol": symbol, "expiry": chain.get("expiry"), **option_oi_heatmap(chain.get("strikes") or [])}


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
