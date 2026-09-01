# nse_fetcher.py  — NSE data fetcher using cloudscraper
#
# WHY cloudscraper?
#   NSE uses Cloudflare protection. Plain requests.Session() gets blocked from
#   cloud server IPs (Render is in the US). cloudscraper:
#     • Solves Cloudflare's JavaScript challenge automatically
#     • Mimics real browser TLS fingerprint (Chrome/Firefox)
#     • Handles cookie jar exactly like a browser
#     • Works from cloud servers (proven on AWS/GCP/Render)
#
# HOW it works vs Chrome:
#   Chrome:        Indian IP + real browser JS → Cloudflare passes ✅
#   requests:      US cloud IP + no JS        → Cloudflare blocks ❌
#   cloudscraper:  US cloud IP + fake browser JS → Cloudflare passes ✅

import time
import logging
import cloudscraper                        # pip install cloudscraper
from threading import Lock
from app.config import SESSION_REFRESH_SECONDS

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"

# Exact headers a real Chrome 124 browser sends to NSE
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

# Pages visited in order — seeds the session with valid NSE Cloudflare cookies
SEED_URLS = [
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/live-equity-market",
    "https://www.nseindia.com/market-data/equity-derivatives-watch",
    "https://www.nseindia.com/option-chain",
]

# ── NSE Live-analysis endpoints (pre-computed by NSE every ~3 min) ─────────────
# These are the SAME endpoints that power NSE's own F&O analytics pages.
# Primary source — most reliable for OI signal data.
BUILDUP_ENDPOINTS = {
    "long_buildup":   "/api/live-analysis-oi-change-with-price-gainers",
    "short_buildup":  "/api/live-analysis-oi-change-with-price-losers",
    "short_covering": "/api/live-analysis-price-change-with-oi-reducers",
    "long_unwinding": "/api/live-analysis-oi-reducers-with-price-reducers",
}

# OI spurt endpoint — stocks with biggest OI jump today
OI_SPURT_URL = "/api/live-analysis-oi-spurts-underlyings"


class NSECloudSession:
    """
    Cloudflare-bypassing NSE session using cloudscraper.

    cloudscraper creates a session that:
    1. Presents a real browser TLS fingerprint (Chrome/Firefox)
    2. Executes Cloudflare's JavaScript challenge (via embedded JS engine)
    3. Maintains cookies across requests exactly like a browser
    4. Auto-rotates if challenge changes

    Result: NSE sees it as a legitimate browser — same as Chrome.
    """

    def __init__(self):
        self._scraper = None
        self._lock    = Lock()
        self._last_refresh: float = 0.0

    def _build_scraper(self) -> cloudscraper.CloudScraper:
        """Create a fresh cloudscraper session seeded with NSE cookies."""
        logger.info("Building new cloudscraper session (browser=chrome, platform=windows)…")

        # browser='chrome' + platform='windows' mimics Chrome on Windows exactly
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        scraper.headers.update(HEADERS)

        # Seed: visit NSE pages in order to collect Cloudflare clearance cookies
        for url in SEED_URLS:
            try:
                r = scraper.get(url, timeout=20)
                logger.info(f"  seed {url} → HTTP {r.status_code}")
            except Exception as exc:
                logger.warning(f"  seed failed {url}: {exc}")
            time.sleep(2.0)          # human-like delay between page loads

        logger.info("NSE cloudscraper session ready.")
        return scraper

    def _ensure_session(self):
        now = time.time()
        if self._scraper is None or (now - self._last_refresh) > SESSION_REFRESH_SECONDS:
            self._scraper      = self._build_scraper()
            self._last_refresh = time.time()

    def _safe_json(self, resp, url: str) -> dict | None:
        """Parse response safely — handles empty body / HTML / non-JSON."""
        if not resp.content:
            logger.warning(f"Empty body: {url}")
            return None

        ct = resp.headers.get("Content-Type", "")
        if "html" in ct.lower():
            preview = resp.text[:200].replace("\n", " ")
            logger.warning(f"HTML response (still blocked?): {url} | {preview!r}")
            return None

        try:
            return resp.json()
        except ValueError as exc:
            preview = resp.text[:120].replace("\n", " ")
            logger.warning(f"Non-JSON ({exc}): {url} | {preview!r}")
            return None

    def get(self, url: str, retries: int = 3) -> dict | None:
        """Fetch JSON from NSE with Cloudflare bypass + retry logic."""
        with self._lock:
            self._ensure_session()

            for attempt in range(retries):
                try:
                    resp = self._scraper.get(url, timeout=20)

                    if resp.status_code == 200:
                        data = self._safe_json(resp, url)
                        if data is not None:
                            return data
                        # Empty/HTML body → re-seed and retry
                        logger.warning(f"Re-seeding after bad body (attempt {attempt+1})…")
                        self._scraper      = self._build_scraper()
                        self._last_refresh = time.time()

                    elif resp.status_code in (401, 403, 429):
                        logger.warning(
                            f"HTTP {resp.status_code} (attempt {attempt+1}/{retries}) "
                            f"— re-seeding session: {url}"
                        )
                        self._scraper      = self._build_scraper()
                        self._last_refresh = time.time()
                        time.sleep(4 * (attempt + 1))   # exponential back-off

                    elif resp.status_code == 404:
                        logger.warning(f"HTTP 404 (endpoint changed?): {url}")
                        return None   # no point retrying 404

                    else:
                        logger.warning(f"HTTP {resp.status_code}: {url}")
                        return None

                except Exception as exc:
                    logger.error(f"Request error (attempt {attempt+1}): {url} — {exc}")
                    time.sleep(2)

            logger.error(f"All {retries} retries failed: {url}")
            return None

    def get_first_working(self, urls: list[str]) -> dict | None:
        """Try URLs in order, return first successful response."""
        for url in urls:
            result = self.get(url)
            if result:
                return result
        return None


# ── Singleton session ──────────────────────────────────────────────────────────
_nse = NSECloudSession()


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API — 100% real-time NSE data, zero hardcoded values
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_buildup_category(category: str) -> list[dict]:
    """
    Fetch NSE's pre-computed buildup list for one signal category.
    NSE classifies every F&O stock — we just read the result.
    Returns [] on failure (market closed / temporarily blocked).
    """
    endpoint = BUILDUP_ENDPOINTS.get(category)
    if not endpoint:
        logger.error(f"Unknown category: {category}")
        return []

    data = _nse.get(NSE_BASE + endpoint)
    if not data:
        return []

    rows = data.get("data", [])
    logger.info(f"NSE [{category}]: {len(rows)} stocks received")
    return rows


def fetch_all_buildup_categories() -> dict[str, list[dict]]:
    """
    Fetch all 4 buildup categories from NSE.
    Returns: { 'long_buildup': [...], 'short_buildup': [...], ... }
    """
    return {cat: fetch_buildup_category(cat) for cat in BUILDUP_ENDPOINTS}


def fetch_oi_spurts() -> list[dict]:
    """Stocks with biggest OI change today — prime scalping candidates."""
    data = _nse.get(NSE_BASE + OI_SPURT_URL)
    return data.get("data", []) if data else []


def fetch_option_chain_index(symbol: str) -> dict | None:
    return _nse.get(f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}")


def fetch_option_chain_equity(symbol: str) -> dict | None:
    return _nse.get(f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}")


def fetch_quote_derivative(symbol: str) -> dict | None:
    return _nse.get(f"{NSE_BASE}/api/quote-derivative?symbol={symbol}")
