# nse_fetcher.py — NSE data fetcher using cloudscraper (Singapore region)
#
# CONFIRMED WORKING ENDPOINTS (from Singapore server, tested live):
#   ✅ /api/live-analysis-oi-spurts-underlyings  → ALL F&O stocks, OI + price data
#   ✅ /api/option-chain-indices?symbol=NIFTY     → index option chains
#   ✅ /api/option-chain-equities?symbol=RELIANCE → stock option chains
#   ✅ /api/quote-derivative?symbol=RELIANCE      → futures quote
#
# CONFIRMED 404 (endpoint names changed by NSE):
#   ❌ /api/live-analysis-oi-change-with-price-gainers
#   ❌ /api/live-analysis-oi-change-with-price-losers
#   ❌ /api/live-analysis-price-change-with-oi-reducers
#   ❌ /api/live-analysis-oi-reducers-with-price-reducers
#   ❌ /api/equity-stockIndices?index=SECURITIES IN F&O

import time
import logging
import cloudscraper
from threading import Lock
from app.config import SESSION_REFRESH_SECONDS

logger = logging.getLogger(__name__)
NSE_BASE = "https://www.nseindia.com"

HEADERS = {
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Referer":          "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Cache-Control":    "no-cache",
    "Pragma":           "no-cache",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
}

# Seed pages — visited in order to acquire Cloudflare clearance cookies
SEED_URLS = [
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/live-equity-market",
    "https://www.nseindia.com/market-data/equity-derivatives-watch",
    "https://www.nseindia.com/option-chain",
]


class NSECloudSession:
    def __init__(self):
        self._scraper = None
        self._lock    = Lock()
        self._last_refresh: float = 0.0

    def _build_scraper(self):
        logger.info("Building NSE cloudscraper session (chrome/windows)…")
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        s.headers.update(HEADERS)
        for url in SEED_URLS:
            try:
                r = s.get(url, timeout=20)
                logger.info(f"  seed {url} → HTTP {r.status_code}")
            except Exception as e:
                logger.warning(f"  seed failed {url}: {e}")
            time.sleep(2.0)
        logger.info("NSE session ready.")
        return s

    def _ensure_session(self):
        now = time.time()
        if self._scraper is None or (now - self._last_refresh) > SESSION_REFRESH_SECONDS:
            self._scraper = self._build_scraper()
            self._last_refresh = time.time()

    def _safe_json(self, resp, url):
        if not resp.content:
            logger.warning(f"Empty body: {url}")
            return None
        ct = resp.headers.get("Content-Type", "")
        if "html" in ct.lower():
            logger.warning(f"HTML body (blocked): {url}")
            return None
        try:
            return resp.json()
        except ValueError as e:
            logger.warning(f"Non-JSON ({e}): {url} | {resp.text[:100]!r}")
            return None

    def get(self, url: str, retries: int = 3) -> dict | None:
        with self._lock:
            self._ensure_session()
            for attempt in range(retries):
                try:
                    resp = self._scraper.get(url, timeout=20)
                    if resp.status_code == 200:
                        return self._safe_json(resp, url)
                    elif resp.status_code in (401, 403, 429):
                        logger.warning(f"HTTP {resp.status_code} attempt {attempt+1} — re-seeding: {url}")
                        self._scraper = self._build_scraper()
                        self._last_refresh = time.time()
                        time.sleep(4 * (attempt + 1))
                    elif resp.status_code == 404:
                        logger.warning(f"HTTP 404 (endpoint changed): {url}")
                        return None
                    else:
                        logger.warning(f"HTTP {resp.status_code}: {url}")
                        return None
                except Exception as e:
                    logger.error(f"Request error attempt {attempt+1}: {url} — {e}")
                    time.sleep(2)
            return None


_nse = NSECloudSession()


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIRMED WORKING NSE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_fno_oi_change() -> list[dict]:
    """
    Fetch OI + price data for ALL F&O underlyings.

    Endpoint: /api/live-analysis-oi-spurts-underlyings
    Status:   ✅ CONFIRMED WORKING from Singapore (live tested)

    Returns each row with fields (field names vary — all handled in oi_analyzer):
      underlying / symbol    → stock name
      oi                     → open interest (contracts)
      oiChange               → OI change
      oiChangePct / perOIchange → OI change %
      ltp / lastPrice        → last traded price
      pChange / change       → price change %

    This single endpoint gives BOTH OI + price data for ALL ~200 F&O stocks.
    We classify all 4 signal types (Long Buildup / Short Buildup /
    Short Covering / Long Unwinding) from price direction + OI direction.
    """
    data = _nse.get(f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings")
    if not data:
        logger.warning("OI spurts endpoint returned no data (market closed?)")
        return []
    rows = data.get("data", [])
    logger.info(f"OI spurts: {len(rows)} F&O underlyings received")
    return rows


def fetch_option_chain_index(symbol: str) -> dict | None:
    """Option chain for NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY."""
    return _nse.get(f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}")


def fetch_option_chain_equity(symbol: str) -> dict | None:
    """Option chain for an individual F&O stock."""
    return _nse.get(f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}")


def fetch_quote_derivative(symbol: str) -> dict | None:
    """Futures quote for a specific symbol (OI, price, expiry)."""
    return _nse.get(f"{NSE_BASE}/api/quote-derivative?symbol={symbol}")
