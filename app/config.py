# config.py — Signal thresholds & constants (no fixed watchlist)

# ─── NSE Indices to always include in scan ───────────────────────────────────
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# ─── Signal classification thresholds ────────────────────────────────────────
# Minimum % price move to be considered "rising" or "falling"
PRICE_CHANGE_THRESHOLD = 0.10   # 0.10%

# Minimum % OI change to be significant (filters noise)
OI_CHANGE_THRESHOLD = 1.0       # 1.0%

# Minimum absolute OI (in contracts) — filters illiquid/penny contracts
MIN_OI_ABSOLUTE = 500

# Minimum strength score to show in results (0–100)
MIN_STRENGTH_SCORE = 5.0

# ─── Cache TTL ────────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = 60          # refresh every 60 s

# ─── NSE session auto-refresh ─────────────────────────────────────────────────
SESSION_REFRESH_SECONDS = 600   # 10 minutes

# ─── Option chain: ATM ± strikes to display ──────────────────────────────────
STRIKES_EACH_SIDE = 10

# ─── Market hours (IST) ───────────────────────────────────────────────────────
MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MIN  = 30

# ─── Poll interval for background poller ─────────────────────────────────────
POLL_INTERVAL_SECONDS = 60
