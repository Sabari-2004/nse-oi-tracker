# oi_analyzer.py — Signal engine using live-analysis-oi-spurts-underlyings
#
# Data source: single NSE endpoint that returns ALL F&O stocks with:
#   - OI change %  (direction tells us: OI up = buildup, OI down = unwinding/covering)
#   - Price change % (direction tells us: price up = bulls, price down = bears)
#
# Classification matrix:
#   Price ↑ + OI ↑ → LONG_BUILDUP   (BUY  — strong)
#   Price ↓ + OI ↑ → SHORT_BUILDUP  (SELL — strong)
#   Price ↑ + OI ↓ → SHORT_COVERING (BUY  — weak, quick)
#   Price ↓ + OI ↓ → LONG_UNWINDING (SELL — weak, quick)

from __future__ import annotations
import logging
from app.config import (
    PRICE_CHANGE_THRESHOLD, OI_CHANGE_THRESHOLD,
    MIN_OI_ABSOLUTE, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
    STRIKES_EACH_SIDE, INDICES,
)
from app.nse_fetcher import (
    fetch_all_fno_oi_change,
    fetch_option_chain_index,
    fetch_option_chain_equity,
    fetch_quote_derivative,
)

logger = logging.getLogger(__name__)

# ── Signal constants ──────────────────────────────────────────────────────────
SIGNAL_LONG_BUILDUP   = "LONG_BUILDUP"
SIGNAL_SHORT_BUILDUP  = "SHORT_BUILDUP"
SIGNAL_SHORT_COVERING = "SHORT_COVERING"
SIGNAL_LONG_UNWINDING = "LONG_UNWINDING"
SIGNAL_NEUTRAL        = "NEUTRAL"

SIGNAL_META = {
    SIGNAL_LONG_BUILDUP:   {"label":"Long Buildup",   "emoji":"🟢","color":"green",  "bias":"Bullish",      "direction":"BUY"},
    SIGNAL_SHORT_BUILDUP:  {"label":"Short Buildup",  "emoji":"🔴","color":"red",    "bias":"Bearish",      "direction":"SELL"},
    SIGNAL_SHORT_COVERING: {"label":"Short Covering", "emoji":"🟡","color":"yellow", "bias":"Bullish Fade", "direction":"BUY"},
    SIGNAL_LONG_UNWINDING: {"label":"Long Unwinding", "emoji":"🟠","color":"orange", "bias":"Bearish Fade", "direction":"SELL"},
    SIGNAL_NEUTRAL:        {"label":"Neutral",        "emoji":"⚪","color":"gray",   "bias":"Sideways",     "direction":"NONE"},
}

# Used by main.py /api/category route
CATEGORY_TO_SIGNAL = {
    "long_buildup":   SIGNAL_LONG_BUILDUP,
    "short_buildup":  SIGNAL_SHORT_BUILDUP,
    "short_covering": SIGNAL_SHORT_COVERING,
    "long_unwinding": SIGNAL_LONG_UNWINDING,
}


# ── Signal classifier ─────────────────────────────────────────────────────────

def classify_signal(price_change_pct: float, oi_change_pct: float) -> str:
    price_up = price_change_pct >  PRICE_CHANGE_THRESHOLD
    price_dn = price_change_pct < -PRICE_CHANGE_THRESHOLD
    oi_up    = oi_change_pct    >  OI_CHANGE_THRESHOLD
    oi_dn    = oi_change_pct    < -OI_CHANGE_THRESHOLD
    if price_up and oi_up:  return SIGNAL_LONG_BUILDUP
    if price_dn and oi_up:  return SIGNAL_SHORT_BUILDUP
    if price_up and oi_dn:  return SIGNAL_SHORT_COVERING
    if price_dn and oi_dn:  return SIGNAL_LONG_UNWINDING
    return SIGNAL_NEUTRAL


# ── High-confidence scoring ───────────────────────────────────────────────────

def confidence_score(price_chg_p: float, oi_chg_p: float, oi_abs: float) -> int:
    """
    Composite confidence 0–100.
    Components:
      Price strength  (0–35 pts): how far price moved from flat
      OI conviction   (0–45 pts): how strongly OI changed
      Liquidity       (0–20 pts): absolute OI size (illiquid stocks filtered)

    HIGH   ≥ 65 → shown (⭐⭐⭐)
    MEDIUM 40–64 → shown (⭐⭐)
    LOW    < 40  → hidden
    """
    score = 0
    p = abs(price_chg_p)
    if   p >= 3.0: score += 35
    elif p >= 2.0: score += 28
    elif p >= 1.0: score += 20
    elif p >= 0.5: score += 12
    elif p >= 0.4: score += 6

    o = abs(oi_chg_p)
    if   o >= 20: score += 45
    elif o >= 15: score += 38
    elif o >= 10: score += 30
    elif o >=  7: score += 22
    elif o >=  5: score += 15
    elif o >=  3: score += 8

    if   oi_abs >= 1_000_000: score += 20
    elif oi_abs >= 500_000:   score += 16
    elif oi_abs >= 100_000:   score += 12
    elif oi_abs >= 50_000:    score += 8
    elif oi_abs >= 10_000:    score += 4

    return min(score, 100)


