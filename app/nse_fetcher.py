# nse_fetcher.py — Production NSE data fetcher (Singapore)
# Uses cloudscraper for Cloudflare bypass.
#
# Key insight: NSE checks the Referer + cookies per API call.
# Option chain APIs need a FRESH per-symbol page visit immediately
# before the API call to get the right cookies — done via _get_seeded().

import time
import logging
import cloudscraper
from threading import RLock          # Re-entrant so _get_seeded can call _raw_get
from app.config import SESSION_REFRESH_SECONDS

logger   = logging.getLogger(__name__)
NSE_BASE = "https://www.nseindia.com"

# ── Headers for page navigation (HTML seed visits) ───────────────────────────
NAV_HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Cache-Control":   "no-cache",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── Headers for JSON API calls ────────────────────────────────────────────────
API_HEADERS = {
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
    "Cache-Control":    "no-cache",
    "Connection":       "keep-alive",
}

# ── Initial session seed sequence ─────────────────────────────────────────────
SEED_PAGES = [
    ("https://www.nseindia.com/",
     "https://www.google.com/"),
    ("https://www.nseindia.com/market-data/live-equity-market",
     "https://www.nseindia.com/"),
    ("https://www.nseindia.com/market-data/equity-derivatives-watch",
     "https://www.nseindia.com/market-data/live-equity-market"),
    ("https://www.nseindia.com/option-chain",
     "https://www.nseindia.com/market-data/equity-derivatives-watch"),
]


class NSESession:
    """
    Thread-safe cloudscraper session for NSE.

    Uses RLock (re-entrant) so inner _raw_get calls from _get_seeded
    don't deadlock against themselves.

    Session lifecycle:
    - Built on first use by visiting 4 seed pages (2s gap each)
    - Auto-refreshed every SESSION_REFRESH_SECONDS (10 min)
    - Re-seeded on 401/403/429 responses (cookie expired)
    """

    def __init__(self):
        self._sc        = None
        self._lock      = RLock()        # re-entrant — critical for _get_seeded
        self._last_init = 0.0

    # ── Internal: build a fresh scraper session ────────────────────────────────

    def _build(self) -> cloudscraper.CloudScraper:
        logger.info("Building NSE cloudscraper session…")
        sc = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        for url, referer in SEED_PAGES:
            sc.headers.update({**NAV_HEADERS, "Referer": referer})
            try:
                r = sc.get(url, timeout=20)
                logger.info(f"  seed {r.status_code} → {url}")
            except Exception as e:
                logger.warning(f"  seed failed {url}: {e}")
            time.sleep(2.2)
        logger.info("NSE session ready.")
        return sc

    def _ensure(self):
        """Call inside lock. Rebuild if session missing or stale."""
        now = time.time()
        if self._sc is None or (now - self._last_init) > SESSION_REFRESH_SECONDS:
            self._sc        = self._build()
            self._last_init = time.time()

    def _safe_json(self, resp, label: str):
        if resp is None:
            return None
        if not resp.content:
            logger.warning(f"Empty body: {label}")
            return None
        ct = resp.headers.get("Content-Type", "")
        if "html" in ct.lower():
            logger.warning(f"HTML body (blocked by Cloudflare?): {label} status={resp.status_code}")
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.warning(f"JSON parse fail: {label}: {e}")
            return None

    # ── Internal: raw GET (must hold lock when called) ─────────────────────────

    def _raw_get(self, url: str, referer: str, is_page: bool = False) -> object:
        """Execute one HTTP GET. Returns response object or None on failure."""
        headers = NAV_HEADERS if is_page else API_HEADERS
        self._sc.headers.update({**headers, "Referer": referer})
        try:
            return self._sc.get(url, timeout=25)
        except Exception as e:
            logger.error(f"Request error: {url}: {e}")
            return None

    # ── Public: standard JSON GET ──────────────────────────────────────────────

    def get(self, url: str, referer: str, retries: int = 2) -> dict | None:
        """Fetch JSON from NSE API. Auto-retries with session rebuild on 4xx."""
        with self._lock:
            self._ensure()
            for attempt in range(retries):
                resp = self._raw_get(url, referer)
                if resp is None:
                    time.sleep(2)
                    continue
                code = resp.status_code
                if code == 200:
                    return self._safe_json(resp, url)
                if code in (401, 403, 429):
                    logger.warning(f"HTTP {code} (attempt {attempt+1}) — rebuilding session: {url}")
                    self._sc        = self._build()
                    self._last_init = time.time()
                    time.sleep(3)
                elif code == 404:
                    logger.warning(f"HTTP 404 — endpoint removed: {url}")
                    return None
                else:
                    logger.warning(f"HTTP {code}: {url}")
                    return None
            return None

    # ── Public: page-seeded GET (visits a page first, then calls API) ──────────

    def get_seeded(self, seed_url: str, seed_referer: str,
                   api_url: str, api_referer: str,
                   retries: int = 2) -> dict | None:
        """
        Visit seed_url first (to set symbol-specific cookies), then
        call api_url. Both inside the same lock — no deadlock because RLock.

        Used for option chain: NSE requires a fresh page visit per symbol
        before the API call will return JSON (otherwise returns HTML/403).
        """
        with self._lock:
            self._ensure()
            for attempt in range(retries):
                # 1. Visit the seed page (sets fresh cookies for this symbol)
                logger.info(f"  option-chain seed: {seed_url}")
                seed_resp = self._raw_get(seed_url, seed_referer, is_page=True)
                if seed_resp is not None:
                    logger.info(f"  seed status: {seed_resp.status_code}")
                time.sleep(1.5)

                # 2. Now call the API
                api_resp = self._raw_get(api_url, api_referer)
                if api_resp is None:
                    time.sleep(2)
                    continue
                code = api_resp.status_code
                if code == 200:
                    data = self._safe_json(api_resp, api_url)
                    if data is not None:
                        return data
                    # Got 200 but HTML body — session stale, rebuild
                    logger.warning("200 but HTML body — rebuilding session")
                    self._sc        = self._build()
                    self._last_init = time.time()
                elif code in (401, 403, 429):
                    logger.warning(f"HTTP {code} on option-chain attempt {attempt+1} — rebuilding")
                    self._sc        = self._build()
                    self._last_init = time.time()
                    time.sleep(3)
                else:
                    logger.warning(f"HTTP {code} on option-chain: {api_url}")
                    return None

            logger.error(f"Option chain failed after {retries} attempts: {api_url}")
            return None


