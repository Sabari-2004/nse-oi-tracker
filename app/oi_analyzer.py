# oi_analyzer.py — Dynamic real-time OI signal scanner (no fixed watchlist)

from __future__ import annotations
import logging
from typing import Optional
from app.config import (
    PRICE_CHANGE_THRESHOLD,
    OI_CHANGE_THRESHOLD,
    MIN_OI_ABSOLUTE,
    MIN_STRENGTH_SCORE,
    STRIKES_EACH_SIDE,
    INDICES,
)
from app.nse_fetcher import (
    fetch_all_fno_oi_change,
    fetch_fno_price_data,
    fetch_oi_buildup,
    fetch_option_chain_index,
    fetch_option_chain_equity,
    fetch_quote_derivative,
)

logger = logging.getLogger(__name__)

# ─── Signal types ─────────────────────────────────────────────────────────────
SIGNAL_LONG_BUILDUP   = "LONG_BUILDUP"
SIGNAL_SHORT_BUILDUP  = "SHORT_BUILDUP"
SIGNAL_SHORT_COVERING = "SHORT_COVERING"
SIGNAL_LONG_UNWINDING = "LONG_UNWINDING"
SIGNAL_NEUTRAL        = "NEUTRAL"

SIGNAL_META = {
    SIGNAL_LONG_BUILDUP:   {"label": "Long Buildup",   "color": "green",  "emoji": "🟢", "bias": "Bullish"},
    SIGNAL_SHORT_BUILDUP:  {"label": "Short Buildup",  "color": "red",    "emoji": "🔴", "bias": "Bearish"},
    SIGNAL_SHORT_COVERING: {"label": "Short Covering", "color": "yellow", "emoji": "🟡", "bias": "Bullish Fade"},
    SIGNAL_LONG_UNWINDING: {"label": "Long Unwinding", "color": "orange", "emoji": "🟠", "bias": "Bearish Fade"},
    SIGNAL_NEUTRAL:        {"label": "Neutral",        "color": "gray",   "emoji": "⚪", "bias": "Sideways"},
}


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


def signal_strength(price_change_pct: float, oi_change_pct: float) -> float:
    p = min(abs(price_change_pct) / 2.0, 50)
    o = min(abs(oi_change_pct)    / 5.0, 50)
    return round(p + o, 1)


def _build_result(symbol: str, ltp: float, price_chg: float, price_chg_p: float,
                  oi: int, oi_chg: int, oi_chg_p: float,
                  series: str = "FUTSTK") -> dict:
    signal   = classify_signal(price_chg_p, oi_chg_p)
    strength = signal_strength(price_chg_p, oi_chg_p)
    meta     = SIGNAL_META[signal]
    return {
        "symbol":           symbol,
        "series":           series,
        "ltp":              round(ltp, 2),
        "price_change":     round(price_chg, 2),
        "price_change_pct": round(price_chg_p, 2),
        "oi":               int(oi),
        "oi_change":        int(oi_chg),
        "oi_change_pct":    round(oi_chg_p, 2),
        "signal":           signal,
        "signal_label":     meta["label"],
        "signal_color":     meta["color"],
        "signal_emoji":     meta["emoji"],
        "signal_bias":      meta["bias"],
        "strength":         strength,
    }


# ─── PRIMARY SCANNER ──────────────────────────────────────────────────────────

