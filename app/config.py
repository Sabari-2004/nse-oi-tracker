# config.py — Signal thresholds, confidence config, constants



# ─── NSE Indices (always checked) ────────────────────────────────────────────

INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]



# ─── Signal classification thresholds ────────────────────────────────────────

# Minimum % price move to be "rising" or "falling"

PRICE_CHANGE_THRESHOLD = 0.25    # 0.25% — filters ordinary tick noise



# Minimum % OI change to be significant

OI_CHANGE_THRESHOLD    = 1.00    # 1.00% — filters mechanical OI churn



# ─── Confidence tiers (multi-factor score 0–100) ─────────────────────────────

# Display both tiers. A 60-point floor was too restrictive for ordinary
# intraday moves: rows could satisfy the signal-direction thresholds yet still
# be discarded because the score awarded no points for modest price/OI moves.

CONFIDENCE_HIGH   = 75   # ⭐⭐⭐⭐ — very high confidence
CONFIDENCE_MEDIUM = 55   # ⭐⭐ — requires stronger independent evidence

# Below MEDIUM → filtered out



# ─── Liquidity filter ─────────────────────────────────────────────────────────

# Minimum absolute OI in contracts (removes penny/illiquid F&O stocks)

MIN_OI_ABSOLUTE = 50_000



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
