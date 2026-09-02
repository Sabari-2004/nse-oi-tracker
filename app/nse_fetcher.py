# nse_fetcher.py — Production NSE fetcher using curl-cffi Chrome impersonation
#
# WHY curl-cffi?
# NSE uses Akamai Bot Manager (+ Cloudflare) for bot protection.
# Both check the TLS fingerprint (JA3 hash) at the TCP level — before
# any cookies or JS challenge. Standard requests/urllib3/cloudscraper
# produce a non-browser JA3 hash that Akamai instantly flags as a bot.
#
# curl-cffi uses Chrome's own BoringSSL library to produce an IDENTICAL
# TLS fingerprint to a real Chrome browser. Akamai cannot distinguish
# our requests from a real user. This fixes option chain 403/HTML blocks.
#
# Reference: https://github.com/yifeikong/curl-cffi

import time
import logging
from threading import RLock
from urllib.parse import quote
from curl_cffi import requests as cffi_requests
from app.config import SESSION_REFRESH_SECONDS

logger   = logging.getLogger(__name__)
NSE_BASE = "https://www.nseindia.com"

# Chrome impersonation target (curl-cffi supports many versions)
CHROME = "chrome120"

# Base headers for all requests (realistic Chrome headers)
BASE_HEADERS = {
    "Accept-Language":           "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br",
    "Cache-Control":             "no-cache",
    "Pragma":                    "no-cache",
    "Sec-Ch-Ua":                 '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile":         "?0",
    "Sec-Ch-Ua-Platform":       '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "User-Agent":                ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36"),
}

# JSON API-specific headers (added on top of BASE_HEADERS for API calls)
API_EXTRA = {
    "Accept":           "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
}

