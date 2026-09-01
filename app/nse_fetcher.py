# nse_fetcher.py — NSE session manager + all dynamic F&O data fetchers

import requests
import time
import logging
from threading import Lock
from app.config import SESSION_REFRESH_SECONDS

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# Seed pages to grab valid session cookies
SEED_URLS = [
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/live-equity-market",
    "https://www.nseindia.com/option-chain",
]


class NSESession:
    """Persistent NSE session with automatic cookie refresh."""

    def __init__(self):
        self._session: requests.Session | None = None
        self._lock = Lock()
        self._last_refresh = 0
        self._init_session()

    def _init_session(self):
        logger.info("Initializing NSE session...")
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            for url in SEED_URLS:
                r = session.get(url, timeout=15)
                logger.debug(f"Seeded {url} → {r.status_code}")
                time.sleep(1.5)
            self._session = session
            self._last_refresh = time.time()
            logger.info("NSE session ready.")
        except Exception as e:
            logger.error(f"Session init failed: {e}")
            self._session = session

    def _refresh_if_needed(self):
        if time.time() - self._last_refresh > SESSION_REFRESH_SECONDS:
            self._init_session()

    def get(self, url: str, retries: int = 3) -> dict | None:
        with self._lock:
            self._refresh_if_needed()
            for attempt in range(retries):
                try:
                    resp = self._session.get(url, timeout=15)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code in (401, 403):
                        logger.warning(f"NSE blocked ({resp.status_code}), refreshing session...")
                        self._init_session()
                        time.sleep(2)
                    else:
                        logger.warning(f"HTTP {resp.status_code} for {url}")
                        return None
                except requests.Timeout:
                    logger.warning(f"Timeout attempt {attempt+1}: {url}")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Error fetching {url}: {e}")
                    return None
            return None


# ─── Singleton session ────────────────────────────────────────────────────────
_nse = NSESession()


# ─────────────────────────────────────────────────────────────────────────────
#  DYNAMIC REAL-TIME F&O SCANNERS
#  These return ALL F&O stocks — no hardcoded lists
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_fno_oi_change() -> dict | None:
    """
    Fetch OI change data for ALL F&O underlyings from NSE's
    live OI spurts endpoint.

    Returns a list of all F&O stocks with:
      symbol, oi, oiChange, oiChangePct, ltp, previousClose, pChange
    """
    url = f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings"
    return _nse.get(url)


def fetch_fno_price_data() -> dict | None:
    """
    Fetch live prices for ALL securities in F&O from the
    NSE stock indices endpoint.

    Returns list with symbol, lastPrice, change, pChange, totalTradedVolume
    """
    # SECURITIES IN F&O index — ~200 stocks
    url = f"{NSE_BASE}/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
    return _nse.get(url)


def fetch_oi_buildup(category: str = "long") -> dict | None:
    """
    Fetch pre-computed buildup lists from NSE analytics.

    category options:
      'long'           → Long Buildup  (Price ↑ OI ↑)
      'short'          → Short Buildup (Price ↓ OI ↑)
      'short_covering' → Short Covering (Price ↑ OI ↓)
      'long_unwinding' → Long Unwinding (Price ↓ OI ↓)
    """
    category_map = {
        "long":           "oi-change-with-price-gainers",     # Long Buildup
        "short":          "oi-change-with-price-losers",      # Short Buildup
        "short_covering": "price-change-with-oi-reducers",    # Short Covering
        "long_unwinding": "oi-reducers-with-price-reducers",  # Long Unwinding
    }
    key = category_map.get(category, "oi-change-with-price-gainers")
    url = f"{NSE_BASE}/api/live-analysis-{key}"
    return _nse.get(url)


def fetch_oi_spurts() -> dict | None:
    """
    NSE's OI spurts — stocks where OI jumped most in current session.
    These are prime scalping candidates.
    """
    url = f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings"
    return _nse.get(url)


def fetch_most_active_derivatives() -> dict | None:
    """Most active F&O contracts by value — high liquidity stocks."""
    url = f"{NSE_BASE}/api/live-analysis-variations?index=most_act_fo_cont_by_trd_val"
    return _nse.get(url)


# ─── Option Chain (single symbol) ────────────────────────────────────────────

def fetch_option_chain_index(symbol: str) -> dict | None:
    url = f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}"
    return _nse.get(url)


def fetch_option_chain_equity(symbol: str) -> dict | None:
    url = f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}"
    return _nse.get(url)


def fetch_quote_derivative(symbol: str) -> dict | None:
    url = f"{NSE_BASE}/api/quote-derivative?symbol={symbol}"
    return _nse.get(url)