def confidence_tier(score: int) -> str:
    if score >= CONFIDENCE_HIGH:   return "HIGH"
    if score >= CONFIDENCE_MEDIUM: return "MEDIUM"
    return "LOW"


def signal_strength(price_chg_p: float, oi_chg_p: float) -> float:
    return round(min(abs(price_chg_p)/2.0, 50) + min(abs(oi_chg_p)/5.0, 50), 1)


# ── Safe field helpers ────────────────────────────────────────────────────────

def _f(val) -> float:
    """Safe float — returns 0.0 on None/empty/error."""
    if val is None or val == "" or val == "-":
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _symbol(row: dict) -> str:
    """Extract symbol from NSE row — handles all known field name variants."""
    for key in ("underlying", "symbol", "UNDERLYING", "SYMBOL"):
        v = row.get(key)
        if v and isinstance(v, str):
            return v.strip().upper()
    return ""


def _build_signal_row(sym, ltp, price_chg, price_chg_p, oi, oi_chg, oi_chg_p, signal) -> dict:
    meta = SIGNAL_META[signal]
    conf = confidence_score(price_chg_p, oi_chg_p, oi)
    tier = confidence_tier(conf)
    strg = signal_strength(price_chg_p, oi_chg_p)
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
        "strength":         strg,
        "confidence":       conf,
        "confidence_tier":  tier,
    }


def _parse_row(row: dict) -> dict | None:
    """
    Parse one row from live-analysis-oi-spurts-underlyings.
    Handles ALL known NSE field name variants (NSE changes names without notice).
    """
    sym = _symbol(row)
    if not sym:
        return None

    ltp = _f(
        row.get("ltp") or row.get("lastPrice") or row.get("ltP") or
        row.get("LTP") or row.get("price") or 0
    )

    price_chg_p = _f(
        row.get("pChange") or row.get("perChange") or
        row.get("changePer") or row.get("change_p") or
        row.get("perchange") or row.get("pchange") or
        row.get("percentChange") or 0
    )

    price_chg = _f(
        row.get("change") or row.get("priceChange") or
        row.get("netChange") or 0
    )

    oi = _f(
        row.get("oi") or row.get("openInterest") or
        row.get("OI") or row.get("openinterest") or 0
    )

    oi_chg = _f(
        row.get("oiChange") or row.get("changeinOpenInterest") or
        row.get("COI") or row.get("changeInOI") or
        row.get("oichange") or 0
    )

    oi_chg_p = _f(
        row.get("oiChangePct") or row.get("perOIchange") or
        row.get("oiChangePer") or row.get("changeOI_pct") or
        row.get("perOIChange") or row.get("oiChangePercent") or
        row.get("pOIchng") or row.get("oichngper") or 0
    )

    # Derive OI% if we have absolute OI and OI change
    if oi_chg_p == 0 and oi_chg != 0 and (oi - oi_chg) > 0:
        oi_chg_p = (oi_chg / (oi - oi_chg)) * 100

    # Skip rows with no price data
    if ltp == 0:
        return None

    # Skip illiquid stocks (min OI check — but only if OI data exists)
    if oi > 0 and oi < MIN_OI_ABSOLUTE:
        return None

    signal = classify_signal(price_chg_p, oi_chg_p)
    if signal == SIGNAL_NEUTRAL:
        return None

    result = _build_signal_row(sym, ltp, price_chg, price_chg_p, oi, oi_chg, oi_chg_p, signal)

    # Only HIGH/MEDIUM confidence shown
    if result["confidence_tier"] == "LOW":
        return None

    return result


# ── PRIMARY SCANNER ───────────────────────────────────────────────────────────

def scan_all_fno_realtime() -> list[dict]:
    """
    Scan ALL NSE F&O stocks for high-confidence signals.

    Uses: /api/live-analysis-oi-spurts-underlyings (confirmed working from Singapore)
    This single endpoint returns OI + price data for ALL ~200 F&O underlyings.
    We classify all 4 signal types from the price + OI direction combination.

    Returns only HIGH (≥65) + MEDIUM (≥40) confidence signals, sorted by confidence.
    """
    rows = fetch_all_fno_oi_change()

    if not rows:
        logger.info("No data returned (market closed or NSE temporarily unavailable)")
        return []

    results:  list[dict] = []
    seen:     set[str]   = set()

    for row in rows:
        result = _parse_row(row)
        if result is None:
            continue
        if result["symbol"] in seen:
            continue
        seen.add(result["symbol"])
        results.append(result)

    # Sort: HIGH first → higher confidence → higher strength
    results.sort(key=lambda r: (
        0 if r["confidence_tier"] == "HIGH" else 1,
        -r["confidence"],
        -r["strength"],
    ))

    high   = sum(1 for r in results if r["confidence_tier"] == "HIGH")
    medium = sum(1 for r in results if r["confidence_tier"] == "MEDIUM")
    logger.info(
        f"Scan complete: {len(rows)} F&O stocks checked → "
        f"{len(results)} signals ({high} HIGH ⭐⭐⭐, {medium} MEDIUM ⭐⭐)"
    )
    return results