# ── Singleton ─────────────────────────────────────────────────────────────────
_nse = NSESession()


# ── Public fetch functions ─────────────────────────────────────────────────────

def fetch_all_fno_oi_change() -> list[dict]:
    """
    Fetch OI + price data for ALL F&O underlyings.
    Endpoint: live-analysis-oi-spurts-underlyings
    CONFIRMED WORKING from Singapore.
    """
    data = _nse.get(
        f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings",
        referer="https://www.nseindia.com/market-data/equity-derivatives-watch",
    )
    if not data:
        return []
    rows = data.get("data", [])
    logger.info(f"OI spurts: {len(rows)} F&O rows")
    return rows


def fetch_option_chain_index(symbol: str) -> dict | None:
    """
    Option chain for NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY.
    Visits the generic option-chain page first to refresh cookies.
    """
    return _nse.get_seeded(
        seed_url     = f"{NSE_BASE}/option-chain",
        seed_referer = "https://www.nseindia.com/market-data/equity-derivatives-watch",
        api_url      = f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}",
        api_referer  = f"{NSE_BASE}/option-chain",
    )


def fetch_option_chain_equity(symbol: str) -> dict | None:
    """
    Option chain for individual F&O stocks (e.g. RELIANCE, TCS).
    Visits the derivatives quote page for that symbol first,
    then calls the option-chain API.
    """
    return _nse.get_seeded(
        seed_url     = f"{NSE_BASE}/get-quotes/derivatives?symbol={symbol}",
        seed_referer = f"{NSE_BASE}/market-data/equity-derivatives-watch",
        api_url      = f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}",
        api_referer  = f"{NSE_BASE}/option-chain",
    )


def fetch_quote_derivative(symbol: str) -> dict | None:
    """Futures quote for a specific symbol — price, OI, expiry."""
    return _nse.get_seeded(
        seed_url     = f"{NSE_BASE}/get-quotes/derivatives?symbol={symbol}",
        seed_referer = f"{NSE_BASE}/market-data/live-equity-market",
        api_url      = f"{NSE_BASE}/api/quote-derivative?symbol={symbol}",
        api_referer  = f"{NSE_BASE}/get-quotes/derivatives?symbol={symbol}",
    )


def test_nse_connectivity() -> dict:
    """Diagnostic: test all key NSE endpoints. Call via /api/debug."""
    out = {}
    tests = [
        ("oi_spurts",     fetch_all_fno_oi_change),
        ("chain_nifty",   lambda: fetch_option_chain_index("NIFTY")),
        ("chain_equity",  lambda: fetch_option_chain_equity("RELIANCE")),
        ("quote_deriv",   lambda: fetch_quote_derivative("RELIANCE")),
    ]
    for name, fn in tests:
        try:
            data = fn()
            if data is None:
                out[name] = "blocked/empty"
            elif isinstance(data, list):
                out[name] = f"ok ({len(data)} rows)"
            else:
                keys = list(data.keys())[:4]
                out[name] = f"ok — keys: {keys}"
        except Exception as e:
            out[name] = f"error: {e}"
    return out
