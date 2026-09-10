"""Conservative daily-context bull/bear-trap risk labels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def trap_risk(candidate: Mapping[str, Any], technical: Mapping[str, Any] | None) -> dict[str, Any]:
    """Identify only fully evidenced *daily-context* fake-break risks.

    The app has no intraday candles, true intraday VWAP, or tick volume.  It
    therefore never calls a price/OI mismatch alone a trap; the report exposes
    which conditions are unavailable or unmet.
    """
    technical = technical or {}
    price = _number(candidate.get("ltp"))
    oi_change = _number(candidate.get("oi_change_pct"))
    resistance = _number(technical.get("daily_resistance20"))
    support = _number(technical.get("daily_support20"))
    vwap_proxy = _number(technical.get("daily_vwap_proxy20"))
    relative_volume = _number(technical.get("relative_volume20"))
    missing = []
    if price is None or oi_change is None:
        missing.append("current price/OI change")
    if resistance is None or support is None:
        missing.append("20 prior daily bars")
    if vwap_proxy is None:
        missing.append("daily VWAP proxy")
    if relative_volume is None:
        missing.append("daily relative volume")
    if missing:
        return {
            "risk_label": "INSUFFICIENT_DATA",
            "evidence": [], "missing_data": missing,
            "trade_recommendation": "NO_TRADE",
            "caveat": "Trap detection requires daily context and is not an intraday VWAP/volume signal.",
        }

    bullish_break = price > resistance
    bearish_break = price < support
    bull_evidence = [
        "price above prior 20-day resistance" if bullish_break else None,
        "OI not confirming upside" if oi_change <= 0 else None,
        "price below daily VWAP proxy after breakout" if bullish_break and price < vwap_proxy else None,
        "daily volume fading" if relative_volume < 0.8 else None,
    ]
    bear_evidence = [
        "price below prior 20-day support" if bearish_break else None,
        "OI not confirming downside" if oi_change <= 0 else None,
        "price above daily VWAP proxy after breakdown" if bearish_break and price > vwap_proxy else None,
        "daily volume fading" if relative_volume < 0.8 else None,
    ]
    bull_evidence = [item for item in bull_evidence if item]
    bear_evidence = [item for item in bear_evidence if item]
    if len(bull_evidence) >= 3:
        label, evidence = "BULL_TRAP_RISK", bull_evidence
    elif len(bear_evidence) >= 3:
        label, evidence = "BEAR_TRAP_RISK", bear_evidence
    else:
        label, evidence = "NO_TRAP_CONFIRMATION", []
    return {
        "risk_label": label,
        "evidence": evidence,
        "missing_data": [],
        "trade_recommendation": "NO_TRADE",
        "caveat": "Uses daily bars and a daily VWAP proxy only; it cannot validate an intraday bull or bear trap.",
    }