def scan_all_fno_realtime() -> list[dict]:
    """
    Dynamically scan ALL F&O stocks in real-time.

    Strategy:
      1. Fetch the NSE OI spurts endpoint → all F&O underlyings with OI change data
      2. Fetch NSE Securities-in-F&O price index → live LTP + price change for all
      3. Merge on symbol → compute signal for each
      4. Filter by signal strength threshold + min OI filter
      5. Sort by strength descending

    No hardcoded watchlist. Only stocks ACTUALLY moving in OI & price appear.
    """
    results: list[dict] = []

    # ── Step 1: Get OI change data for all F&O underlyings ───────────────────
    oi_data_raw = fetch_all_fno_oi_change()
    oi_map: dict[str, dict] = {}

    if oi_data_raw:
        # Response structure: {"data": [...], "timestamp": ...}
        for row in oi_data_raw.get("data", []):
            sym = (
                row.get("underlying") or
                row.get("symbol") or
                row.get("UNDERLYING") or ""
            ).upper().strip()
            if not sym:
                continue
            oi_map[sym] = {
                "oi":        _safe_float(row.get("oi") or row.get("openInterest") or row.get("OI") or 0),
                "oi_chg":    _safe_float(row.get("oiChange") or row.get("changeinOpenInterest") or row.get("COI") or 0),
                "oi_chg_p":  _safe_float(row.get("oiChangePct") or row.get("perOIchange") or row.get("poichange") or 0),
                "ltp":       _safe_float(row.get("ltp") or row.get("lastPrice") or row.get("LTP") or 0),
                "price_chg": _safe_float(row.get("change") or row.get("priceChange") or 0),
                "price_chg_p": _safe_float(row.get("pChange") or row.get("perchange") or row.get("percChange") or 0),
            }

    # ── Step 2: Get live price data for all F&O stocks ───────────────────────
    price_data_raw = fetch_fno_price_data()
    price_map: dict[str, dict] = {}

    if price_data_raw:
        for row in price_data_raw.get("data", []):
            sym = (row.get("symbol") or "").upper().strip()
            if not sym:
                continue
            price_map[sym] = {
                "ltp":         _safe_float(row.get("lastPrice") or row.get("ltp") or 0),
                "price_chg":   _safe_float(row.get("change") or 0),
                "price_chg_p": _safe_float(row.get("pChange") or 0),
            }

    # ── Step 3: Merge all symbols from both sources ───────────────────────────
    all_symbols = set(oi_map.keys()) | set(price_map.keys())

    for sym in all_symbols:
        oi_row    = oi_map.get(sym, {})
        price_row = price_map.get(sym, {})

        # Prefer OI-endpoint LTP if available, fallback to price endpoint
        ltp       = oi_row.get("ltp") or price_row.get("ltp") or 0
        price_chg = oi_row.get("price_chg") or price_row.get("price_chg") or 0
        price_chg_p = oi_row.get("price_chg_p") or price_row.get("price_chg_p") or 0

        oi      = oi_row.get("oi", 0)
        oi_chg  = oi_row.get("oi_chg", 0)
        oi_chg_p = oi_row.get("oi_chg_p", 0)

        # Filter: skip if no OI data or OI too small
        if oi < MIN_OI_ABSOLUTE and oi != 0:
            continue
        # Skip if no meaningful data
        if ltp == 0:
            continue

        result = _build_result(sym, ltp, price_chg, price_chg_p, oi, oi_chg, oi_chg_p)

        # Only include non-neutral OR strong neutral (reduce noise)
        if result["signal"] != SIGNAL_NEUTRAL and result["strength"] >= MIN_STRENGTH_SCORE:
            results.append(result)

    # ── Step 4: Sort by strength ──────────────────────────────────────────────
    results.sort(key=lambda x: x["strength"], reverse=True)
    return results


# ─── BUILDUP CATEGORY SCANNER ─────────────────────────────────────────────────

def scan_by_category(category: str) -> list[dict]:
    """
    Use NSE's pre-computed buildup category endpoints for fast, accurate results.

    category: 'long' | 'short' | 'short_covering' | 'long_unwinding'
    """
    raw = fetch_oi_buildup(category)
    results = []
    if not raw:
        return results

    signal_map = {
        "long":           SIGNAL_LONG_BUILDUP,
        "short":          SIGNAL_SHORT_BUILDUP,
        "short_covering": SIGNAL_SHORT_COVERING,
        "long_unwinding": SIGNAL_LONG_UNWINDING,
    }
    signal = signal_map.get(category, SIGNAL_NEUTRAL)
    meta   = SIGNAL_META[signal]

    for row in raw.get("data", []):
        sym = (
            row.get("underlying") or
            row.get("symbol") or
            row.get("UNDERLYING") or ""
        ).upper().strip()
        if not sym:
            continue

        ltp         = _safe_float(row.get("ltp") or row.get("lastPrice") or 0)
        price_chg_p = _safe_float(row.get("pChange") or row.get("perchange") or 0)
        oi          = _safe_float(row.get("oi") or row.get("openInterest") or 0)
        oi_chg      = _safe_float(row.get("oiChange") or row.get("changeinOpenInterest") or 0)
        oi_chg_p    = _safe_float(row.get("oiChangePct") or row.get("perOIchange") or 0)
        strength    = signal_strength(price_chg_p, oi_chg_p)

        results.append({
            "symbol":           sym,
            "ltp":              round(ltp, 2),
            "price_change":     0,
            "price_change_pct": round(price_chg_p, 2),
            "oi":               int(oi),
            "oi_change":        int(oi_chg),
            "oi_change_pct":    round(oi_chg_p, 2),
            "signal":           signal,
            "signal_label":     meta["label"],
            "signal_color":     meta["color"],
            "signal_emoji":     meta["emoji"],
            "signal_bias":      meta["bias"],
            "strength":         strength,
        })

    results.sort(key=lambda x: x["strength"], reverse=True)
    return results


