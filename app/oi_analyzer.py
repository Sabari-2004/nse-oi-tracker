# oi_analyzer.py — High-confidence OI signal engine
# Uses NSE pre-computed buildup endpoints as primary source

from __future__ import annotations
import logging
from app.config import (
    PRICE_CHANGE_THRESHOLD, OI_CHANGE_THRESHOLD,
    MIN_OI_ABSOLUTE, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
    STRIKES_EACH_SIDE, INDICES,
)
from app.nse_fetcher import (
    fetch_all_buildup_categories,
    fetch_option_chain_index,
    fetch_option_chain_equity,
    fetch_quote_derivative,
)

logger = logging.getLogger(__name__)

# ── Signal constants ───────────────────────────────────────────────────────────
SIGNAL_LONG_BUILDUP   = "LONG_BUILDUP"
SIGNAL_SHORT_BUILDUP  = "SHORT_BUILDUP"
SIGNAL_SHORT_COVERING = "SHORT_COVERING"
SIGNAL_LONG_UNWINDING = "LONG_UNWINDING"
SIGNAL_NEUTRAL        = "NEUTRAL"

SIGNAL_META = {
    SIGNAL_LONG_BUILDUP:   {"label": "Long Buildup",   "emoji": "🟢", "color": "green",  "bias": "Bullish",      "direction": "BUY"},
    SIGNAL_SHORT_BUILDUP:  {"label": "Short Buildup",  "emoji": "🔴", "color": "red",    "bias": "Bearish",      "direction": "SELL"},
    SIGNAL_SHORT_COVERING: {"label": "Short Covering", "emoji": "🟡", "color": "yellow", "bias": "Bullish Fade", "direction": "BUY"},
    SIGNAL_LONG_UNWINDING: {"label": "Long Unwinding", "emoji": "🟠", "color": "orange", "bias": "Bearish Fade", "direction": "SELL"},
    SIGNAL_NEUTRAL:        {"label": "Neutral",        "emoji": "⚪", "color": "gray",   "bias": "Sideways",     "direction": "NONE"},
}

# NSE category → signal type mapping
CATEGORY_TO_SIGNAL = {
    "long_buildup":   SIGNAL_LONG_BUILDUP,
    "short_buildup":  SIGNAL_SHORT_BUILDUP,
    "short_covering": SIGNAL_SHORT_COVERING,
    "long_unwinding": SIGNAL_LONG_UNWINDING,
}

# ── High-confidence scoring ────────────────────────────────────────────────────

def confidence_score(price_chg_p: float, oi_chg_p: float, oi_abs: float) -> int:
    """
    Composite confidence score 0–100.
    Only HIGH (≥65) signals are shown by default.

    Three components:
      1. Price momentum   (0–35 pts) — how strongly price moved
      2. OI conviction    (0–45 pts) — how strongly OI moved
      3. Liquidity        (0–20 pts) — absolute OI (filters illiquid stocks)
    """
    score = 0

    # 1. Price strength
    p = abs(price_chg_p)
    if   p >= 3.0:  score += 35
    elif p >= 2.0:  score += 28
    elif p >= 1.0:  score += 20
    elif p >= 0.5:  score += 12
    elif p >= 0.4:  score += 6

    # 2. OI conviction
    o = abs(oi_chg_p)
    if   o >= 20:   score += 45
    elif o >= 15:   score += 38
    elif o >= 10:   score += 30
    elif o >= 7:    score += 22
    elif o >= 5:    score += 15
    elif o >= 3:    score += 8

    # 3. Liquidity (absolute OI)
    if   oi_abs >= 1_000_000: score += 20
    elif oi_abs >= 500_000:   score += 16
    elif oi_abs >= 100_000:   score += 12
    elif oi_abs >= 50_000:    score += 8
    elif oi_abs >= 10_000:    score += 4

    return min(score, 100)


def confidence_tier(score: int) -> str:
    if score >= CONFIDENCE_HIGH:    return "HIGH"
    if score >= CONFIDENCE_MEDIUM:  return "MEDIUM"
    return "LOW"