# Page navigation headers (for seed visits — looks like a real browser navigation)
NAV_EXTRA = {
    "Accept":         "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Seed pages visited on session startup
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
    Chrome-impersonating NSE session using curl-cffi.

    curl-cffi produces the exact same TLS fingerprint (JA3 hash) as
    Chrome 120. This bypasses Akamai Bot Manager at the TLS level,
    before any cookie or JavaScript challenge is even considered.

    Session lifecycle:
    - Built on first use (visits 4 seed pages with 2s delays)
    - Auto-refreshed every SESSION_REFRESH_SECONDS (10 min)
    - Re-seeded on 401/403/429 (stale cookies)
    """

    def __init__(self):
        self._sess      = None
        self._lock      = RLock()
        self._last_init = 0.0

    def _new_session(self) -> cffi_requests.Session:
        """Create a fresh curl-cffi session impersonating Chrome."""
        s = cffi_requests.Session(impersonate=CHROME)
        s.headers.update(BASE_HEADERS)
        return s

    def _build(self):
        """Seed a new session by visiting NSE pages in browser-like order."""
        logger.info("Building NSE Chrome-impersonation session…")
        sess = self._new_session()

        for url, referer in SEED_PAGES:
            sess.headers.update({**NAV_EXTRA, "Referer": referer})
            try:
                r = sess.get(url, timeout=20)
                logger.info(f"  seed {r.status_code} {url}")
            except Exception as e:
                logger.warning(f"  seed failed {url}: {e}")
            time.sleep(2.2)

        # Reset to base headers after seeding
        sess.headers.update(BASE_HEADERS)
        logger.info("NSE session ready (Chrome TLS fingerprint).")
        return sess

    def _ensure(self):
        """Rebuild session if missing or older than SESSION_REFRESH_SECONDS."""
        now = time.time()
        if self._sess is None or (now - self._last_init) > SESSION_REFRESH_SECONDS:
            self._sess      = self._build()
            self._last_init = time.time()

    def _safe_json(self, resp, label: str):
        """Parse JSON from response. Returns None if body is empty or HTML."""
        if resp is None or not resp.content:
            logger.warning(f"Empty response: {label}")
            return None
        ct = resp.headers.get("content-type", "")
        if "html" in ct.lower():
            snip = resp.text[:150].replace("\n", " ")
            logger.warning(f"HTML body from {label}: {snip!r}")
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.warning(f"JSON parse error {label}: {e}")
            return None

    def _api_get(self, url: str, referer: str):
        """Raw API GET with JSON headers. Must hold lock."""
        self._sess.headers.update({**BASE_HEADERS, **API_EXTRA, "Referer": referer})
        try:
            return self._sess.get(url, timeout=25)
        except Exception as e:
            logger.error(f"Request error {url}: {e}")
            return None

    def _nav_get(self, url: str, referer: str):
        """Raw navigation GET (page visit). Must hold lock."""
        self._sess.headers.update({**BASE_HEADERS, **NAV_EXTRA, "Referer": referer})
        try:
            return self._sess.get(url, timeout=20)
        except Exception as e:
            logger.warning(f"Nav error {url}: {e}")
            return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, url: str, referer: str, retries: int = 2) -> dict | None:
        """Standard JSON API fetch with auto-retry on 4xx."""
        with self._lock:
            self._ensure()
            for attempt in range(retries):
                resp = self._api_get(url, referer)
                if resp is None:
                    time.sleep(2)
                    continue
                code = resp.status_code
                if code == 200:
                    return self._safe_json(resp, url)
                if code in (401, 403, 429):
                    logger.warning(f"HTTP {code} attempt {attempt+1} — rebuilding session: {url}")
                    self._sess      = self._build()
                    self._last_init = time.time()
                    time.sleep(3 * (attempt + 1))
                elif code == 404:
                    logger.warning(f"HTTP 404 (endpoint removed): {url}")
                    return None
                else:
                    logger.warning(f"HTTP {code}: {url}")
                    return None
            return None

    def get_seeded(self, seed_url: str, seed_referer: str,
                   api_url: str, api_referer: str,
                   retries: int = 3) -> dict | None:
        """
        Visit seed_url first (sets fresh per-symbol cookies), then call api_url.
        Both in one lock acquisition — safe because RLock is re-entrant.

        Critical for option chain: NSE checks that the exact symbol page was
        visited right before the option chain API call.
        """
        with self._lock:
            self._ensure()
            for attempt in range(retries):
                # Step 1: Visit the seed page (browser navigation)
                logger.info(f"  seeding for option chain: {seed_url}")
                seed_resp = self._nav_get(seed_url, seed_referer)
                sc = seed_resp.status_code if seed_resp else "failed"
                logger.info(f"  seed status: {sc}")
                time.sleep(1.5)

                # Step 2: Call the JSON API
                api_resp = self._api_get(api_url, api_referer)
                if api_resp is None:
                    time.sleep(2)
                    continue

                code = api_resp.status_code
                if code == 200:
                    data = self._safe_json(api_resp, api_url)
                    if data is not None:
                        return data
                    # 200 but HTML — session stale, rebuild
                    logger.warning("200 but HTML body — rebuilding session")
                    self._sess      = self._build()
                    self._last_init = time.time()
                    time.sleep(3)
                elif code in (401, 403, 429):
                    logger.warning(f"HTTP {code} option-chain attempt {attempt+1} — rebuilding")
                    self._sess      = self._build()
                    self._last_init = time.time()
                    time.sleep(4 * (attempt + 1))
                elif code == 404:
                    logger.warning(f"HTTP 404 option-chain: {api_url}")
                    return None
                else:
                    logger.warning(f"HTTP {code} option-chain: {api_url}")
                    return None

            logger.error(f"Option chain failed after {retries} attempts: {api_url}")
            return None


# ── Singleton ──────────────────────────────────────────────────────────────────
_nse = NSESession()


# ── Public data functions ──────────────────────────────────────────────────────

def fetch_all_fno_oi_change() -> list[dict]:
    """
    Fetch OI + price data for ALL F&O underlyings.
    CONFIRMED WORKING from Singapore.
    """
    data = _nse.get(
        f"{NSE_BASE}/api/live-analysis-oi-spurts-underlyings",
        referer="https://www.nseindia.com/market-data/equity-derivatives-watch",
    )
    if not data:
        return []
    rows = data.get("data", [])
    logger.info(f"OI spurts: {len(rows)} F&O rows received")
    return rows


def _fetch_option_chain(symbol: str, market_type: str) -> dict | None:
    """Fetch an option chain using NSE's current v3 API and nearest expiry."""
    symbol = symbol.upper().strip()
    encoded_symbol = quote(symbol, safe="")
    contract_info = _nse.get(
        f"{NSE_BASE}/api/option-chain-contract-info?symbol={encoded_symbol}",
        referer="https://www.nseindia.com/option-chain",
    )
    expiry_dates = (contract_info or {}).get("expiryDates", [])
    if not expiry_dates:
        logger.warning("No expiry dates returned for option chain %s", symbol)
        return None
    expiry = quote(str(expiry_dates[0]), safe="")
    api_url = (
        f"{NSE_BASE}/api/option-chain-v3?type={market_type}"
        f"&symbol={encoded_symbol}&expiry={expiry}"
    )
    seed_url = (
        f"{NSE_BASE}/get-quotes/derivatives?symbol={encoded_symbol}"
        if market_type == "Equity" else f"{NSE_BASE}/option-chain"
    )
    return _nse.get_seeded(
        seed_url=seed_url,
        seed_referer="https://www.nseindia.com/option-chain",
        api_url=api_url,
        api_referer="https://www.nseindia.com/option-chain",
    )


def fetch_option_chain_index(symbol: str) -> dict | None:
    """Option chain for NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY."""
    return _fetch_option_chain(symbol, "Indices")


def fetch_option_chain_equity(symbol: str) -> dict | None:
    """Option chain for individual F&O stocks (RELIANCE, TCS etc.)."""
    return _fetch_option_chain(symbol, "Equity")


def fetch_quote_derivative(symbol: str) -> dict | None:
    """Futures quote for a specific symbol — live price, OI, expiry."""
    return _nse.get_seeded(
        seed_url     = f"{NSE_BASE}/get-quotes/derivatives?symbol={symbol}",
        seed_referer = "https://www.nseindia.com/market-data/live-equity-market",
        api_url      = f"{NSE_BASE}/api/quote-derivative?symbol={symbol}",
        api_referer  = f"https://www.nseindia.com/get-quotes/derivatives?symbol={symbol}",
    )


def test_nse_connectivity() -> dict:
    """Diagnostic — test all key endpoints. Accessible via /api/debug."""
    results = {}
    tests = [
        ("oi_spurts",    lambda: fetch_all_fno_oi_change()),
        ("chain_nifty",  lambda: fetch_option_chain_index("NIFTY")),
        ("chain_equity", lambda: fetch_option_chain_equity("RELIANCE")),
        ("quote_deriv",  lambda: fetch_quote_derivative("RELIANCE")),
    ]
    for name, fn in tests:
        try:
            data = fn()
            if data is None:
                results[name] = "blocked/empty"
            elif isinstance(data, list):
                results[name] = f"ok ({len(data)} rows)"
            else:
                keys = list(data.keys())[:5]
                results[name] = f"ok — keys: {keys}"
        except Exception as e:
            results[name] = f"error: {e}"
    return results
