# nse_fetcher.py — Production-level NSE data fetcher
# Singapore deployment · cloudscraper for Cloudflare bypass
#
# CONFIRMED WORKING from Singapore (live tested 2026-09-01):
#   /api/live-analysis-oi-spurts-underlyings  → ALL F&O OI + price data
#   /api/option-chain-indices?symbol=NIFTY    → NIFTY/BANKNIFTY chain
#   /api/option-chain-equities?symbol=X       → stock option chain

import time
import logging
import cloudscraper
from threading import Lock
from app.config import SESSION_REFRESH_SECONDS

logger = logging.getLogger(__name__)
NSE_BASE = "https://www.nseindia.com"

# Headers sent with every API request
COMMON_HEADERS = {
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Cache-Control":    "no-cache, no-store",
    "Pragma":           "no-cache",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
    "Connection":       "keep-alive",
}

# Seed pages visited in sequence — each one adds cookies needed for API calls
SEED_SEQUENCE = [
    ("https://www.nseindia.com/",
     {"Referer": "https://www.google.com/", "Sec-Fetch-Mode": "navigate"}),
    ("https://www.nseindia.com/market-data/live-equity-market",
     {"Referer": "https://www.nseindia.com/"}),
    ("https://www.nseindia.com/market-data/equity-derivatives-watch",
     {"Referer": "https://www.nseindia.com/market-data/live-equity-market"}),
    ("https://www.nseindia.com/option-chain",
     {"Referer": "https://www.nseindia.com/market-data/equity-derivatives-watch"}),
]


class NSECloudSession:
    """
    Thread-safe NSE session using cloudscraper.

    Manages Cloudflare cookie lifecycle:
    - Builds session by visiting seed pages (simulates real browser navigation)
    - Refreshes session every SESSION_REFRESH_SECONDS (10 min)
    - Re-seeds on 401/403/429 responses (cookie expired)
    - Safe JSON parsing prevents crash on empty/HTML responses
    """

    def __init__(self):
        self._scraper   = None
        self._lock      = Lock()
        self._last_init = 0.0

    def _build_scraper(self) -> cloudscraper.CloudScraper:
        logger.info("Initialising NSE cloudscraper session…")
        sc = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        sc.headers.update(COMMON_HEADERS)

        for url, extra_headers in SEED_SEQUENCE:
            sc.headers.update({"Referer": extra_headers.get("Referer", "https://www.nseindia.com/")})
            try:
                r = sc.get(url, timeout=20)
                logger.info(f"  seed {r.status_code} {url}")
            except Exception as e:
                logger.warning(f"  seed failed {url}: {e}")
            time.sleep(2.0)

        sc.headers.update(COMMON_HEADERS)   # reset to API headers
        sc.headers.update({"Referer": "https://www.nseindia.com/"})
        logger.info("NSE session ready.")
        return sc

    def _ensure_session(self):
        now = time.time()
        if self._scraper is None or (now - self._last_init) > SESSION_REFRESH_SECONDS:
            self._scraper  = self._build_scraper()
            self._last_init = time.time()

    def _safe_json(self, resp, url: str):
        if not resp.content:
            logger.warning(f"Empty response body: {url}")
            return None
        ct = resp.headers.get("Content-Type", "")
        if "html" in ct.lower():
            snippet = resp.text[:120].replace("\n", " ")
            logger.warning(f"HTML body (Cloudflare block?): {url} → {snippet!r}")
            return None
        try:
            return resp.json()
        except ValueError as e:
            logger.warning(f"JSON parse error at {url}: {e} | body={resp.text[:80]!r}")
            return None

    def get(self, url: str, referer: str = "https://www.nseindia.com/",
            retries: int = 3) -> dict | None:
        with self._lock:
            self._ensure_session()
            self._scraper.headers.update({"Referer": referer})

            for attempt in range(retries):
                try:
                    resp = self._scraper.get(url, timeout=25)
                    code = resp.status_code

                    if code == 200:
                        return self._safe_json(resp, url)

                    if code in (401, 403, 429):
                        logger.warning(f"HTTP {code} on attempt {attempt+1}, re-seeding…")
                        self._scraper  = self._build_scraper()
                        self._last_init = time.time()
                        self._scraper.headers.update({"Referer": referer})
                        time.sleep(3 * (attempt + 1))

                    elif code == 404:
                        logger.warning(f"HTTP 404 — endpoint removed: {url}")
                        return None

                    else:
                        logger.warning(f"HTTP {code}: {url}")
                        return None

                except Exception as e:
                    logger.error(f"Request error (attempt {attempt+1}): {url} — {e}")
                    time.sleep(2)

            logger.error(f"All {retries} attempts failed: {url}")
            return None


# Singleton session shared across the app
_nse = NSECloudSession()


# ── Public data functions ──────────────────────────────────────────────────────

def fetch_all_fno_oi_change() -> list[dict]:
    """
    Fetch OI + price data for ALL F&O underlyings.
    Endpoint: /api/live-analysis-oi-spurts-underlyings
    CONFIRMED WORKING from Singapore.

    Returns list of rows, each with symbol, ltp, pChange, oi, oiChange, etc.
    """
    data = _nse.get(
        f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings",
        referer="https://www.nseindia.com/market-data/equity-derivatives-watch"
    )
    if not data:
        return []
    rows = data.get("data", [])
    logger.info(f"OI spurts: {len(rows)} F&O rows received")
    return rows


def fetch_option_chain_index(symbol: str) -> dict | None:
    """Option chain for NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY."""
    return _nse.get(
        f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}",
        referer="https://www.nseindia.com/option-chain"
    )


def fetch_option_chain_equity(symbol: str) -> dict | None:
    """Option chain for an individual F&O equity (e.g. RELIANCE, TCS)."""
    return _nse.get(
        f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}",
        referer="https://www.nseindia.com/option-chain"
    )


def fetch_quote_derivative(symbol: str) -> dict | None:
    """Futures quote — price, OI, expiry for a specific symbol."""
    return _nse.get(
        f"{NSE_BASE}/api/quote-derivative?symbol={symbol}",
        referer="https://www.nseindia.com/get-quotes/derivatives?symbol=" + symbol
    )


def test_nse_connectivity() -> dict:
    """
    Quick connectivity test — call from /api/debug to diagnose issues.
    Returns status for each key NSE endpoint.
    """
    results = {}

    urls = [
        ("oi_spurts", f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings",
         "https://www.nseindia.com/market-data/equity-derivatives-watch"),
        ("option_chain_nifty", f"{NSE_BASE}/api/option-chain-indices?symbol=NIFTY",
         "https://www.nseindia.com/option-chain"),
        ("option_chain_reliance", f"{NSE_BASE}/api/option-chain-equities?symbol=RELIANCE",
         "https://www.nseindia.com/option-chain"),
        ("quote_derivative", f"{NSE_BASE}/api/quote-derivative?symbol=RELIANCE",
         "https://www.nseindia.com/get-quotes/derivatives?symbol=RELIANCE"),
    ]

    for name, url, referer in urls:
        try:
            data = _nse.get(url, referer=referer)
            if data is None:
                results[name] = "blocked/empty"
            else:
                rows = data.get("data", data.get("records", data.get("stocks", [])))
                count = len(rows) if isinstance(rows, list) else "ok"
                results[name] = f"ok ({count} rows)"
        except Exception as e:
            results[name] = f"error: {e}"

    return results