def signal_strength(price_chg_p: float, oi_chg_p: float) -> float:
    """Legacy strength score 0–100 (used for sorting within same tier)."""
    p = min(abs(price_chg_p) / 2.0, 50)
    o = min(abs(oi_chg_p)    / 5.0, 50)
    return round(p + o, 1)


# ── Safe field extraction ──────────────────────────────────────────────────────

def _f(val) -> float:
    """Safe float cast — returns 0.0 on None/empty/error."""
    if val is None or val == "" or val == "-":
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _symbol(row: dict) -> str:
    """Extract symbol from NSE row — tries multiple known field names."""
    for key in ("underlying", "symbol", "UNDERLYING", "SYMBOL", "meta"):
        v = row.get(key)
        if v and isinstance(v, str):
            return v.strip().upper()
    return ""


def _build_signal_row(sym: str, ltp: float, price_chg: float,
                       price_chg_p: float, oi: float,
                       oi_chg: float, oi_chg_p: float,
                       signal: str) -> dict:
    meta   = SIGNAL_META[signal]
    conf   = confidence_score(price_chg_p, oi_chg_p, oi)
    tier   = confidence_tier(conf)
    streng = signal_strength(price_chg_p, oi_chg_p)
    return {
        "symbol":           sym,
        "ltp":              round(ltp, 2),
        "price_change":     round(price_chg, 2),
        "price_change_pct": round(price_chg_p, 2),
        "oi":               int(oi),
        "oi_change":        int(oi_chg),
        "oi_change_pct":    round(oi_chg_p, 2),
        "signal":           signal,
        "signal_label":     meta["label"],
        "signal_emoji":     meta["emoji"],
        "signal_color":     meta["color"],
        "signal_bias":      meta["bias"],
        "signal_direction": meta["direction"],
        "strength":         streng,
        "confidence":       conf,
        "confidence_tier":  tier,
    }


# ── NSE row parsers (field names differ across endpoints) ─────────────────────

def _parse_buildup_row(row: dict, signal: str) -> dict | None:
    """
    Parse one row from NSE's pre-computed buildup endpoints.
    NSE buildup rows have varied field names — we try all known variants.
    """
    sym = _symbol(row)
    if not sym:
        return None

    ltp         = _f(row.get("ltp") or row.get("lastPrice") or row.get("LTP"))
    price_chg   = _f(row.get("change") or row.get("priceChange") or 0)
    price_chg_p = _f(row.get("pChange") or row.get("perChange") or
                     row.get("percChange") or row.get("per_change") or 0)
    oi          = _f(row.get("oi") or row.get("openInterest") or
                     row.get("OI") or row.get("openint") or 0)
    oi_chg      = _f(row.get("oiChange") or row.get("changeinOpenInterest") or
                     row.get("COI") or row.get("oi_change") or 0)
    oi_chg_p    = _f(row.get("oiChangePct") or row.get("perOIchange") or
                     row.get("poichange") or row.get("oi_pct_change") or 0)

    # Skip if LTP is zero (bad/missing data)
    if ltp == 0:
        return None

    # Skip illiquid stocks
    if 0 < oi < MIN_OI_ABSOLUTE:
        return None

    # Skip if price change is below threshold (even though NSE classified it)
    if abs(price_chg_p) < PRICE_CHANGE_THRESHOLD:
        return None

    # Skip if OI change is below threshold
    if abs(oi_chg_p) < OI_CHANGE_THRESHOLD:
        return None

    result = _build_signal_row(sym, ltp, price_chg, price_chg_p,
                                oi, oi_chg, oi_chg_p, signal)

    # Only return HIGH/MEDIUM confidence
    if result["confidence_tier"] == "LOW":
        return None

    return result


# ── PRIMARY SCANNER ────────────────────────────────────────────────────────────