# ─── SINGLE SYMBOL FUTURES SIGNAL ────────────────────────────────────────────

def get_futures_signal(symbol: str) -> dict | None:
    """Fetch futures OI + price for a specific symbol."""
    data = fetch_quote_derivative(symbol)
    if not data:
        return None
    try:
        stocks = data.get("stocks", [])
        fut = None
        for s in stocks:
            itype = s.get("metadata", {}).get("instrumentType", "")
            ident = s.get("metadata", {}).get("identifier", "")
            if "Futures" in itype or "FUT" in ident:
                fut = s
                break
        if not fut and stocks:
            fut = stocks[0]

        meta      = fut.get("metadata", {}) if fut else {}
        ltp       = _safe_float(meta.get("lastPrice", 0))
        price_chg = _safe_float(meta.get("change", 0))
        price_chg_p = _safe_float(meta.get("pChange", 0))
        oi        = _safe_float(meta.get("openInterest", 0))
        oi_chg    = _safe_float(meta.get("changeinOpenInterest", 0))
        oi_chg_p  = ((oi_chg / (oi - oi_chg)) * 100) if (oi - oi_chg) > 0 else 0

        return _build_result(symbol, ltp, price_chg, price_chg_p, int(oi), int(oi_chg), oi_chg_p)
    except Exception as e:
        logger.error(f"Error getting futures signal for {symbol}: {e}")
        return None


# ─── OPTION CHAIN ─────────────────────────────────────────────────────────────

def get_option_chain_analysis(symbol: str) -> dict:
    is_index = symbol in INDICES
    raw = fetch_option_chain_index(symbol) if is_index else fetch_option_chain_equity(symbol)
    if not raw:
        return {"symbol": symbol, "error": "Failed to fetch NSE data"}
    return _parse_option_chain(raw, symbol)


def _parse_option_chain(data: dict, symbol: str) -> dict:
    try:
        records  = data.get("records", {})
        filtered = data.get("filtered", {})
        exp_dates = records.get("expiryDates", [])
        atm_strike = _safe_float(records.get("underlyingValue", 0))
        nearest_exp = exp_dates[0] if exp_dates else None
        all_data = filtered.get("data", [])

        total_ce_oi = total_pe_oi = 0
        strikes = []
        pain_map: dict[float, float] = {}

        for row in all_data:
            strike = _safe_float(row.get("strikePrice", 0))
            ce = row.get("CE", {}) or {}
            pe = row.get("PE", {}) or {}

            ce_oi  = _safe_float(ce.get("openInterest", 0))
            pe_oi  = _safe_float(pe.get("openInterest", 0))
            ce_doi = _safe_float(ce.get("changeinOpenInterest", 0))
            pe_doi = _safe_float(pe.get("changeinOpenInterest", 0))
            ce_ltp = _safe_float(ce.get("lastPrice", 0))
            pe_ltp = _safe_float(pe.get("lastPrice", 0))

            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            pain_map[strike] = pain_map.get(strike, 0) + ce_oi * ce_ltp + pe_oi * pe_ltp
            strikes.append({"strike": strike, "ce_oi": int(ce_oi), "ce_doi": int(ce_doi),
                             "ce_ltp": ce_ltp, "pe_oi": int(pe_oi), "pe_doi": int(pe_doi), "pe_ltp": pe_ltp})

        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
        max_pain = min(pain_map, key=pain_map.get) if pain_map else 0

        # ATM ± STRIKES_EACH_SIDE
        atm_list = sorted(set(s["strike"] for s in strikes))
        atm_idx = min(range(len(atm_list)), key=lambda i: abs(atm_list[i] - atm_strike)) if atm_list else 0
        lo = max(0, atm_idx - STRIKES_EACH_SIDE)
        hi = min(len(atm_list) - 1, atm_idx + STRIKES_EACH_SIDE)
        relevant = {atm_list[i] for i in range(lo, hi + 1)}
        strikes_filtered = [s for s in strikes if s["strike"] in relevant]

        return {
            "symbol": symbol, "atm_strike": atm_strike, "expiry": nearest_exp,
            "pcr": pcr, "total_ce_oi": int(total_ce_oi), "total_pe_oi": int(total_pe_oi),
            "max_pain": max_pain, "strikes": strikes_filtered,
        }
    except Exception as e:
        logger.error(f"Error parsing chain for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


# ─── Utility ──────────────────────────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0
