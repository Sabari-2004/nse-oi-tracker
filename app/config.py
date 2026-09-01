# config.py — Signal thresholds, confidence config, constants

# ─── NSE Indices (always checked) ────────────────────────────────────────────
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# ─── Signal classification thresholds ────────────────────────────────────────
# Minimum % price move to be "rising" or "falling" (higher = less noise)
PRICE_CHANGE_THRESHOLD = 0.40    # 0.40% — filters out micro-moves

# Minimum % OI change to be significant
OI_CHANGE_THRESHOLD    = 3.0     # 3.0% — filters out noise OI fluctuations

# ─── Confidence tiers (multi-factor score 0–100) ─────────────────────────────
# Score computed from: price strength + OI strength + absolute OI (liquidity)
CONFIDENCE_HIGH   = 65   # ⭐⭐⭐  — only these are shown by default
CONFIDENCE_MEDIUM = 40   # ⭐⭐    — shown if user relaxes filter
# Below MEDIUM → filtered out completely

# ─── Liquidity filter ─────────────────────────────────────────────────────────
# Minimum absolute OI in contracts (removes penny/illiquid F&O stocks)
MIN_OI_ABSOLUTE = 10_000

# ─── Strength score (legacy, still computed) ─────────────────────────────────
MIN_STRENGTH_SCORE = 5.0

# ─── Cache & polling ──────────────────────────────────────────────────────────
CACHE_TTL_SECONDS    = 60
POLL_INTERVAL_SECONDS = 60
SESSION_REFRESH_SECONDS = 600   # 10 min NSE cookie refresh

# ─── Option chain display ─────────────────────────────────────────────────────
STRIKES_EACH_SIDE = 10

# ─── Market hours IST ────────────────────────────────────────────────────────
MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MIN    = 15
MARKET_CLOSE_HOUR  = 15
MARKET_CLOSE_MIN   = 30