def scan_all_fno_realtime() -> list[dict]:
    """
    Scan all F&O stocks using NSE's pre-computed buildup categories.

    Why this approach?
    - NSE already classifies every F&O stock every ~3 min during market hours
    - We read 4 endpoints (one per signal type) — all pre-computed by NSE
    - No manual merging/computing required — NSE's logic is authoritative
    - These endpoints are the actual data powering NSE's own analytics pages
    - Returns only HIGH + MEDIUM confidence signals (filters noise)
    """
    all_results:  list[dict] = []
    seen_symbols: set[str]   = set()        # dedup across categories

    raw_categories = fetch_all_buildup_categories()   # dict[cat, list[row]]

    for cat, rows in raw_categories.items():
        signal = CATEGORY_TO_SIGNAL.get(cat)
        if not signal:
            continue

        for row in rows:
            result = _parse_buildup_row(row, signal)
            if result is None:
                continue
            if result["symbol"] in seen_symbols:
                continue
            seen_symbols.add(result["symbol"])
            all_results.append(result)

    # Sort: HIGH confidence first, then by strength descending
    all_results.sort(
        key=lambda r: (
            0 if r["confidence_tier"] == "HIGH" else 1,   # HIGH before MEDIUM
            -r["confidence"],                               # higher conf first
            -r["strength"],                                 # then strength
        )
    )

    logger.info(
        f"Dynamic scan complete: {len(all_results)} signals "
        f"({sum(1 for r in all_results if r['confidence_tier']=='HIGH')} HIGH, "
        f"{sum(1 for r in all_results if r['confidence_tier']=='MEDIUM')} MEDIUM)"
    )
    return all_results


# ── OPTION CHAIN ──────────────────────────────────────────────────────────────

def get_option_chain_analysis(symbol: str) -> dict:
    is_index = symbol in INDICES
    raw = fetch_option_chain_index(symbol) if is_index else fetch_option_chain_equity(symbol)
    if not raw:
        return {"symbol": symbol, "error": "NSE returned no data (market closed or IP blocked)"}
    return _parse_option_chain(raw, symbol)


def _parse_option_chain(data: dict, symbol: str) -> dict:
    try:
        records     = data.get("records", {}) or {}
        filtered    = data.get("filtered", {}) or {}
        exp_dates   = records.get("expiryDates", [])
        atm_strike  = _f(records.get("underlyingValue", 0))
        nearest_exp = exp_dates[0] if exp_dates else None
        all_data    = filtered.get("data", [])

        total_ce_oi = total_pe_oi = 0
        strikes: list[dict] = []
        pain_map: dict[float, float] = {}

        for row in all_data:
            strike  = _f(row.get("strikePrice", 0))
            ce      = row.get("CE") or {}
            pe      = row.get("PE") or {}
            ce_oi   = _f(ce.get("openInterest",       0))
            pe_oi   = _f(pe.get("openInterest",       0))
            ce_doi  = _f(ce.get("changeinOpenInterest", 0))
            pe_doi  = _f(pe.get("changeinOpenInterest", 0))
            ce_ltp  = _f(ce.get("lastPrice",           0))
            pe_ltp  = _f(pe.get("lastPrice",           0))

            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            pain_map[strike] = (pain_map.get(strike, 0)
                                + ce_oi * ce_ltp + pe_oi * pe_ltp)
            strikes.append({
                "strike": strike,
                "ce_oi": int(ce_oi), "ce_doi": int(ce_doi), "ce_ltp": ce_ltp,
                "pe_oi": int(pe_oi), "pe_doi": int(pe_doi), "pe_ltp": pe_ltp,
            })

        pcr       = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
        max_pain  = min(pain_map, key=pain_map.get) if pain_map else 0

        # Filter to ATM ± STRIKES_EACH_SIDE
        atm_list  = sorted({s["strike"] for s in strikes})
        atm_idx   = (min(range(len(atm_list)),
                         key=lambda i: abs(atm_list[i] - atm_strike))
                     if atm_list else 0)
        lo        = max(0, atm_idx - STRIKES_EACH_SIDE)
        hi        = min(len(atm_list) - 1, atm_idx + STRIKES_EACH_SIDE)
        relevant  = {atm_list[i] for i in range(lo, hi + 1)}
        strikes_f = [s for s in strikes if s["strike"] in relevant]

        return {
            "symbol": symbol, "atm_strike": atm_strike,
            "expiry": nearest_exp, "pcr": pcr,
            "total_ce_oi": int(total_ce_oi),
            "total_pe_oi": int(total_pe_oi),
            "max_pain": max_pain, "strikes": strikes_f,
        }
    except Exception as exc:
        logger.error(f"Error parsing option chain for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}