# ── Dummy parse for /api/category route (back-compat) ────────────────────────

def _parse_buildup_row(row: dict, signal: str) -> dict | None:
    """Parse a raw row and force a specific signal type (used by /api/category)."""
    sym = _symbol(row)
    if not sym:
        return None
    ltp         = _f(row.get("ltp") or row.get("lastPrice") or 0)
    price_chg_p = _f(row.get("pChange") or row.get("perChange") or 0)
    price_chg   = _f(row.get("change") or 0)
    oi          = _f(row.get("oi") or row.get("openInterest") or 0)
    oi_chg      = _f(row.get("oiChange") or row.get("changeinOpenInterest") or 0)
    oi_chg_p    = _f(row.get("oiChangePct") or row.get("perOIchange") or 0)
    if ltp == 0:
        return None
    return _build_signal_row(sym, ltp, price_chg, price_chg_p, oi, oi_chg, oi_chg_p, signal)


# ── OPTION CHAIN ──────────────────────────────────────────────────────────────

def get_option_chain_analysis(symbol: str) -> dict:
    is_index = symbol in INDICES
    raw = fetch_option_chain_index(symbol) if is_index else fetch_option_chain_equity(symbol)
    if not raw:
        return {"symbol": symbol, "error": "NSE returned no data (market closed or IP restricted)"}
    return _parse_option_chain(raw, symbol)


def _parse_option_chain(data: dict, symbol: str) -> dict:
    try:
        records    = data.get("records", {}) or {}
        filtered   = data.get("filtered", {}) or {}
        exp_dates  = records.get("expiryDates", [])
        atm_strike = _f(records.get("underlyingValue", 0))
        # NSE v3 returns the selected expiry under records.data and may omit
        # filtered.data; retain compatibility with the legacy response shape.
        all_data   = filtered.get("data") or records.get("data", [])

        total_ce = total_pe = 0
        strikes: list[dict] = []
        pain_map: dict[float, float] = {}

        for row in all_data:
            strike = _f(row.get("strikePrice", 0))
            ce     = row.get("CE") or {}
            pe     = row.get("PE") or {}
            ce_oi  = _f(ce.get("openInterest",          0))
            pe_oi  = _f(pe.get("openInterest",          0))
            ce_doi = _f(ce.get("changeinOpenInterest",  0))
            pe_doi = _f(pe.get("changeinOpenInterest",  0))
            ce_ltp = _f(ce.get("lastPrice",             0))
            pe_ltp = _f(pe.get("lastPrice",             0))
            total_ce += ce_oi
            total_pe += pe_oi
            pain_map[strike] = (pain_map.get(strike, 0)
                                + ce_oi * ce_ltp + pe_oi * pe_ltp)
            strikes.append({
                "strike": strike,
                "ce_oi": int(ce_oi), "ce_doi": int(ce_doi), "ce_ltp": ce_ltp,
                "pe_oi": int(pe_oi), "pe_doi": int(pe_doi), "pe_ltp": pe_ltp,
            })

        pcr      = round(total_pe / total_ce, 2) if total_ce > 0 else 0
        max_pain = min(pain_map, key=pain_map.get) if pain_map else 0

        # Filter ATM ± STRIKES_EACH_SIDE
        atm_list = sorted({s["strike"] for s in strikes})
        atm_idx  = (min(range(len(atm_list)), key=lambda i: abs(atm_list[i] - atm_strike))
                    if atm_list else 0)
        lo = max(0, atm_idx - STRIKES_EACH_SIDE)
        hi = min(len(atm_list) - 1, atm_idx + STRIKES_EACH_SIDE)
        relevant = {atm_list[i] for i in range(lo, hi + 1)}
        strikes_f = [s for s in strikes if s["strike"] in relevant]

        return {
            "symbol": symbol, "atm_strike": atm_strike,
            "expiry": exp_dates[0] if exp_dates else None,
            "pcr": pcr,
            "total_ce_oi": int(total_ce),
            "total_pe_oi": int(total_pe),
            "max_pain": max_pain,
            "strikes": strikes_f,
        }
    except Exception as exc:
        logger.error(f"Option chain parse error for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}
