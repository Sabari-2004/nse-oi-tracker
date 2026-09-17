"""Bounded public NSE index-history backfill."""
from datetime import date, timedelta
import logging
import time
from app.config import INDICES
from app.nse_fetcher import fetch_index_history
from utils.time import now_ist

logger = logging.getLogger(__name__)
INDEX_TYPES={"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FINANCIAL SERVICES","MIDCPNIFTY":"NIFTY MIDCAP SELECT"}
def backfill_index_bars(repository, *, days=60, max_downloads=60):
    end=now_ist().date()-timedelta(days=1); start=end-timedelta(days=days*2); stored=0
    for symbol in INDICES:
        try:
            rows=fetch_index_history(INDEX_TYPES[symbol], start, end)
        except Exception:
            logger.exception("Index history fetch failed for %s", symbol)
            rows=[]
        stored += repository.upsert_daily_index_bars([{**row,"symbol":symbol} for row in rows])
        time.sleep(0.25)
    return {"stored":stored,"symbols":len(INDICES)}
